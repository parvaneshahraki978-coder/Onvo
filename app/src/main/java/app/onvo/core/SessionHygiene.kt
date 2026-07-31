package app.onvo.core

import android.content.Context
import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import java.io.File

/**
 * Wipes the traces a finished session leaves behind.
 *
 * Two separate reasons, and only one of them is about privacy:
 *
 *  1. Reusing the last known-good gateway is convenient but it makes every
 *     session land on the same exit. If that exit is being watched or has
 *     been flagged, the pattern is the tell, not any single connection.
 *
 *  2. The tunnel core caches a device identity so the upstream provider does
 *     not re-register it constantly. Keeping that identity forever means a
 *     stable handle across sessions.
 *
 * So the default is: clear the route cache on disconnect so the next attempt
 * genuinely re-scans and lands somewhere new, but keep the device identity
 * unless the user explicitly asks for a full reset — throwing it away every
 * time triggers rate limiting and makes the app look broken.
 */
object SessionHygiene {

    /** Files the core writes that pin us to one gateway. */
    private val ROUTE_CACHE = listOf(
        "lastconn",          // Aether's quick-reconnect record
        "lastconn.json",
        "last_gateway",
        "endpoint.cache"
    )

    /** Files that carry the persistent device identity. */
    private val IDENTITY = listOf(
        "wgcf-identity.json",
        "wgcf-profile.conf",
        "account.json"
    )

    private fun coreDir(ctx: Context) = File(ctx.filesDir, "core")

    /**
     * Called after every disconnect.
     *
     * Removing the quick-reconnect record is what forces the next connection
     * to rescan instead of snapping straight back to the same endpoint.
     */
    fun clearRouteCache(ctx: Context) {
        val dir = coreDir(ctx)
        if (!dir.exists()) return

        var removed = 0
        ROUTE_CACHE.forEach { name ->
            val f = File(dir, name)
            if (f.exists() && f.delete()) removed++
        }
        // Anything the core wrote that looks like a cached path
        dir.listFiles()?.forEach { f ->
            val n = f.name.lowercase()
            if (f.isFile && (n.contains("lastconn") || n.contains("cache") || n.endsWith(".tmp"))) {
                if (f.delete()) removed++
            }
        }

        if (removed > 0) {
            LogBus.log(LogLevel.INFO, "hygiene: cleared $removed cached route file(s)")
            LogBus.log(LogLevel.INFO, "hygiene: next connection will pick a fresh endpoint")
        }
    }

    /**
     * Full reset, including the device identity.
     *
     * Only on explicit user action. The upstream provider treats a new
     * identity as a new device and will rate limit an address that churns.
     */
    fun clearEverything(ctx: Context) {
        clearRouteCache(ctx)
        val dir = coreDir(ctx)
        var removed = 0
        IDENTITY.forEach { name ->
            val f = File(dir, name)
            if (f.exists() && f.delete()) removed++
        }
        runCatching { File(ctx.cacheDir, "tun2socks.yml").delete() }
        LogBus.log(LogLevel.WARN, "hygiene: full reset, $removed identity file(s) removed")
    }

    /** Rough size of what is currently cached, for the settings screen. */
    fun cachedBytes(ctx: Context): Long =
        coreDir(ctx).walkTopDown().filter { it.isFile }.sumOf { it.length() }
}
