package app.onvo.vpn

import android.content.Context
import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader

/**
 * The DPI-evasion layer: ByeDPI running as a local SOCKS5 proxy.
 *
 * This is the root-free rebuild of what SNI-Spoofing did on Windows. It cannot
 * inject raw packets — Android denies CAP_NET_RAW to normal apps and there is no
 * way around that — but it reaches the same outcome on an ordinary TCP socket:
 *
 *   1. setsockopt(IP_TTL, low) so the decoy dies before the real server
 *   2. vmsplice(SPLICE_F_GIFT) hands the fake ClientHello pages to the kernel
 *   3. the same pages are overwritten in place with the genuine payload
 *   4. the kernel's retransmit carries the real data on the same sequence
 *
 * The DPI box records the fake SNI and waves the flow through; the origin server
 * never sees the decoy. TCP_MD5SIG adds a second layer: an invalid signature the
 * server discards and the middlebox ignores.
 *
 * Modes map to increasingly aggressive strategies. AUTO starts conservative and
 * the AI router escalates only when a probe fails, because heavier desync costs
 * latency and is more fingerprintable.
 */
class DesyncEngine(private val ctx: Context) {

    enum class Strategy(val args: List<String>) {
        /** Split the ClientHello at the SNI boundary. Cheapest, often enough. */
        SPLIT(listOf("-s1")),

        /** Split plus out-of-order delivery, for reassembling middleboxes. */
        DISORDER(listOf("-d1")),

        /** Fake ClientHello with a decoy SNI at low TTL — closest to SNI-Spoofing. */
        FAKE(listOf("-f-1", "-t", "4")),

        /** Fake + MD5 signature so the origin drops the decoy for a second reason. */
        FAKE_MD5(listOf("-f-1", "-t", "4", "-M")),

        /** Everything at once, for the harshest networks. */
        AGGRESSIVE(listOf("-f-1", "-t", "4", "-M", "-d1", "-s1", "-An", "-Ar"))
    }

    data class Config(
        val strategy: Strategy = Strategy.FAKE,
        val fakeSni: String = DEFAULT_FAKE_SNI,
        val listenPort: Int = 1080,
        /**
         * Chain into the tunnel core instead of going direct.
         *
         * ciadpi has no `-P` upstream flag — `-P` is "protect path", a Linux
         * routing helper. Upstream proxying is spelled `-C socks5://host:port`
         * (connect-to with a scheme), which makes every outbound connection
         * ride the Aether SOCKS listener on 1819.
         */
        val upstreamSocksPort: Int? = null
    )

    private var process: Process? = null
    private var readerJob: Job? = null

    val binary: File
        get() = File(ctx.applicationInfo.nativeLibraryDir, BINARY_NAME)

    val isAvailable: Boolean get() = binary.exists() && binary.canExecute()
    val isRunning: Boolean get() = process?.isAlive == true

    fun start(cfg: Config, scope: CoroutineScope): Boolean {
        if (!isAvailable) {
            LogBus.log(LogLevel.WARN, "desync binary missing, continuing without it")
            return false
        }
        stop()

        val argv = buildList {
            add(binary.absolutePath)
            add("-i"); add("127.0.0.1")
            add("-p"); add(cfg.listenPort.toString())
            add("-n"); add(cfg.fakeSni)          // the decoy name the DPI will log
            addAll(cfg.strategy.args)
            cfg.upstreamSocksPort?.let { add("-C"); add("socks5://127.0.0.1:$it") }
        }

        LogBus.log(
            LogLevel.INFO,
            "desync start strategy=${cfg.strategy.name.lowercase()} sni=${cfg.fakeSni} port=${cfg.listenPort}"
        )

        process = try {
            ProcessBuilder(argv)
                .directory(ctx.cacheDir)
                .redirectErrorStream(true)
                .start()
        } catch (e: Exception) {
            LogBus.log(LogLevel.ERROR, "desync exec failed: ${e.javaClass.simpleName}")
            return false
        }

        readerJob = scope.launch(Dispatchers.IO) {
            runCatching {
                BufferedReader(InputStreamReader(process!!.inputStream)).use { r ->
                    while (true) {
                        val l = r.readLine() ?: break
                        if (l.isNotBlank()) LogBus.log(LogLevel.DEBUG, "desync: ${l.take(200)}")
                    }
                }
            }
        }
        return true
    }

    fun stop() {
        readerJob?.cancel(); readerJob = null
        process?.let { p ->
            runCatching { p.destroy() }
            runCatching {
                if (!p.waitFor(2, java.util.concurrent.TimeUnit.SECONDS)) p.destroyForcibly()
            }
        }
        process = null
    }

    companion object {
        const val BINARY_NAME = "libonvodesync.so"

        /**
         * A name that is plausible, TLS-terminated and almost never blocked.
         * Same choice the SNI-Spoofing project shipped in its config.json.
         */
        const val DEFAULT_FAKE_SNI = "auth.vercel.com"

        val SNI_CANDIDATES = listOf(
            "auth.vercel.com",
            "www.speedtest.net",
            "cdn.jsdelivr.net",
            "www.cloudflare.com"
        )
    }
}
