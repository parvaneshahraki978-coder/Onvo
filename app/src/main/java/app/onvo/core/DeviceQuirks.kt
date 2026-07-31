package app.onvo.core

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings

/**
 * Vendor ROM workarounds, aimed mostly at Xiaomi.
 *
 * MIUI/HyperOS diverge from AOSP in ways that quietly break a VPN app:
 *
 *  1. Autostart is denied by default. A killed VpnService never restarts, so
 *     "always-on" silently stops working after a reboot or a memory purge.
 *  2. MIUI's own battery saver is separate from Android's Doze whitelist.
 *     Being on the Android whitelist is not enough.
 *  3. Background pop-up permission is required for the VPN consent dialog to
 *     appear when the app is not in the foreground.
 *  4. Per-app locales are sometimes reset after reboot.
 *  5. MIUI shows an extra scare dialog for VpnService and can revoke consent
 *     when "MIUI optimization" is on.
 *
 * Everything here is best-effort: every intent is wrapped, because these
 * activities are undocumented and get renamed between MIUI versions. Failing
 * to open a settings screen must never crash the app.
 */
object DeviceQuirks {

    val manufacturer: String by lazy { Build.MANUFACTURER.lowercase() }

    val isXiaomi: Boolean by lazy {
        manufacturer.contains("xiaomi") || manufacturer.contains("redmi") ||
            manufacturer.contains("poco") || !miuiVersion().isNullOrBlank()
    }
    val isHuawei: Boolean by lazy { manufacturer.contains("huawei") || manufacturer.contains("honor") }
    val isOppoRealme: Boolean by lazy {
        manufacturer.contains("oppo") || manufacturer.contains("realme") || manufacturer.contains("oneplus")
    }
    val isVivo: Boolean by lazy { manufacturer.contains("vivo") }
    val isSamsung: Boolean by lazy { manufacturer.contains("samsung") }

    /** True on any ROM known to kill background services aggressively. */
    val needsAutostartHelp: Boolean by lazy {
        isXiaomi || isHuawei || isOppoRealme || isVivo
    }

    private fun miuiVersion(): String? = runCatching {
        @Suppress("PrivateApi")
        val c = Class.forName("android.os.SystemProperties")
        val get = c.getMethod("get", String::class.java)
        (get.invoke(null, "ro.miui.ui.version.name") as? String)?.takeIf { it.isNotBlank() }
    }.getOrNull()

    /** Android's own Doze whitelist — necessary everywhere, sufficient nowhere on MIUI. */
    fun isIgnoringBatteryOptimizations(ctx: Context): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M)
            ctx.getSystemService(PowerManager::class.java)
                ?.isIgnoringBatteryOptimizations(ctx.packageName) ?: true
        else true

    fun requestIgnoreBatteryOptimizations(ctx: Context): Boolean = tryStart(
        ctx,
        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
            .setData(Uri.parse("package:${ctx.packageName}"))
    )

    /**
     * Opens the vendor autostart screen. Each ROM hides it somewhere else and
     * renames it between releases, so we walk a list of known components and
     * stop at the first one that resolves.
     */
    fun openAutostartSettings(ctx: Context): Boolean {
        val candidates = buildList {
            if (isXiaomi) {
                add("com.miui.securitycenter" to "com.miui.permcenter.autostart.AutoStartManagementActivity")
                add("com.miui.securitycenter" to "com.miui.permcenter.autostart.AutoStartDetailActivity")
            }
            if (isHuawei) {
                add("com.huawei.systemmanager" to "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity")
                add("com.huawei.systemmanager" to "com.huawei.systemmanager.optimize.process.ProtectActivity")
            }
            if (isOppoRealme) {
                add("com.coloros.safecenter" to "com.coloros.safecenter.permission.startup.StartupAppListActivity")
                add("com.coloros.safecenter" to "com.coloros.safecenter.startupapp.StartupAppListActivity")
                add("com.oppo.safe" to "com.oppo.safe.permission.startup.StartupAppListActivity")
            }
            if (isVivo) {
                add("com.vivo.permissionmanager" to "com.vivo.permissionmanager.activity.BgStartUpManagerActivity")
                add("com.iqoo.secure" to "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity")
            }
            if (isSamsung) {
                add("com.samsung.android.lool" to "com.samsung.android.sm.ui.battery.BatteryActivity")
            }
        }
        for ((pkg, cls) in candidates) {
            val i = Intent().setClassName(pkg, cls).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (ctx.packageManager.resolveActivity(i, 0) != null && tryStart(ctx, i)) return true
        }
        // fall back to this app's settings page, where the toggles usually live too
        return openAppDetails(ctx)
    }

    /** MIUI keeps "other permissions" (background pop-up, etc.) on its own screen. */
    fun openMiuiPermissionEditor(ctx: Context): Boolean {
        if (!isXiaomi) return false
        val i = Intent("miui.intent.action.APP_PERM_EDITOR")
            .setClassName(
                "com.miui.securitycenter",
                "com.miui.permcenter.permissions.PermissionsEditorActivity"
            )
            .putExtra("extra_pkgname", ctx.packageName)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return tryStart(ctx, i)
    }

    fun openAppDetails(ctx: Context): Boolean = tryStart(
        ctx,
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            .setData(Uri.parse("package:${ctx.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    )

    /** System VPN settings — where always-on VPN lives. */
    fun openVpnSettings(ctx: Context): Boolean = tryStart(
        ctx, Intent("android.net.vpn.SETTINGS").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    ) || tryStart(
        ctx, Intent(Settings.ACTION_VPN_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    )

    private fun tryStart(ctx: Context, intent: Intent): Boolean = try {
        if (intent.flags and Intent.FLAG_ACTIVITY_NEW_TASK == 0)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        ctx.startActivity(intent); true
    } catch (_: ActivityNotFoundException) { false
    } catch (_: SecurityException) { false
    } catch (_: Exception) { false }
}
