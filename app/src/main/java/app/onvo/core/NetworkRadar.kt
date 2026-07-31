package app.onvo.core

import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.Socket
import java.net.URL
import kotlin.math.roundToInt
import kotlin.system.measureTimeMillis

/**
 * Continuous health monitoring while connected, plus a censorship probe.
 *
 * Two jobs, deliberately separate:
 *
 *  • Latency, sampled every 30s through the tunnel, translated into what the
 *    connection is actually good for. A number alone means nothing to most
 *    people; "good for video calls" does.
 *
 *  • DPI hostility, measured by seeing which techniques the network still
 *    permits. This is what decides whether the user is on a lightly filtered
 *    network or one that is actively hunting tunnels, and it feeds the
 *    strategy picker so escalation is evidence-driven.
 */
class NetworkRadar(private var socksPort: Int = 1819) {

    /** The controller points this at the port traffic actually rides:
     *  the desync engine (1080) when a strategy chains it, else the core (1819). */
    fun usePort(port: Int) {
        if (port != socksPort) {
            socksPort = port
            stopMonitoring()
        }
    }

    /** What the current latency is realistically good for. */
    enum class Grade(val emoji: String) {
        EXCELLENT("🟢"),   // < 60ms
        GOOD("🟢"),        // < 120ms
        FAIR("🟡"),        // < 250ms
        POOR("🟠"),        // < 500ms
        BAD("🔴"),         // >= 500ms or unreachable
        UNKNOWN("⚪")
    }

    /** How aggressively the network appears to be filtering. */
    enum class Hostility {
        UNKNOWN,
        OPEN,         // plain TLS reaches blocked hosts, nothing to evade
        FILTERED,     // SNI-based blocking, split alone gets through
        AGGRESSIVE,   // needs fake ClientHello or heavier
        SEVERE        // even the tunnel edge is hard to reach
    }

    data class Reading(
        val pingMs: Int = -1,
        val jitterMs: Int = 0,
        val loss: Int = 0,
        val grade: Grade = Grade.UNKNOWN,
        val hostility: Hostility = Hostility.UNKNOWN,
        val udpOk: Boolean? = null,
        val sampledAt: Long = 0L
    ) {
        val hasData: Boolean get() = pingMs >= 0
    }

    private val _reading = MutableStateFlow(Reading())
    val reading: StateFlow<Reading> = _reading.asStateFlow()

    private var pingJob: Job? = null
    private val history = ArrayDeque<Int>()

    private fun proxy() = Proxy(Proxy.Type.SOCKS, InetSocketAddress("127.0.0.1", socksPort))

    // ------------------------------------------------------------ ping loop

    fun startMonitoring(scope: CoroutineScope) {
        stopMonitoring()
        pingJob = scope.launch(Dispatchers.IO) {
            // first sample immediately so the UI is not blank for half a minute
            sample()
            while (isActive) {
                delay(SAMPLE_INTERVAL_MS)
                sample()
            }
        }
    }

    fun stopMonitoring() {
        pingJob?.cancel(); pingJob = null
        history.clear()
        _reading.value = Reading()
    }

    private suspend fun sample() {
        val rtt = measureLatency()
        if (rtt < 0) {
            history.addLast(-1)
        } else {
            history.addLast(rtt)
        }
        while (history.size > HISTORY) history.removeFirst()

        val good = history.filter { it >= 0 }
        val lossPct = if (history.isEmpty()) 0
            else (history.count { it < 0 } * 100 / history.size)

        val avg = if (good.isEmpty()) -1 else good.average().roundToInt()
        val jitter = if (good.size < 2) 0 else {
            val mean = good.average()
            good.sumOf { kotlin.math.abs(it - mean) }.div(good.size).roundToInt()
        }

        _reading.value = _reading.value.copy(
            pingMs = avg,
            jitterMs = jitter,
            loss = lossPct,
            grade = gradeFor(avg, lossPct),
            sampledAt = System.currentTimeMillis()
        )

        LogBus.log(
            LogLevel.DEBUG,
            "radar: rtt=${if (avg < 0) "n/a" else "${avg}ms"} jitter=${jitter}ms loss=${lossPct}%"
        )
    }

    /**
     * Latency of a real request through the tunnel, not an ICMP ping.
     * ICMP is frequently rate-limited or dropped outright, and it would not
     * traverse the SOCKS proxy anyway, so it would measure the wrong path.
     */
    private fun measureLatency(): Int = try {
        var code = 0
        val ms = measureTimeMillis {
            val c = URL(PING_URL).openConnection(proxy()) as HttpURLConnection
            c.connectTimeout = PING_TIMEOUT
            c.readTimeout = PING_TIMEOUT
            c.requestMethod = "HEAD"
            c.setRequestProperty("User-Agent", UA)
            c.useCaches = false
            code = c.responseCode
            c.inputStream?.close()
        }
        if (code in 200..399) ms.toInt() else -1
    } catch (_: Exception) { -1 }

    private fun gradeFor(ms: Int, loss: Int): Grade = when {
        ms < 0 || loss > 40 -> Grade.BAD
        ms < 60 && loss == 0 -> Grade.EXCELLENT
        ms < 120 -> Grade.GOOD
        ms < 250 -> Grade.FAIR
        ms < 500 -> Grade.POOR
        else -> Grade.BAD
    }

    // -------------------------------------------------------- DPI profiling

    /**
     * Works out how hostile the network is by testing progressively harder
     * paths, stopping at the first one that succeeds.
     *
     * Run before connecting, on the direct network. The result narrows the
     * search space for the strategy picker: on an OPEN network there is no
     * point paying the latency cost of aggressive desync, and on a SEVERE one
     * there is no point starting from the cheapest option.
     */
    suspend fun probeHostility(): Hostility = withContext(Dispatchers.IO) {
        LogBus.log(LogLevel.INFO, "radar: profiling network")

        // 1. Can we reach a benign TLS host at all?
        if (!tlsReachable("www.cloudflare.com")) {
            LogBus.log(LogLevel.WARN, "radar: baseline TLS failed, network may be down")
            return@withContext Hostility.SEVERE
        }

        // 2. Does a commonly blocked host resolve and connect untouched?
        val plainOk = CANARIES.count { tlsReachable(it) }
        LogBus.log(LogLevel.DEBUG, "radar: $plainOk/${CANARIES.size} canaries reachable directly")

        val h = when {
            plainOk == CANARIES.size -> Hostility.OPEN
            plainOk > 0 -> Hostility.FILTERED
            // Nothing blocked reachable. Is the Cloudflare edge itself throttled?
            !edgeReachable() -> Hostility.SEVERE
            else -> Hostility.AGGRESSIVE
        }

        _reading.value = _reading.value.copy(hostility = h)
        LogBus.log(LogLevel.INFO, "radar: hostility=${h.name.lowercase()}")
        h
    }

    private fun tlsReachable(host: String): Boolean = try {
        Socket().use { s ->
            s.connect(InetSocketAddress(host, 443), CANARY_TIMEOUT)
            s.isConnected
        }
    } catch (_: IOException) { false } catch (_: Exception) { false }

    /** Is the tunnel's own entry point reachable? */
    private fun edgeReachable(): Boolean = try {
        Socket().use { s ->
            s.connect(InetSocketAddress("162.159.192.1", 443), CANARY_TIMEOUT)
            s.isConnected
        }
    } catch (_: Exception) { false }

    companion object {
        const val SAMPLE_INTERVAL_MS = 30_000L
        private const val HISTORY = 6              // three minutes of context
        private const val PING_TIMEOUT = 8_000
        private const val CANARY_TIMEOUT = 4_000
        private const val PING_URL = "https://www.cloudflare.com/cdn-cgi/trace"
        private const val UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile"

        /** Hosts commonly filtered where this app is used. */
        private val CANARIES = listOf(
            "www.youtube.com",
            "telegram.org",
            "discord.com"
        )
    }
}
