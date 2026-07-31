package app.onvo.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Teal        = Color(0xFF2FE3C0)
val TealDeep    = Color(0xFF12BFA0)
val Neutral     = Color(0xFF4A5C58)
val BgDark      = Color(0xFF080D0C)
val SurfDark    = Color(0xFF0E1614)
val Surf2Dark   = Color(0xFF141F1C)
val Surf3Dark   = Color(0xFF1B2926)
val OnDark      = Color(0xFFE6F0ED)
val DimDark     = Color(0xFF7E938E)
val OutlineDark = Color(0xFF233330)
val WarnColor   = Color(0xFFF5C86B)
val ErrColor    = Color(0xFFFF8A80)

private val DarkScheme = darkColorScheme(
    primary = Teal,
    onPrimary = Color(0xFF042A25),
    secondary = TealDeep,
    background = BgDark,
    onBackground = OnDark,
    surface = SurfDark,
    onSurface = OnDark,
    surfaceVariant = Surf2Dark,
    onSurfaceVariant = DimDark,
    outline = OutlineDark,
    error = ErrColor
)

private val LightScheme = lightColorScheme(
    primary = TealDeep,
    onPrimary = Color.White,
    background = Color(0xFFF3F8F7),
    onBackground = Color(0xFF08201C),
    surface = Color.White,
    onSurface = Color(0xFF08201C),
    surfaceVariant = Color(0xFFE6F0EE),
    onSurfaceVariant = Color(0xFF5A706B),
    outline = Color(0xFFCBDEDA)
)

@Composable
fun OnvoTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (dark) DarkScheme else LightScheme,
        content = content
    )
}
