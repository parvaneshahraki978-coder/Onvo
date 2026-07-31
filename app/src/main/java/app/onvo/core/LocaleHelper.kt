package app.onvo.core

import android.app.LocaleManager
import android.content.Context
import android.content.res.Configuration
import android.os.Build
import android.os.LocaleList
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import java.util.Locale

/**
 * App language, independent of the phone's system language.
 *
 * Persian is the default even on an English phone, because that is the
 * audience. The user can switch to English from settings.
 *
 * ── Why this file looks the way it does ──────────────────────────────────
 *
 * The obvious implementation — wrap the base context, then call recreate()
 * when the user picks a language — has two visible defects that were both
 * reported:
 *
 *   1. Switching English → Persian left the layout stuck in LTR. Compose does
 *      not read layout direction from the Activity's resources at runtime; it
 *      reads LocalLayoutDirection, which is seeded once from the Configuration
 *      that existed when the composition started. Recreating the Activity
 *      re-seeds it only if the new Configuration is already in place *before*
 *      setContent runs, and with attachBaseContext that ordering is fragile.
 *
 *   2. recreate() tears down and rebuilds the window, so the user sees a black
 *      frame. On a language toggle that flash is jarring and looks like a
 *      crash.
 *
 * Both disappear if the language is treated as ordinary Compose state instead
 * of an Activity lifecycle event. [LocalizedContent] rebuilds a localized
 * Context and overrides LocalLayoutDirection in the same recomposition, so the
 * whole tree flips direction in one frame with no restart and no black flash.
 *
 * attachBaseContext is still overridden so that anything outside Compose —
 * notifications, the VPN session label, toasts from the service — also speaks
 * the chosen language.
 *
 * MIUI note: some builds reset per-app locales after a reboot, so the stored
 * preference is re-applied on every launch rather than trusting the framework
 * to have kept it.
 */
object LocaleHelper {

    const val FA = "fa"
    const val EN = "en"

    private const val PREFS = "onvo_locale"
    private const val KEY = "lang"

    fun current(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, FA) ?: FA

    fun save(context: Context, lang: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY, lang).apply()
        // Also tell the framework, so the system "App languages" screen agrees
        // with us and notifications outside Compose follow along.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            runCatching {
                context.getSystemService(LocaleManager::class.java)
                    ?.applicationLocales = LocaleList.forLanguageTags(lang)
            }
        }
    }

    fun isRtl(lang: String): Boolean = lang == FA

    fun localeOf(lang: String): Locale = Locale(lang)

    /** Wrap a context so resource lookups outside Compose resolve correctly. */
    fun wrap(base: Context): Context = wrap(base, current(base))

    fun wrap(base: Context, lang: String): Context {
        val locale = localeOf(lang)
        Locale.setDefault(locale)
        val cfg = Configuration(base.resources.configuration).apply {
            setLocale(locale)
            setLayoutDirection(locale)
            setLocales(LocaleList(locale))
        }
        return base.createConfigurationContext(cfg)
    }
}

/**
 * Applies [lang] to everything inside [content] without restarting the Activity.
 *
 * Overriding LocalLayoutDirection here is the part that actually fixes RTL:
 * every Compose layout consults it, so providing it alongside the localized
 * Context flips the entire tree in a single recomposition.
 */
@Composable
fun LocalizedContent(lang: String, content: @Composable () -> Unit) {
    val base = LocalContext.current
    val localized = remember(lang) { LocaleHelper.wrap(base, lang) }
    val configuration = remember(lang) { localized.resources.configuration }
    val direction = if (LocaleHelper.isRtl(lang)) LayoutDirection.Rtl else LayoutDirection.Ltr

    CompositionLocalProvider(
        LocalContext provides localized,
        LocalConfiguration provides configuration,
        LocalLayoutDirection provides direction,
        content = content
    )
}
