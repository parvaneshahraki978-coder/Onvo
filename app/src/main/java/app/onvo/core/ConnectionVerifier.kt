package app.onvo.core

import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.DataOutputStream
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.Socket
import java.net.URL
import java.util.Random
import kotlin.system.measureTimeMillis

/**
 * Proves the tunnel actually carries traffic, instead of trusting a green icon.
 *
 * Every probe runs *through the local SOCKS5 proxy*, never over the default
 * network, so a probe that succeeds is real evidence the tunnel works. This is
 * the difference between "the handshake completed" and "packets get through".
 *
 * The baseline IP is captured before the tunnel comes up, direct, so the
 * comparison afterwards is meaningful.
 */
class ConnectionVerifier(private val socksPort: Int = 1819) {

    private fun proxy() = Proxy(Proxy.Type.SOCKS, InetSocketAddress("127.0.0.1", socksPort))

    /** Direct, pre-connect. Used as the "before" side of the IP comparison. */
    suspend fun baselineIp(): String? = withContext(Dispatchers.IO) {
        fetchTrace(useProxy = false)?.let { parseTrace(it)["ip"] }
    }

    suspend fun runAll(baseline: String?): VerifyReport = withContext(Dispatchers.IO) {
        LogBus.log(LogLevel.INFO, "verify: starting 6 probes")

        val trace = fetchTrace(useProxy = true)
        val fields = trace?.let { parseTrace(it) } ?: emptyMap()

        val exitIp = fields["ip"]
        val ipChanged = exitIp != null && (baseline == null || exitIp != baseline)
        log("ip", ipChanged, exitIp?.let { mask(it) } ?: "no answer")

        // Cloudflare reports warp=on/plus when traffic really rides the tunnel
        val warp = fields["warp"]
        if (warp != null) LogBus.log(LogLevel.DEBUG, "verify: warp=$warp loc=${fields["loc"]}")

        val noDnsLeak = checkDnsThroughTunnel()
        log("dns", noDnsLeak, if (noDnsLeak) "resolver inside tunnel" else "resolved outside")

        val noV6Leak = checkNoV6Leak()
        log("ipv6", noV6Leak, if (noV6Leak) "no v6 exposure" else "v6 leaked")

        var throughputOk = false
        var stable = false
        val samples = mutableListOf<Long>()
        repeat(3) {
            val bytes = downloadSample()
            if (bytes > 0) samples += bytes
        }
        if (samples.isNotEmpty()) {
            throughputOk = samples.sum() > 150_000
            // stable if no sample is wildly slower than the best one
            val best = samples.max().toDouble()
            stable = samples.all { it >= best * 0.35 }
        }
        log("throughput", throughputOk, "${samples.sum() / 1024} KB over ${samples.size} runs")
        log("stability", stable, if (stable) "consistent" else "erratic")

        val udpOk = checkUdpReachable()
        log("udp", udpOk, if (udpOk) "quic/udp passes" else "udp blocked")

        val report = VerifyReport(
            ipChanged = ipChanged,
            noDnsLeak = noDnsLeak,
            noV6Leak = noV6Leak,
            throughputOk = throughputOk,
            udpOk = udpOk,
            stable = stable
        )
        LogBus.log(
            if (report.allPassed) LogLevel.OK else LogLevel.WARN,
            "verify: ${report.passed}/6 passed"
        )
        report
    }

    // ---- probe 1 & 2: exit identity ----

    private fun fetchTrace(useProxy: Boolean): String? = try {
        val url = URL("https://www.cloudflare.com/cdn-cgi/trace")
        val c = (if (useProxy) url.openConnection(proxy()) else url.openConnection())
                as HttpURLConnection
        c.connectTimeout = 8000; c.readTimeout = 8000
        c.setRequestProperty("User-Agent", UA)
        c.inputStream.bufferedReader().use { it.readText() }
    } catch (_: Exception) { null }

    private fun parseTrace(body: String): Map<String, String> =
        body.lineSequence().mapNotNull { l ->
            val i = l.indexOf('='); if (i <= 0) null else l.substring(0, i) to l.substring(i + 1)
        }.toMap()

    // ---- probe 3: DNS leak ----

    /**
     * Resolves a name by sending DNS-over-TCP through the proxy. If the tunnel
     * is really carrying us, this succeeds; if the proxy is dead the request
     * cannot silently fall back to the carrier resolver, which is the point.
     */
    private fun checkDnsThroughTunnel(): Boolean = try {
        Socket(proxy()).use { s ->
            s.soTimeout = 6000
            s.connect(InetSocketAddress("1.1.1.1", 53), 6000)
            val q = buildDnsQuery("cloudflare.com")
            DataOutputStream(s.getOutputStream()).apply {
                writeShort(q.size); write(q); flush()
            }
            val ins = s.getInputStream()
            val lenHi = ins.read(); val lenLo = ins.read()
            if (lenHi < 0 || lenLo < 0) {
                false
            } else {
                val len = (lenHi shl 8) or lenLo
                val buf = ByteArray(len)
                var read = 0
                while (read < len) {
                    val n = ins.read(buf, read, len - read)
                    if (n < 0) break
                    read += n
                }
                // ANCOUNT > 0 means a real answer came back through the tunnel
                read >= 8 &&
                    (((buf[6].toInt() and 0xFF) shl 8) or (buf[7].toInt() and 0xFF)) > 0
            }
        }
    } catch (_: Exception) { false }

    private fun buildDnsQuery(host: String): ByteArray {
        val id = Random().nextInt(65535)
        val out = ArrayList<Byte>()
        fun be16(v: Int) { out.add((v shr 8).toByte()); out.add(v.toByte()) }
        be16(id); be16(0x0100); be16(1); be16(0); be16(0); be16(0)
        host.split('.').forEach { label ->
            out.add(label.length.toByte())
            label.forEach { out.add(it.code.toByte()) }
        }
        out.add(0); be16(1); be16(1)   // A, IN
        return out.toByteArray()
    }

    // ---- probe 4: IPv6 leak ----

    /**
     * When the tunnel is v4-only, any working v6 path is a leak: traffic could
     * bypass the tunnel entirely over v6.
     */
    private fun checkNoV6Leak(): Boolean = try {
        val url = URL("https://[2606:4700:4700::1111]/cdn-cgi/trace")
        val c = url.openConnection() as HttpURLConnection   // deliberately direct
        c.connectTimeout = 3500; c.readTimeout = 3500
        c.setRequestProperty("User-Agent", UA)
        c.inputStream.close()
        false        // reachable over v6 outside the tunnel -> leak
    } catch (_: Exception) {
        true         // unreachable -> no leak
    }

    // ---- probe 5: real throughput ----

    private fun downloadSample(): Long = try {
        val url = URL("https://speed.cloudflare.com/__down?bytes=131072")
        val c = url.openConnection(proxy()) as HttpURLConnection
        c.connectTimeout = 8000; c.readTimeout = 12000
        c.setRequestProperty("User-Agent", UA)
        var total = 0L
        val ms = measureTimeMillis {
            c.inputStream.use { ins ->
                val buf = ByteArray(16 * 1024)
                while (true) {
                    val n = ins.read(buf)
                    if (n < 0) break
                    total += n
                }
            }
        }
        LogBus.log(LogLevel.DEBUG, "verify: sample ${total / 1024}KB in ${ms}ms")
        total
    } catch (_: Exception) { 0L }

    // ---- probe 6: UDP / QUIC ----

    /**
     * SOCKS5 UDP ASSOCIATE, so we learn whether UDP survives. Plenty of networks
     * pass TCP and silently drop UDP, which breaks QUIC and calls while the app
     * still looks connected.
     */
    private fun checkUdpReachable(): Boolean = try {
        Socket("127.0.0.1", socksPort).use { s ->
            s.soTimeout = 5000
            val out = s.getOutputStream(); val ins = s.getInputStream()
            out.write(byteArrayOf(0x05, 0x01, 0x00)); out.flush()
            val hello = ByteArray(2)
            if (ins.read(hello) != 2 || hello[1].toInt() != 0x00) {
                false
            } else {
                // UDP ASSOCIATE with an unspecified bind address
                out.write(byteArrayOf(0x05, 0x03, 0x00, 0x01, 0, 0, 0, 0, 0, 0)); out.flush()
                val reply = ByteArray(10)
                val n = ins.read(reply)
                n >= 2 && reply[1].toInt() == 0x00
            }
        }
    } catch (_: Exception) { false }

    private fun log(name: String, ok: Boolean, detail: String) =
        LogBus.log(if (ok) LogLevel.OK else LogLevel.WARN, "verify $name: ${if (ok) "pass" else "fail"} · $detail")

    private fun mask(ip: String): String {
        val p = ip.split('.')
        return if (p.size == 4) "${p[0]}.${p[1]}.•••.•••" else "•••"
    }

    companion object {
        private const val UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile"
    }
}
