package app.onvo

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.DecelerateEasing
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.onvo.ui.theme.BgDark
import app.onvo.ui.theme.Teal
import app.onvo.ui.theme.TealDeep
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Branded splash, rendered by Compose instead of the platform splash API.
 *
 * Why: the platform SplashScreen animation is an AnimatedVectorDrawable on the
 * splash window. On several ROMs — MIUI notably — that drawable either never
 * animates (shows the last frame) or the window is torn down before it
 * finishes. The prototype's animation (ring draws itself, a wave rises, the
 * dot pops, the wordmark fades in) is deterministic when we own the frame, so
 * this activity replays it with plain Compose state, then hands over to
 * MainActivity. Total ≈ 2.2s, matching the prototype timing.
 */
class SplashActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            SplashScene(onFinished = {
                startActivity(Intent(this, MainActivity::class.java))
                overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
                finish()
            })
        }
    }
}

@Composable
private fun SplashScene(onFinished: () -> Unit) {
    val ring = remember { Animatable(0f) }       // trim-path of the circle
    val waveRise = remember { Animatable(1f) }   // 1 -> 0 (translation)
    val waveAlpha = remember { Animatable(0f) }
    val dotScale = remember { Animatable(0f) }
    val textAlpha = remember { Animatable(0f) }

    LaunchedEffect(Unit) {
        coroutineScope {
            launch {
                ring.animateTo(
                    1f,
                    tween(durationMillis = 1250, easing = FastOutSlowInEasing)
                )
            }
            launch {
                delay(1050)
                waveRise.animateTo(0f, tween(900, easing = DecelerateEasing))
            }
            launch {
                delay(1050)
                waveAlpha.animateTo(1f, tween(300))
            }
            launch {
                delay(1450)
                dotScale.animateTo(
                    1f,
                    spring(
                        dampingRatio = Spring.DampingRatioMediumBouncy,
                        stiffness = Spring.StiffnessLow
                    )
                )
            }
            launch {
                delay(1500)
                textAlpha.animateTo(1f, tween(750, easing = FastOutSlowInEasing))
            }
        }
        delay(420)  // let the last motion settle
        onFinished()
    }

    Box(
        Modifier.fillMaxSize().background(BgDark),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            OnvoMark(
                ring = ring.value,
                waveRise = waveRise.value,
                waveAlpha = waveAlpha.value,
                dotScale = dotScale.value
            )
            Spacer(Modifier.height(26.dp))
            Text(
                text = "Onvo",
                fontSize = 26.sp,
                fontWeight = FontWeight.SemiBold,
                color = Color(0xFFE6F0ED).copy(alpha = textAlpha.value),
                letterSpacing = 2.sp
            )
            Spacer(Modifier.height(6.dp))
            Text(
                text = "اتصال امن",
                fontSize = 12.sp,
                color = Teal.copy(alpha = textAlpha.value * 0.9f)
            )
        }
    }
}

/**
 * The Onvo mark recreated from the prototype's SVG, in a 72-unit grid scaled
 * to [sizeDp]. Ring first (0..1 trim), then the wave rising from the bottom,
 * then the floating dot.
 */
@Composable
private fun OnvoMark(
    ring: Float,
    waveRise: Float,
    waveAlpha: Float,
    dotScale: Float,
    sizeDp: androidx.compose.ui.unit.Dp = 230.dp
) {
    Canvas(Modifier.size(sizeDp)) {
        val s = size.minDimension / 72f
        val strokeW = 2.9f * s
        val waveW = 2.4f * s

        // ring — drawn clockwise from the top, trimmed like stroke-dashoffset
        drawArc(
            color = Teal,
            startAngle = -90f,
            sweepAngle = 360f * ring,
            useCenter = false,
            topLeft = Offset(8.5f * s, 8.5f * s),
            size = androidx.compose.ui.geometry.Size(31f * s, 31f * s),
            style = Stroke(width = strokeW, cap = StrokeCap.Round)
        )

        // wave — the same cubic as the prototype, translated up as it fades in
        val wave = Path().apply {
            moveTo(15.5f * s, 27.5f * s + 14f * s * waveRise)
            cubicTo(
                18f * s, 24f * s + 14f * s * waveRise,
                20.5f * s, 31f * s + 14f * s * waveRise,
                24f * s, 27.5f * s + 14f * s * waveRise
            )
            cubicTo(
                27.5f * s, 24f * s + 14f * s * waveRise,
                30f * s, 31f * s + 14f * s * waveRise,
                32.5f * s, 27.5f * s + 14f * s * waveRise
            )
        }
        drawPath(
            path = wave,
            color = Teal.copy(alpha = waveAlpha),
            style = Stroke(width = waveW, cap = StrokeCap.Round)
        )

        // dot — pops in with overshoot, like the prototype's dotpop
        val dotR = 2.6f * s * dotScale
        if (dotR > 0.05f) {
            drawCircle(
                color = TealDeep,
                radius = dotR,
                center = Offset(24f * s, 16f * s)
            )
        }
    }
}
