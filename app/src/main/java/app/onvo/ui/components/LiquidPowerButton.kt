package app.onvo.ui.components

import androidx.compose.animation.core.*
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.unit.dp
import app.onvo.core.Phase
import app.onvo.ui.theme.Neutral
import app.onvo.ui.theme.Teal
import app.onvo.ui.theme.TealDeep
import kotlin.math.PI
import kotlin.math.sin

/**
 * The single-tap connect control.
 *
 * The power glyph never rotates — it stays perfectly still and only shifts colour.
 * Progress is shown by teal liquid rising from the bottom with two overlapping
 * sine waves travelling in opposite directions, so it reads as fluid rather than
 * as a progress bar. Colour animates from a dead neutral grey-green to teal.
 */
@Composable
fun LiquidPowerButton(
    phase: Phase,
    progress: Float,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    val fill by animateFloatAsState(
        targetValue = progress,
        animationSpec = tween(if (phase == Phase.OFF) 1100 else 600, easing = FastOutSlowInEasing),
        label = "fill"
    )
    val tint by animateColorAsState(
        targetValue = if (phase == Phase.ON) Color(0xFF032420) else Neutral,
        animationSpec = tween(700), label = "tint"
    )
    val ringAlpha by animateFloatAsState(
        targetValue = if (phase == Phase.OFF) 0.10f else 1f,
        animationSpec = tween(900), label = "ringAlpha"
    )

    val t = rememberInfiniteTransition(label = "waves")
    // two waves, different speed and direction
    val w1 by t.animateFloat(
        0f, 1f, infiniteRepeatable(tween(2600, easing = LinearEasing)), label = "w1"
    )
    val w2 by t.animateFloat(
        1f, 0f, infiniteRepeatable(tween(3900, easing = LinearEasing)), label = "w2"
    )
    // organic breathing rings
    val breathe by t.animateFloat(
        0f, 1f,
        infiniteRepeatable(
            tween(if (phase == Phase.CONNECTING) 3200 else 7000, easing = FastOutSlowInEasing),
            RepeatMode.Reverse
        ), label = "breathe"
    )

    Box(
        modifier = modifier.size(250.dp),
        contentAlignment = Alignment.Center
    ) {
        // ---- organic concentric rings ----
        Canvas(Modifier.fillMaxSize()) {
            val c = Offset(size.width / 2, size.height / 2)
            val ringColor = lerp(Neutral, Teal, ringAlpha)
            for (i in 0 until 5) {
                val phase01 = (breathe + i * 0.2f) % 1f
                val base = 0.36f + i * 0.055f
                val s = 0.90f + 0.14f * phase01
                val a = (0.05f + 0.37f * phase01) * ringAlpha
                rotate(degrees = phase01 * 6f + i * 9f, pivot = c) {
                    drawOrganicRing(c, size.minDimension * base * s, ringColor, a)
                }
            }
        }

        // ---- the button itself ----
        Box(
            Modifier
                .size(126.dp)
                .clip(CircleShape)
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null
                ) { onClick() },
            contentAlignment = Alignment.Center
        ) {
            Canvas(Modifier.fillMaxSize()) {
                val r = size.minDimension / 2
                val c = Offset(size.width / 2, size.height / 2)

                // idle body
                drawCircle(Color(0xFF1B2926), radius = r, center = c)

                if (fill > 0.001f) {
                    // clip to the circle and draw the rising liquid
                    clipPath(Path().apply { addOval(androidx.compose.ui.geometry.Rect(c - Offset(r, r), c + Offset(r, r))) }) {
                        val surfaceY = size.height * (1f - fill)
                        val amp = (6f + 4f * (1f - fill)).coerceAtMost(9f)

                        drawWave(surfaceY, amp, w1, 1.0f, Brush.verticalGradient(
                            listOf(Teal, TealDeep), startY = surfaceY, endY = size.height
                        ), 0.95f)
                        drawWave(surfaceY + 5f, amp * 0.7f, w2, 1.35f, SolidColor(Teal), 0.45f)
                    }
                }

                // subtle inner rim
                drawCircle(Color.White.copy(alpha = 0.05f), radius = r - 0.5f, center = c,
                    style = Stroke(width = 1f))
            }

            // ---- static power glyph, never rotates ----
            Canvas(Modifier.size(46.dp)) {
                val sw = size.minDimension * 0.075f
                val cx = size.width / 2
                val inset = sw * 1.2f
                // arc
                drawArc(
                    color = tint,
                    startAngle = -63f,
                    sweepAngle = 306f,
                    useCenter = false,
                    topLeft = Offset(inset, inset + size.height * 0.10f),
                    size = Size(size.width - inset * 2, size.height - inset * 2 - size.height * 0.10f),
                    style = Stroke(width = sw, cap = StrokeCap.Round)
                )
                // vertical stem
                drawLine(
                    color = tint,
                    start = Offset(cx, size.height * 0.06f),
                    end = Offset(cx, size.height * 0.46f),
                    strokeWidth = sw,
                    cap = StrokeCap.Round
                )
            }
        }
    }
}

/** Slightly irregular ring so it reads organic rather than machine-drawn. */
private fun DrawScope.drawOrganicRing(center: Offset, radius: Float, color: Color, alpha: Float) {
    val p = Path()
    val steps = 72
    for (i in 0..steps) {
        val a = (i.toFloat() / steps) * 2f * PI.toFloat()
        // gentle 3-lobe distortion
        val r = radius * (1f + 0.035f * sin(a * 3f) + 0.018f * sin(a * 5f + 1.2f))
        val x = center.x + r * kotlin.math.cos(a.toDouble()).toFloat()
        val y = center.y + r * sin(a.toDouble()).toFloat()
        if (i == 0) p.moveTo(x, y) else p.lineTo(x, y)
    }
    p.close()
    drawPath(p, color, alpha = alpha, style = Stroke(width = 1.2f))
}

/** One travelling sine surface with the body filled beneath it. */
private fun DrawScope.drawWave(
    surfaceY: Float,
    amplitude: Float,
    shift: Float,
    freq: Float,
    brush: Brush,
    alpha: Float
) {
    val p = Path()
    val w = size.width
    p.moveTo(0f, surfaceY)
    var x = 0f
    while (x <= w) {
        val y = surfaceY + amplitude * sin((x / w * freq * 2f * PI + shift * 2f * PI).toFloat())
        p.lineTo(x, y)
        x += 3f
    }
    p.lineTo(w, size.height)
    p.lineTo(0f, size.height)
    p.close()
    drawPath(p, brush, alpha = alpha)
}
