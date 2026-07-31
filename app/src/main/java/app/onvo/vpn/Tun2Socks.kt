package app.onvo.vpn

import android.os.ParcelFileDescriptor
import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import hev.htproxy.TProxyService
import java.io.File

/**
 * Bridges the tun device to the core's local SOCKS5 proxy.
 *
 * Implementation is hev-socks5-tunnel, shipped as libhevsocks5.so. It is driven
 * by a small YAML file rather than argv, so the config is written to the app's
 * private dir at start.
 *
 * The tun fd is passed by number: the library dups it, so we keep ownership and
 * close it ourselves in the service teardown.
 *
 * JNI contract: the library registers TProxyStartService/TProxyStopService on
 * hev.htproxy.TProxyService (see that class). Calling those methods is the only
 * supported way to talk to it — there are no nativeStart/nativeStop symbols.
 */
object Tun2Socks {

    private const val LIB = "hevsocks5"
    private val proxy = TProxyService()
    private var loaded = false
    private var running = false

    /**
     * Whether the bridge library is present for this ABI.
     *
     * Checked up front so a missing payload is reported once, clearly, instead
     * of surfacing as a generic connection failure on every strategy.
     */
    val isSupported: Boolean by lazy { ensureLoaded() }

    /**
     * Loading is optional at runtime: if the .so was not bundled for this ABI we
     * degrade to a clear log line instead of taking the whole app down with an
     * UnsatisfiedLinkError.
     */
    private fun ensureLoaded(): Boolean {
        if (loaded) return true
        return try {
            System.loadLibrary(LIB)
            loaded = true
            true
        } catch (e: UnsatisfiedLinkError) {
            LogBus.log(LogLevel.ERROR, "tun2socks native lib unavailable: ${e.message}")
            false
        }
    }

    fun start(
        tun: ParcelFileDescriptor,
        configDir: File,
        socksPort: Int,
        mtu: Int = 1420
    ): Boolean {
        if (!ensureLoaded()) return false
        if (running) stop()

        val cfg = File(configDir, "tun2socks.yml")
        cfg.writeText(
            """
            tunnel:
              mtu: $mtu
              ipv4: 10.7.0.2
            socks5:
              address: 127.0.0.1
              port: $socksPort
              udp: udp
            misc:
              task-stack-size: 20480
              log-level: warn
            """.trimIndent()
        )

        return try {
            if (proxy.TProxyStartService(cfg.absolutePath, tun.fd)) {
                running = true
                LogBus.log(LogLevel.OK, "tun2socks up · fd=${tun.fd} mtu=$mtu -> 127.0.0.1:$socksPort")
                true
            } else {
                LogBus.log(LogLevel.ERROR, "tun2socks start returned false (bad config?)")
                false
            }
        } catch (e: Throwable) {
            LogBus.log(LogLevel.ERROR, "tun2socks start failed: ${e.javaClass.simpleName}")
            false
        }
    }

    fun stop() {
        if (!running) return
        runCatching { proxy.TProxyStopService() }
        running = false
        LogBus.log(LogLevel.INFO, "tun2socks stopped")
    }

    val isRunning: Boolean get() = running
}
