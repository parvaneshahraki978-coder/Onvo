package app.onvo.ui.screens

import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.onvo.ui.theme.OnvoIcons
import app.onvo.R
import app.onvo.core.*
import app.onvo.core.NetworkRadar
import app.onvo.ui.components.LiquidPowerButton
import app.onvo.ui.components.RadarCard
import app.onvo.ui.theme.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    state: UiState,
    radar: NetworkRadar.Reading = NetworkRadar.Reading(),
    onToggle: () -> Unit,
    onMode: (Mode) -> Unit,
    onMenu: () -> Unit
) {
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = {},
                navigationIcon = {
                    IconButton(onClick = onMenu) {
                        Icon(OnvoIcons.Menu, contentDescription = "menu")
                    }
                },
                actions = { StatusPill(state.phase); Spacer(Modifier.width(10.dp)) },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Color.Transparent,
                    navigationIconContentColor = MaterialTheme.colorScheme.onBackground
                )
            )
        }
    ) { pad ->
        Column(
            Modifier
                .padding(pad)
                .fillMaxSize()
                .verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            // ---- timer ----
            Text(
                text = formatElapsed(state.elapsedSec),
                fontSize = 33.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace,
                color = if (state.phase == Phase.ON) Teal else MaterialTheme.colorScheme.onSurfaceVariant
            )
            Text(
                text = stringResource(if (state.phase == Phase.ON) R.string.conn_time else R.string.ready),
                fontSize = 12.5.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            LiquidPowerButton(
                phase = state.phase,
                progress = state.progress,
                onClick = onToggle,
                modifier = Modifier.padding(top = 6.dp)
            )

            Text(
                text = when (state.phase) {
                    Phase.ON -> stringResource(R.string.hint_tap_disconnect)
                    Phase.CONNECTING -> state.steps.firstOrNull { it.state == StepState.RUNNING }
                        ?.let { stringResource(it.label) + "…" } ?: ""
                    else -> stringResource(R.string.hint_tap_connect)
                },
                fontSize = 12.5.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 8.dp).height(18.dp)
            )

            // ---- mode chips ----
            Row(
                Modifier.padding(top = 14.dp),
                horizontalArrangement = Arrangement.spacedBy(7.dp)
            ) {
                Mode.entries.forEach { m ->
                    ModeChip(stringResource(m.label), m == state.mode) { onMode(m) }
                }
            }

            Spacer(Modifier.height(16.dp))

            AnimatedContent(
                targetState = state.phase,
                transitionSpec = { fadeIn(tween(400)) togetherWith fadeOut(tween(220)) },
                label = "body"
            ) { phase ->
                when (phase) {
                    Phase.CONNECTING -> StepList(state.steps)
                    Phase.ON -> ConnectedBody(state, radar)
                    Phase.FAILED -> FailureCard(state.failureReason)
                    else -> EmptyState()
                }
            }
            Spacer(Modifier.height(28.dp))
        }
    }
}

@Composable
private fun StatusPill(phase: Phase) {
    val (label, color) = when (phase) {
        Phase.ON -> stringResource(R.string.pill_on) to Teal
        Phase.CONNECTING -> stringResource(R.string.pill_connecting) to MaterialTheme.colorScheme.onSurfaceVariant
        else -> stringResource(R.string.pill_off) to MaterialTheme.colorScheme.onSurfaceVariant
    }
    Row(
        Modifier
            .clip(RoundedCornerShape(20.dp))
            .background(if (phase == Phase.ON) Teal.copy(alpha = .07f) else MaterialTheme.colorScheme.surfaceVariant)
            .border(1.dp, if (phase == Phase.ON) Teal.copy(alpha = .25f) else MaterialTheme.colorScheme.outline,
                RoundedCornerShape(20.dp))
            .padding(horizontal = 13.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(color))
        Text(label, fontSize = 11.5.sp, color = color)
    }
}

@Composable
private fun ModeChip(label: String, selected: Boolean, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(22.dp),
        color = if (selected) Teal else Color.Transparent,
        contentColor = if (selected) Color(0xFF042A25) else MaterialTheme.colorScheme.onSurfaceVariant,
        border = if (selected) null else androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
    ) {
        Text(
            label,
            Modifier.padding(horizontal = 17.dp, vertical = 8.dp),
            fontSize = 12.5.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium
        )
    }
}

@Composable
private fun StepList(steps: List<Step>) {
    Card(
        Modifier.fillMaxWidth().padding(horizontal = 18.dp),
        shape = RoundedCornerShape(22.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
    ) {
        steps.forEachIndexed { i, s ->
            if (i > 0) HorizontalDivider(color = Color.White.copy(alpha = .03f))
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(11.dp)
            ) {
                Box(Modifier.size(18.dp), contentAlignment = Alignment.Center) {
                    when (s.state) {
                        StepState.DONE -> Icon(OnvoIcons.Check, null, tint = Teal, modifier = Modifier.size(15.dp))
                        StepState.RUNNING -> CircularProgressIndicator(
                            Modifier.size(16.dp), strokeWidth = 1.6.dp, color = Teal
                        )
                        else -> Box(
                            Modifier.size(15.dp).clip(CircleShape)
                                .border(1.5.dp, MaterialTheme.colorScheme.outline, CircleShape)
                        )
                    }
                }
                Text(
                    stringResource(s.label),
                    Modifier.weight(1f),
                    fontSize = 13.sp,
                    color = if (s.state == StepState.PENDING)
                        MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = .45f)
                    else MaterialTheme.colorScheme.onSurface
                )
                s.ms?.let {
                    Text("${it}ms", fontSize = 10.sp, fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
private fun ConnectedBody(state: UiState, radar: NetworkRadar.Reading) {
    Column(Modifier.fillMaxWidth()) {
        // throughput
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 18.dp),
            horizontalArrangement = Arrangement.spacedBy(9.dp)
        ) {
            StatCard(Modifier.weight(1f), OnvoIcons.Down, Teal,
                "%.1f".format(state.downMbps), "MB/s", stringResource(R.string.dl))
            StatCard(Modifier.weight(1f), OnvoIcons.Up, WarnColor,
                "%.1f".format(state.upMbps), "MB/s", stringResource(R.string.ul))
        }
        Spacer(Modifier.height(9.dp))
        // server
        Card(
            Modifier.fillMaxWidth().padding(horizontal = 18.dp),
            shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
        ) {
            Row(
                Modifier.padding(horizontal = 15.dp, vertical = 13.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(11.dp)
            ) {
                Text(state.serverFlag, fontSize = 20.sp)
                Column(Modifier.weight(1f)) {
                    Text(state.serverCity, fontSize = 13.5.sp, fontWeight = FontWeight.Medium)
                    Text("${state.protocol} · ${state.pingMs}ms",
                        fontSize = 10.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                    listOf(5, 9, 14).forEach {
                        Box(Modifier.width(3.dp).height(it.dp).clip(RoundedCornerShape(1.dp)).background(Teal))
                    }
                }
            }
        }
        Spacer(Modifier.height(9.dp))
        RadarCard(radar, Modifier.padding(horizontal = 18.dp))
        Spacer(Modifier.height(9.dp))
        VerifyCard(state.verify)
    }
}

@Composable
private fun StatCard(
    modifier: Modifier, icon: androidx.compose.ui.graphics.vector.ImageVector,
    tint: Color, value: String, unit: String, label: String
) {
    Card(modifier, shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Row(
            Modifier.padding(horizontal = 13.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(9.dp)
        ) {
            Box(Modifier.size(31.dp).clip(CircleShape).background(tint.copy(alpha = .11f)),
                contentAlignment = Alignment.Center) {
                Icon(icon, null, tint = tint, modifier = Modifier.size(15.dp))
            }
            Column {
                Row(verticalAlignment = Alignment.Bottom) {
                    Text(value, fontSize = 14.5.sp, fontWeight = FontWeight.Bold)
                    Text(unit, fontSize = 9.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(start = 2.dp, bottom = 1.dp))
                }
                Text(label, fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun VerifyCard(v: VerifyReport) {
    Card(
        Modifier.fillMaxWidth().padding(horizontal = 18.dp),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = Teal.copy(alpha = .055f)),
        border = androidx.compose.foundation.BorderStroke(1.dp, Teal.copy(alpha = .18f))
    ) {
        Column(Modifier.padding(15.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(OnvoIcons.Shield, null, tint = Teal, modifier = Modifier.size(15.dp))
                Text(stringResource(R.string.really_connected),
                    fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold, color = Teal)
            }
            Spacer(Modifier.height(11.dp))
            val items = listOf(
                v.ipChanged to R.string.v_ip, v.noDnsLeak to R.string.v_dns,
                v.noV6Leak to R.string.v_v6, v.throughputOk to R.string.v_thr,
                v.udpOk to R.string.v_udp, v.stable to R.string.v_stable
            )
            items.chunked(2).forEach { row ->
                Row(Modifier.fillMaxWidth().padding(bottom = 5.dp)) {
                    row.forEach { (ok, res) ->
                        Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Icon(OnvoIcons.Check, null,
                                tint = if (ok) Teal else MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(11.dp))
                            Text(stringResource(res), fontSize = 11.5.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
    }
}

/**
 * Failure needs a cause, not a shrug. A missing native payload and a hostile
 * network look identical from the outside, and telling them apart is the
 * difference between "retry later" and "the build is broken".
 */
@Composable
private fun FailureCard(reason: FailureReason) {
    val isBuild = reason == FailureReason.MISSING_NATIVE
    val accent = if (isBuild) ErrColor else WarnColor
    Card(
        Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 6.dp),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = accent.copy(alpha = .055f)),
        border = androidx.compose.foundation.BorderStroke(1.dp, accent.copy(alpha = .20f))
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                Icon(OnvoIcons.Warning, null, tint = accent, modifier = Modifier.size(16.dp))
                Text(
                    stringResource(if (isBuild) R.string.fail_native_title else R.string.fail_title),
                    fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = accent
                )
            }
            Text(
                stringResource(if (isBuild) R.string.fail_native else R.string.fail_all),
                fontSize = 12.sp, lineHeight = 21.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 10.dp)
            )
        }
    }
}

@Composable
private fun EmptyState() {
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 30.dp, vertical = 22.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        // abstract broken path — no mascots
        androidx.compose.foundation.Canvas(Modifier.width(150.dp).height(40.dp)) {
            val y = size.height / 2
            val c = Neutral
            drawLine(c, androidx.compose.ui.geometry.Offset(8f, y),
                androidx.compose.ui.geometry.Offset(size.width * .27f, y), 2.6f, alpha = .5f,
                cap = androidx.compose.ui.graphics.StrokeCap.Round)
            drawLine(c, androidx.compose.ui.geometry.Offset(size.width * .73f, y),
                androidx.compose.ui.geometry.Offset(size.width - 8f, y), 2.6f, alpha = .5f,
                cap = androidx.compose.ui.graphics.StrokeCap.Round)
            var x = size.width * .34f
            while (x < size.width * .68f) {
                drawLine(c, androidx.compose.ui.geometry.Offset(x, y),
                    androidx.compose.ui.geometry.Offset(x + 6f, y), 2.2f, alpha = .26f,
                    cap = androidx.compose.ui.graphics.StrokeCap.Round)
                x += 14f
            }
            drawCircle(c, 3.2f, androidx.compose.ui.geometry.Offset(8f, y), alpha = .55f)
            drawCircle(c, 3.2f, androidx.compose.ui.geometry.Offset(size.width - 8f, y), alpha = .55f)
        }
        Spacer(Modifier.height(12.dp))
        Text(stringResource(R.string.not_protected), fontSize = 13.sp,
            fontWeight = FontWeight.Medium, textAlign = TextAlign.Center)
        Text(stringResource(R.string.not_protected_sub), fontSize = 11.5.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center, modifier = Modifier.padding(top = 5.dp))
    }
}

private fun formatElapsed(s: Long): String =
    "%02d:%02d:%02d".format(s / 3600, (s % 3600) / 60, s % 60)
