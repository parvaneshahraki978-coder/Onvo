package app.onvo.ui.theme

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp

/**
 * Hand-built stroke icons.
 *
 * These replace androidx material-icons-extended, which ships tens of thousands
 * of vectors and dominates both dex size and build memory. Everything here is
 * drawn in the same visual language as the Onvo mark: 2dp round-capped strokes
 * on a 24 grid, so the icon set and the logo feel like one family.
 */
object OnvoIcons {

    private fun stroke(
        name: String,
        build: androidx.compose.ui.graphics.vector.ImageVector.Builder.() -> Unit
    ): ImageVector = ImageVector.Builder(
        name = name, defaultWidth = 24.dp, defaultHeight = 24.dp,
        viewportWidth = 24f, viewportHeight = 24f
    ).apply(build).build()

    private fun ImageVector.Builder.line(
        pathData: androidx.compose.ui.graphics.vector.PathBuilder.() -> Unit
    ) = path(
        stroke = SolidColor(Color.Black),
        strokeLineWidth = 2f,
        strokeLineCap = StrokeCap.Round,
        strokeLineJoin = StrokeJoin.Round,
        pathBuilder = pathData
    )

    private fun ImageVector.Builder.solid(
        pathData: androidx.compose.ui.graphics.vector.PathBuilder.() -> Unit
    ) = path(fill = SolidColor(Color.Black), pathBuilder = pathData)

    val Menu: ImageVector by lazy {
        stroke("Menu") {
            line { moveTo(3f, 6f); lineTo(21f, 6f) }
            line { moveTo(3f, 12f); lineTo(16f, 12f) }
            line { moveTo(3f, 18f); lineTo(21f, 18f) }
        }
    }

    val Back: ImageVector by lazy {
        stroke("Back") { line { moveTo(15f, 18f); lineTo(9f, 12f); lineTo(15f, 6f) } }
    }

    val Check: ImageVector by lazy {
        stroke("Check") { line { moveTo(20f, 6f); lineTo(9f, 17f); lineTo(4f, 12f) } }
    }

    val Down: ImageVector by lazy {
        stroke("Down") {
            line { moveTo(12f, 5f); lineTo(12f, 19f) }
            line { moveTo(6f, 13f); lineTo(12f, 19f); lineTo(18f, 13f) }
        }
    }

    val Up: ImageVector by lazy {
        stroke("Up") {
            line { moveTo(12f, 19f); lineTo(12f, 5f) }
            line { moveTo(6f, 11f); lineTo(12f, 5f); lineTo(18f, 11f) }
        }
    }

    val Shield: ImageVector by lazy {
        stroke("Shield") {
            line {
                moveTo(12f, 22f); curveTo(12f, 22f, 20f, 18f, 20f, 12f)
                lineTo(20f, 5f); lineTo(12f, 2f); lineTo(4f, 5f); lineTo(4f, 12f)
                curveTo(4f, 18f, 12f, 22f, 12f, 22f); close()
            }
        }
    }

    val Home: ImageVector by lazy {
        stroke("Home") {
            line {
                moveTo(3f, 11f); lineTo(12f, 3f); lineTo(21f, 11f)
                lineTo(21f, 20f); lineTo(3f, 20f); close()
            }
        }
    }

    val Globe: ImageVector by lazy {
        stroke("Globe") {
            line {
                moveTo(21f, 12f)
                arcTo(9f, 9f, 0f, true, true, 3f, 12f)
                arcTo(9f, 9f, 0f, true, true, 21f, 12f)
            }
            line { moveTo(3f, 12f); lineTo(21f, 12f) }
            line {
                moveTo(12f, 3f); curveTo(15f, 6.5f, 15f, 17.5f, 12f, 21f)
                curveTo(9f, 17.5f, 9f, 6.5f, 12f, 3f)
            }
        }
    }

    val Apps: ImageVector by lazy {
        stroke("Apps") {
            line { moveTo(4f, 5f); lineTo(10f, 5f); lineTo(10f, 11f); lineTo(4f, 11f); close() }
            line { moveTo(14f, 5f); lineTo(20f, 5f); lineTo(20f, 11f); lineTo(14f, 11f); close() }
            line { moveTo(4f, 14f); lineTo(10f, 14f); lineTo(10f, 20f); lineTo(4f, 20f); close() }
            line { moveTo(14f, 14f); lineTo(20f, 14f); lineTo(20f, 20f); lineTo(14f, 20f); close() }
        }
    }

    val Settings: ImageVector by lazy {
        stroke("Settings") {
            line {
                moveTo(15f, 12f)
                arcTo(3f, 3f, 0f, true, true, 9f, 12f)
                arcTo(3f, 3f, 0f, true, true, 15f, 12f)
            }
            line { moveTo(12f, 2.5f); lineTo(12f, 6f) }
            line { moveTo(12f, 18f); lineTo(12f, 21.5f) }
            line { moveTo(2.5f, 12f); lineTo(6f, 12f) }
            line { moveTo(18f, 12f); lineTo(21.5f, 12f) }
            line { moveTo(5.6f, 5.6f); lineTo(8f, 8f) }
            line { moveTo(16f, 16f); lineTo(18.4f, 18.4f) }
            line { moveTo(18.4f, 5.6f); lineTo(16f, 8f) }
            line { moveTo(8f, 16f); lineTo(5.6f, 18.4f) }
        }
    }

    val Terminal: ImageVector by lazy {
        stroke("Terminal") {
            line { moveTo(4f, 17f); lineTo(10f, 11f); lineTo(4f, 5f) }
            line { moveTo(12f, 19f); lineTo(20f, 19f) }
        }
    }

    val Download: ImageVector by lazy {
        stroke("Download") {
            line { moveTo(12f, 3f); lineTo(12f, 15f) }
            line { moveTo(7f, 10f); lineTo(12f, 15f); lineTo(17f, 10f) }
            line { moveTo(4f, 20f); lineTo(20f, 20f) }
        }
    }

    val Info: ImageVector by lazy {
        stroke("Info") {
            line {
                moveTo(21f, 12f)
                arcTo(9f, 9f, 0f, true, true, 3f, 12f)
                arcTo(9f, 9f, 0f, true, true, 21f, 12f)
            }
            line { moveTo(12f, 16.5f); lineTo(12f, 11f) }
            solid { moveTo(12f, 6.6f); arcToRelative(1.15f, 1.15f, 0f, true, true, 0f, 2.3f); arcToRelative(1.15f, 1.15f, 0f, true, true, 0f, -2.3f) }
        }
    }

    val Copy: ImageVector by lazy {
        stroke("Copy") {
            line { moveTo(9f, 9f); lineTo(20f, 9f); lineTo(20f, 20f); lineTo(9f, 20f); close() }
            line { moveTo(5f, 15f); lineTo(5f, 4f); lineTo(16f, 4f) }
        }
    }

    val Warning: ImageVector by lazy {
        stroke("Warning") {
            line {
                moveTo(12f, 3f); lineTo(22f, 20f); lineTo(2f, 20f); close()
            }
            line { moveTo(12f, 10f); lineTo(12f, 14.5f) }
            solid { moveTo(12f, 16.4f); arcToRelative(1.1f, 1.1f, 0f, true, true, 0f, 2.2f); arcToRelative(1.1f, 1.1f, 0f, true, true, 0f, -2.2f) }
        }
    }

    val Alert: ImageVector by lazy {
        stroke("Alert") {
            line { moveTo(12f, 5f); lineTo(12f, 14f) }
            solid { moveTo(12f, 16.6f); arcToRelative(1.2f, 1.2f, 0f, true, true, 0f, 2.4f); arcToRelative(1.2f, 1.2f, 0f, true, true, 0f, -2.4f) }
        }
    }
}
