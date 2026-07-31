package app.onvo.ui.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.onvo.R
import app.onvo.core.NetworkRadar
import app.onvo.ui.theme.*
import kotlinx.coroutines.delay

/**
 * Live connection quality, refreshed every 30 seconds.
 *
 * A latency figure on its own means nothing to most people, so the number is
 * always paired with what it is actually good for. "42ms" is trivia;
 * "good for video calls" is an answer.
 */
@Composable
fun RadarCard(
    reading: NetworkRadar.Reading,
    modifier: Modifier = Modifier
) {
    // countdown to the next sample, so the card never looks frozen
    var secondsLeft by remember { mutableIntStateOf(30) }
    LaunchedEffect(reading.sampledAt) {
        while (true) {
            val elapsed = (System.currentTimeMillis() - reading.sampledAt) / 1000
            secondsLeft = (30 - elapsed).coerceIn(0, 30).toInt()
            delay(1000)
        }
    }

    val gradeColor = when (reading.grade) {
        NetworkRadar.Grade.EXCELLENT, NetworkRadar.Grade.GOOD -> Teal
        NetworkRadar.Grade.FAIR -> WarnColor
        NetworkRadar.Grade.POOR -> Color(0xFFF0A26B)
        NetworkRadar.Grade.BAD -> ErrColor
        NetworkRadar.Grade.UNKNOWN -> Neutral
    }

    Card(
        modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
    ) {
        Column(Modifier.padding(15.dp)) {

            Row(verticalAlignment = Alignment.CenterVertically) {
                SignalBars(reading.grade, gradeColor)
                Spacer(Modifier.width(11.dp))
                Column(Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text(
                            if (reading.hasData) "${reading.pingMs}" else "—",
                            fontSize = 26.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                            color = gradeColor
                        )
                        Text(
                            "ms",
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(start = 3.dp, bottom = 4.dp)
                        )
                    }
                    Text(
                        stringResource(R.string.ping_label),
                        fontSize = 10.5.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                if (reading.hasData) {
                    Text(
                        stringResource(R.string.next_check, secondsLeft),
                        fontSize = 10.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = .7f)
                    )
                }
            }

            // what this latency is good for, in plain words
            Text(
                stringResource(
                    when (reading.grade) {
                        NetworkRadar.Grade.EXCELLENT -> R.string.grade_excellent
                        NetworkRadar.Grade.GOOD -> R.string.grade_good
                        NetworkRadar.Grade.FAIR -> R.string.grade_fair
                        NetworkRadar.Grade.POOR -> R.string.grade_poor
                        NetworkRadar.Grade.BAD -> R.string.grade_bad
                        NetworkRadar.Grade.UNKNOWN -> R.string.grade_unknown
                    }
                ),
                fontSize = 12.sp,
                lineHeight = 19.sp,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.padding(top = 10.dp)
            )

            if (reading.hasData) {
                Row(
                    Modifier.fillMaxWidth().padding(top = 10.dp),
                    horizontalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    MiniStat(stringResource(R.string.jitter_label), "${reading.jitterMs}ms")
                    MiniStat(stringResource(R.string.loss_label), "${reading.loss}%")
                }
            }

            HorizontalDivider(
                Modifier.padding(vertical = 12.dp),
                color = Color.White.copy(alpha = .04f)
            )

            HostilityRow(reading.hostility)
        }
    }
}

/** Five bars that fill according to grade — readable at a glance. */
@Composable
private fun SignalBars(grade: NetworkRadar.Grade, color: Color) {
    val filled = when (grade) {
        NetworkRadar.Grade.EXCELLENT -> 5
        NetworkRadar.Grade.GOOD -> 4
        NetworkRadar.Grade.FAIR -> 3
        NetworkRadar.Grade.POOR -> 2
        NetworkRadar.Grade.BAD -> 1
        NetworkRadar.Grade.UNKNOWN -> 0
    }
    val progress by animateFloatAsState(filled / 5f, tween(600), label = "bars")

    Canvas(Modifier.size(34.dp)) {
        val bars = 5
        val gap = size.width * 0.14f
        val w = (size.width - gap * (bars - 1)) / bars
        for (i in 0 until bars) {
            val h = size.height * (0.28f + 0.18f * i)
            val on = (i + 1) <= (progress * bars + 0.01f)
            drawLine(
                color = if (on) color else color.copy(alpha = .16f),
                start = Offset(i * (w + gap) + w / 2, size.height),
                end = Offset(i * (w + gap) + w / 2, size.height - h),
                strokeWidth = w,
                cap = StrokeCap.Round
            )
        }
    }
}

@Composable
private fun MiniStat(label: String, value: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            "$label ", fontSize = 10.5.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(
            value, fontSize = 11.5.sp, fontWeight = FontWeight.Medium,
            fontFamily = FontFamily.Monospace
        )
    }
}

/** How hard the network is pushing back. */
@Composable
private fun HostilityRow(h: NetworkRadar.Hostility) {
    val (color, label) = when (h) {
        NetworkRadar.Hostility.OPEN -> Teal to R.string.dpi_open
        NetworkRadar.Hostility.FILTERED -> WarnColor to R.string.dpi_filtered
        NetworkRadar.Hostility.AGGRESSIVE -> Color(0xFFF0A26B) to R.string.dpi_aggressive
        NetworkRadar.Hostility.SEVERE -> ErrColor to R.string.dpi_severe
        NetworkRadar.Hostility.UNKNOWN -> Neutral to R.string.dpi_unknown
    }
    val level = when (h) {
        NetworkRadar.Hostility.OPEN -> 1
        NetworkRadar.Hostility.FILTERED -> 2
        NetworkRadar.Hostility.AGGRESSIVE -> 3
        NetworkRadar.Hostility.SEVERE -> 4
        NetworkRadar.Hostility.UNKNOWN -> 0
    }

    Column {
        Text(
            stringResource(R.string.dpi_title),
            fontSize = 10.5.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Row(
            Modifier.padding(top = 7.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // four segments, filling as the network gets more hostile
            Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                repeat(4) { i ->
                    Box(
                        Modifier
                            .width(22.dp).height(4.dp)
                            .clip(RoundedCornerShape(2.dp))
                            .background(
                                if (i < level) color
                                else MaterialTheme.colorScheme.outline.copy(alpha = .45f)
                            )
                    )
                }
            }
            Spacer(Modifier.width(10.dp))
            Text(
                stringResource(label),
                fontSize = 11.5.sp,
                color = color,
                fontWeight = FontWeight.Medium
            )
        }
    }
}
