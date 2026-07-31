package app.onvo.vpn

import android.content.Context
import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader

/**
 * Runs the Aether tunnel binary as a child process and streams its output.
 *
 * Why a process and not JNI: Aether is a standalone Rust binary driven entirely
 * by environment variables that prints structured progress to stdout. Wrapping
 * it as a process means we never fork its source, so any upstream release drops
 * in unchanged. The cost is one extra process, which is cheap next to a tunnel.
 *
 * Why the binary lives in jniLibs: since Android 10, W^X is enforced and the
 * only directory an app may exec from is nativeLibraryDir. Files there must be
 * named lib*.so or the installer skips them, hence libonvocore.so. It also
 * requires extractNativeLibs=true, otherwise the file stays compressed inside
 * the APK and there is nothing on disk to exec.
 */
class NativeCore(private val ctx: Context) {

    /** Progress parsed out of the core's own log lines. */
    sealed interface Event {
        data object Scanning : Event
        data class EndpointFound(val addr: String, val rttMs: Int?) : Event
        data class SocksReady(val port: Int) : Event
        data object DataPlaneValidated : Event
        data class Failed(val reason: String) : Event
        data object Exited : Event
    }

    enum class Protocol(val value: String) { MASQUE("masque"), WIREGUARD("wg"), GOOL("gool") }
    enum class Scan(val value: String) { TURBO("turbo"), BALANCED("balanced"), THOROUGH("thorough"), STEALTH("stealth") }

    data class Config(
        val protocol: Protocol = Protocol.MASQUE,
        val scan: Scan = Scan.BALANCED,
        val socksPort: Int = 1819,
        val ipv4Only: Boolean = true,
        val fragment: Boolean = true,
        val noize: String? = null,
        val quickReconnect: Boolean = true
    )

    private var process: Process? = null
    private var readerJob: Job? = null

    val binary: File
        get() = File(ctx.applicationInfo.nativeLibraryDir, BINARY_NAME)

    val isAvailable: Boolean get() = binary.exists() && binary.canExecute()

    /** Persisted WARP identity. Without this Cloudflare re-registers us every run. */
    private val stateDir: File
        get() = File(ctx.filesDir, "core").apply { mkdirs() }

    fun start(cfg: Config, scope: CoroutineScope, onEvent: (Event) -> Unit) {
        if (!isAvailable) {
            LogBus.log(LogLevel.ERROR, "core binary missing at ${binary.path}")
            onEvent(Event.Failed("core_missing"))
            return
        }
        stop()

        val env = buildEnv(cfg)
        LogBus.log(LogLevel.INFO, "starting core proto=${cfg.protocol.value} scan=${cfg.scan.value}")

        process = try {
            ProcessBuilder(listOf(binary.absolutePath))
                .directory(stateDir)
                .redirectErrorStream(true)
                .also { it.environment().putAll(env) }
                .start()
        } catch (e: Exception) {
            LogBus.log(LogLevel.ERROR, "core exec failed: ${e.javaClass.simpleName} ${e.message}")
            onEvent(Event.Failed("exec_failed"))
            null
        } ?: return

        readerJob = scope.launch(Dispatchers.IO) {
            val p = process ?: return@launch
            try {
                BufferedReader(InputStreamReader(p.inputStream)).use { r ->
                    while (true) {
                        val line = r.readLine() ?: break
                        handleLine(line, onEvent)
                    }
                }
            } catch (_: Exception) {
                // stream closed on stop(); not an error
            }
            val code = runCatching { p.waitFor() }.getOrDefault(-1)
            LogBus.log(
                if (code == 0) LogLevel.INFO else LogLevel.WARN,
                "core exited code=$code"
            )
            withContext(Dispatchers.Main) { onEvent(Event.Exited) }
        }
    }

    fun stop() {
        readerJob?.cancel(); readerJob = null
        process?.let { p ->
            runCatching { p.destroy() }
            // give it a moment to unwind the tunnel cleanly, then force it
            runCatching {
                if (!p.waitFor(2, java.util.concurrent.TimeUnit.SECONDS)) p.destroyForcibly()
            }
        }
        process = null
    }

    val isRunning: Boolean get() = process?.isAlive == true

    private fun buildEnv(cfg: Config): Map<String, String> = buildMap {
        // Aether is configured purely through env vars, which is exactly why we
        // never had to fork it. Names verified against upstream cli.rs.
        put("AETHER_PROTOCOL", cfg.protocol.value)
        put("AETHER_SCAN", cfg.scan.value)
        put("AETHER_SOCKS", "127.0.0.1:${cfg.socksPort}")   // loopback only, it has no auth
        put("AETHER_IP", if (cfg.ipv4Only) "v4" else "both")
        put("AETHER_QUICK_RECONNECT", if (cfg.quickReconnect) "1" else "0")
        if (cfg.fragment) put("AETHER_MASQUE_H2_FRAGMENT", "1")
        cfg.noize?.let { put("AETHER_NOIZE", it) }
        put("HOME", stateDir.absolutePath)
        put("TMPDIR", ctx.cacheDir.absolutePath)
    }

    private suspend fun handleLine(raw: String, onEvent: (Event) -> Unit) {
        val line = raw.trim()
        if (line.isEmpty()) return

        // mirror everything into the diagnostic console
        LogBus.log(levelOf(line), line.take(240))

        val socksPort = SOCKS_RE.find(line)?.groupValues?.get(1)?.toIntOrNull()
        val endpoint = ENDPOINT_RE.find(line)?.groupValues?.get(1)

        val ev: Event? = when {
            socksPort != null -> Event.SocksReady(socksPort)
            DATAPLANE_RE.containsMatchIn(line) -> Event.DataPlaneValidated
            FAIL_RE.containsMatchIn(line) -> Event.Failed(line.take(120))
            endpoint != null -> Event.EndpointFound(
                endpoint, RTT_RE.find(line)?.groupValues?.get(1)?.toIntOrNull()
            )
            SCAN_RE.containsMatchIn(line) -> Event.Scanning
            else -> null
        }
        ev?.let { withContext(Dispatchers.Main) { onEvent(it) } }
    }

    private fun levelOf(l: String) = when {
        l.contains("ERROR", true) || l.contains("failed", true) -> LogLevel.ERROR
        l.contains("WARN", true) -> LogLevel.WARN
        l.contains("DEBUG", true) -> LogLevel.DEBUG
        else -> LogLevel.INFO
    }

    companion object {
        const val BINARY_NAME = "libonvocore.so"

        private val SCAN_RE = Regex("""scan(ning)?\b""", RegexOption.IGNORE_CASE)
        private val SOCKS_RE = Regex("""socks.*?127\.0\.0\.1:(\d+)""", RegexOption.IGNORE_CASE)
        private val DATAPLANE_RE = Regex("""data.?plane|validated""", RegexOption.IGNORE_CASE)
        private val ENDPOINT_RE = Regex("""((?:\d{1,3}\.){3}\d{1,3}(?::\d+)?)""")
        private val RTT_RE = Regex("""rtt[=: ]+(\d+)""", RegexOption.IGNORE_CASE)
        private val FAIL_RE = Regex("""no (working|reachable)|all .* failed|giving up""", RegexOption.IGNORE_CASE)
    }
}
