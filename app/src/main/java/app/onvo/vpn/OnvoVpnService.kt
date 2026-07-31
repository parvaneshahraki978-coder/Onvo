package app.onvo.vpn

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.VpnService
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.ParcelFileDescriptor
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import app.onvo.MainActivity
import app.onvo.OnvoApp
import app.onvo.R
import app.onvo.core.DeviceQuirks
import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel

/**
 * Owns the tun device, the core process and the tun2socks bridge.
 *
 * Security constraints that shape this file:
 *  - the core's SOCKS5 has no authentication, so it binds to 127.0.0.1 only and
 *    this app's own uid is excluded from the tunnel, otherwise the core would
 *    try to reach Cloudflare through the tunnel it is itself providing.
 *  - the core binary lives in nativeLibraryDir, the only path on modern Android
 *    that still permits exec, hence the lib*.so naming.
 *  - the WARP identity persists under filesDir/core so Cloudflare does not see a
 *    brand new device on every start and begin rate limiting.
 *
 * Vendor ROM constraints, mostly Xiaomi:
 *  - startForeground must happen within seconds of onStartCommand or MIUI throws
 *    ForegroundServiceDidNotStartInTime, so it is the first statement on every
 *    path including the stop path.
 *  - MIUI kills services it does not consider user-visible, so the notification
 *    is ongoing, low priority, and carries an explicit stop action.
 */
class OnvoVpnService : VpnService() {

    inner class LocalBinder : Binder() { val service: OnvoVpnService get() = this@OnvoVpnService }
    private val binder = LocalBinder()
    override fun onBind(intent: Intent?): IBinder? =
        if (intent?.action == SERVICE_INTERFACE) super.onBind(intent) else binder

    private var tun: ParcelFileDescriptor? = null
    @Volatile private var tunnelUp = false

    /**
     * Kill switch.
     *
     * When armed, losing the tunnel does not fall back to the normal network —
     * the tun device stays up with no usable route, so packets are dropped
     * instead of leaking in the clear. That is the entire point: a VPN that
     * silently reverts to direct traffic on failure is worse than no VPN,
     * because the user still believes they are covered.
     */
    @Volatile var killSwitchEnabled = false
    @Volatile private var blackholed = false

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        promoteToForeground()
        return when (intent?.action) {
            ACTION_STOP -> { bringDown(); stopForegroundCompat(); stopSelf(); START_NOT_STICKY }
            else -> START_STICKY
        }
    }

    private fun promoteToForeground() {
        val n = buildNotification()
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(NOTIF_ID, n)
            }
        } catch (e: Exception) {
            // MIUI throws here if notifications were denied. Survivable.
            LogBus.log(LogLevel.WARN, "foreground promotion failed: ${e.javaClass.simpleName}")
        }
    }

    /**
     * Called by the controller once a strategy has been chosen.
     * Establishes tun and starts the bridge; the core process itself is owned by
     * the controller so it can watch its output and rotate strategies.
     */
    fun bringUp(socksPort: Int): Boolean {
        if (tunnelUp) return true
        LogBus.log(LogLevel.INFO, "establishing tun")

        if (DeviceQuirks.isXiaomi) {
            LogBus.log(LogLevel.INFO, "xiaomi rom detected, compatibility path active")
        }

        val builder = Builder()
            .setSession(getString(R.string.app_name))
            .setMtu(MTU)
            .addAddress("10.7.0.2", 32)
            .addRoute("0.0.0.0", 0)
            .addDnsServer("1.1.1.1")
            .addDnsServer("1.0.0.1")

        // Critical: without this the core's own traffic loops into the tunnel.
        runCatching { builder.addDisallowedApplication(packageName) }
            .onFailure { LogBus.log(LogLevel.ERROR, "could not exclude self from tunnel") }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) builder.setMetered(false)

        tun = try {
            builder.establish()
        } catch (e: IllegalStateException) {
            LogBus.log(LogLevel.ERROR, "establish rejected: ${e.message}"); null
        } catch (e: Exception) {
            LogBus.log(LogLevel.ERROR, "establish failed: ${e.javaClass.simpleName}"); null
        }

        val fd = tun ?: run {
            LogBus.log(LogLevel.ERROR, "no tun, aborting")
            return false
        }
        LogBus.log(LogLevel.OK, "tun established mtu=$MTU")

        if (!Tun2Socks.start(fd, filesDir, socksPort, MTU)) {
            LogBus.log(LogLevel.ERROR, "bridge failed to start")
            closeTun(); return false
        }

        tunnelUp = true
        return true
    }

    fun bringDown() {
        if (!tunnelUp && tun == null) return
        Tun2Socks.stop()
        closeTun()
        tunnelUp = false
        blackholed = false
        LogBus.log(LogLevel.INFO, "tunnel torn down")
    }

    /**
     * Tunnel died unexpectedly. With the kill switch armed we keep the tun
     * interface claimed so traffic has nowhere to go; without it we get out of
     * the way and let the device use its normal connection.
     */
    fun onTunnelLost() {
        if (!killSwitchEnabled) {
            LogBus.log(LogLevel.WARN, "tunnel lost, kill switch off, restoring direct route")
            bringDown()
            return
        }
        Tun2Socks.stop()
        blackholed = true
        LogBus.log(LogLevel.WARN, "tunnel lost, kill switch armed, traffic is being dropped")
        runCatching {
            NotificationManagerCompat.from(this)
                .notify(NOTIF_ID, buildNotification(blocked = true))
        }
    }

    val isBlackholed: Boolean get() = blackholed

    private fun closeTun() {
        runCatching { tun?.close() }
        tun = null
    }

    @Suppress("DEPRECATION")
    private fun stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) stopForeground(Service.STOP_FOREGROUND_REMOVE)
        else stopForeground(true)
    }

    override fun onRevoke() {
        LogBus.log(LogLevel.WARN, "vpn permission revoked by system")
        bringDown(); stopForegroundCompat(); stopSelf()
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        LogBus.log(LogLevel.INFO, "task removed, tunnel stays up")
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() { bringDown(); super.onDestroy() }

    private fun buildNotification(blocked: Boolean = false): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, OnvoVpnService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, OnvoApp.CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(
                if (blocked) R.string.notif_blocked else R.string.notif_connected))
            .setOngoing(true)
            .setShowWhen(false)
            .setContentIntent(open)
            .addAction(0, getString(R.string.notif_stop), stop)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .build()
    }

    companion object {
        const val ACTION_STOP = "app.onvo.STOP"
        private const val NOTIF_ID = 1001
        private const val MTU = 1420
    }
}
