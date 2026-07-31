package app.onvo.vpn

import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.net.InetSocketAddress
import java.net.Socket
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import kotlin.system.measureTimeMillis

/**
 * Finds a decoy SNI that this specific network lets through.
 *
 * A fake ClientHello only fools DPI if the name it carries looks legitimate
 * *to that operator*. A domain that sails past one ISP may be on another's
 * blocklist, in which case the decoy makes things worse: it draws attention
 * instead of deflecting it.
 *
 * Rather than shipping one hardcoded guess, the scanner measures. It opens a
 * real TLS handshake to a neutral IP while presenting each candidate name and
 * keeps the ones the network accepts quickly and consistently.
 *
 * What makes a good decoy:
 *   - hosted somewhere too valuable to block wholesale (CDN, cloud auth)
 *   - unremarkable in logs, the sort of name that appears constantly
 *   - fast to handshake, so it does not slow the real connection
 */
class SniScanner {

    data class Candidate(
        val host: String,
        val reachable: Boolean = false,
        val handshakeMs: Int = -1,
        val score: Int = 0
    )

    data class Result(
        val best: String,
        val ranked: List<Candidate>,
        val scannedAt: Long = System.currentTimeMillis()
    )

    /**
     * Probe every candidate in parallel and rank them.
     *
     * [against] is the address the handshake is actually made to. Using the
     * tunnel edge means we measure the exact path the decoy will travel, not
     * some unrelated route.
     */
    suspend fun scan(
        candidates: List<String> = DEFAULT_CANDIDATES,
        against: String = EDGE_IP,
        port: Int = 443
    ): Result = coroutineScope {
        LogBus.log(LogLevel.INFO, "sni scan: testing ${candidates.size} names")

        val results = candidates.map { host ->
            async(Dispatchers.IO) { probe(host, against, port) }
        }.awaitAll()

        val ranked = results.sortedByDescending { it.score }
        val best = ranked.firstOrNull { it.reachable }?.host
            ?: DesyncEngine.DEFAULT_FAKE_SNI

        ranked.take(5).forEach {
            LogBus.log(
                LogLevel.DEBUG,
                "sni scan: ${it.host} ${if (it.reachable) "${it.handshakeMs}ms score=${it.score}" else "unreachable"}"
            )
        }
        LogBus.log(LogLevel.OK, "sni scan: chose $best")

        Result(best, ranked)
    }

    /**
     * One TLS handshake carrying [host] as the SNI, aimed at [ip].
     *
     * The certificate will not match the name — we are talking to an IP that
     * does not serve that host — so verification is expected to fail. We do
     * not care: the question is only whether the network *allowed* the
     * ClientHello carrying this name to reach the far side.
     */
    private suspend fun probe(host: String, ip: String, port: Int): Candidate =
        withContext(Dispatchers.IO) {
            var ms = -1
            val ok = withTimeoutOrNull(TIMEOUT_MS.toLong()) {
                try {
                    Socket().use { raw ->
                        raw.connect(InetSocketAddress(ip, port), TIMEOUT_MS)
                        val ssl = (SSLSocketFactory.getDefault() as SSLSocketFactory)
                            .createSocket(raw, ip, port, false) as SSLSocket

                        ssl.sslParameters = ssl.sslParameters.apply {
                            serverNames = listOf(SNIHostName(host))
                        }
                        ssl.soTimeout = TIMEOUT_MS

                        ms = measureTimeMillis {
                            try {
                                ssl.startHandshake()
                            } catch (e: javax.net.ssl.SSLHandshakeException) {
                                // Certificate mismatch is the expected outcome and
                                // still proves the ClientHello was not dropped.
                                if (e.message?.contains("Connection closed", true) == true ||
                                    e.message?.contains("reset", true) == true
                                ) throw e
                            }
                        }.toInt()
                        true
                    }
                } catch (_: Exception) {
                    false
                }
            } ?: false

            // Prefer fast handshakes; anything sluggish would drag the real
            // connection down every time the decoy is sent.
            val score = when {
                !ok -> 0
                ms < 200 -> 100
                ms < 400 -> 80
                ms < 700 -> 55
                ms < 1200 -> 30
                else -> 10
            }
            Candidate(host, ok, if (ok) ms else -1, score)
        }

    companion object {
        private const val TIMEOUT_MS = 5_000

        /** A Cloudflare edge address, the same family the tunnel uses. */
        private const val EDGE_IP = "162.159.192.1"

        /**
         * Names chosen to be plausible and expensive to block:
         * auth endpoints, CDNs and developer infrastructure that ordinary
         * traffic hits constantly.
         */
        val DEFAULT_CANDIDATES = listOf(
            "auth.vercel.com",
            "cdn.jsdelivr.net",
            "www.cloudflare.com",
            "ajax.googleapis.com",
            "fonts.gstatic.com",
            "cdnjs.cloudflare.com",
            "s3.amazonaws.com",
            "www.speedtest.net",
            "api.github.com",
            "unpkg.com"
        )
    }
}
