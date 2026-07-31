package app.onvo.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.onvo.ui.theme.OnvoIcons
import app.onvo.R
import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import app.onvo.ui.theme.*
import kotlinx.coroutines.delay

private val ALLOWED = listOf(
    "ping", "dns", "trace", "leak-test", "bench", "stats", "config show", "export"
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConsoleScreen(onBack: () -> Unit) {
    var technical by rememberSaveable { mutableStateOf(true) }
    var copied by remember { mutableStateOf(false) }
    val clip = LocalClipboardManager.current
    val lines by LogBus.lines.collectAsState()

    LaunchedEffect(copied) { if (copied) { delay(1600); copied = false } }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.menu_console), fontSize = 16.sp) },
                navigationIcon = {
                    IconButton(onBack) { Icon(OnvoIcons.Back, null) }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent)
            )
        }
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState())) {

            // plain-language health header
            Card(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                shape = RoundedCornerShape(20.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
            ) {
                Row(
                    Modifier.padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Box(Modifier.size(40.dp).clip(CircleShape).background(Teal.copy(alpha = .11f)),
                        contentAlignment = Alignment.Center) {
                        Icon(OnvoIcons.Check, null, tint = Teal, modifier = Modifier.size(19.dp))
                    }
                    Column {
                        Text(stringResource(R.string.console_ok), fontSize = 14.5.sp,
                            fontWeight = FontWeight.SemiBold, color = Teal)
                        Text(stringResource(R.string.console_ok_sub), fontSize = 11.5.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }

            // the two-layer switch
            Card(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 11.dp),
                shape = RoundedCornerShape(17.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
            ) {
                Row(
                    Modifier.padding(horizontal = 15.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(stringResource(R.string.show_technical), fontSize = 13.sp)
                        Text(stringResource(R.string.show_technical_sub), fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Switch(technical, { technical = it })
                }
            }

            if (!technical) {
                // plain language events
                Column(Modifier.padding(horizontal = 16.dp)) {
                    PlainRow(true, "اتصال برقرار شد", "از طریق سرور آلمان، با روش امن", "11:58")
                    PlainRow(false, "یک‌بار قطع شد و خودکار وصل شد",
                        "شبکه لحظه‌ای ضعیف شد. کاری لازم نیست", "12:41")
                    PlainRow(true, "آزمایش سلامت انجام شد", "هر ۵ مورد سالم بود", "13:02")
                }
            } else {
                // terminal — always LTR regardless of app direction
                CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
                    Card(
                        Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                        shape = RoundedCornerShape(17.dp),
                        colors = CardDefaults.cardColors(containerColor = Color(0xFF060A09)),
                        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                    ) {
                        Column {
                            Row(
                                Modifier.fillMaxWidth().background(Color(0xFF0C1413))
                                    .padding(horizontal = 12.dp, vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text("LOG · onvo-core", fontSize = 10.5.sp,
                                    fontFamily = FontFamily.Monospace,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.weight(1f))
                                Surface(
                                    onClick = { clip.setText(AnnotatedString(LogBus.redactedDump())); copied = true },
                                    shape = RoundedCornerShape(7.dp),
                                    color = Color.Transparent,
                                    border = androidx.compose.foundation.BorderStroke(
                                        1.dp, if (copied) Teal.copy(alpha = .4f) else MaterialTheme.colorScheme.outline)
                                ) {
                                    Row(Modifier.padding(horizontal = 9.dp, vertical = 4.dp),
                                        verticalAlignment = Alignment.CenterVertically,
                                        horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                                        Icon(OnvoIcons.Copy, null, modifier = Modifier.size(11.dp),
                                            tint = if (copied) Teal else MaterialTheme.colorScheme.onSurfaceVariant)
                                        Text(stringResource(if (copied) R.string.copied else R.string.copy_all),
                                            fontSize = 10.sp,
                                            color = if (copied) Teal else MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                }
                            }
                            LazyColumn(Modifier.heightIn(max = 260.dp).padding(horizontal = 12.dp, vertical = 8.dp)) {
                                items(lines) { l ->
                                    Row(Modifier.horizontalScroll(rememberScrollState())) {
                                        Text(l.time, fontSize = 10.5.sp, fontFamily = FontFamily.Monospace,
                                            color = Color(0xFF3F544F))
                                        Spacer(Modifier.width(8.dp))
                                        Text(l.level.tag.padEnd(4), fontSize = 10.5.sp,
                                            fontFamily = FontFamily.Monospace, color = levelColor(l.level))
                                        Spacer(Modifier.width(8.dp))
                                        Text(l.text, fontSize = 10.5.sp, fontFamily = FontFamily.Monospace,
                                            color = Color(0xFFA9BCB7))
                                    }
                                }
                            }
                        }
                    }
                }
                // whitelisted commands — no shell, each maps to a Kotlin function
                Row(
                    Modifier.fillMaxWidth().padding(16.dp).horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    ALLOWED.forEach { cmd ->
                        Surface(
                            shape = RoundedCornerShape(10.dp),
                            color = MaterialTheme.colorScheme.surfaceVariant,
                            border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                        ) {
                            Text(cmd, Modifier.padding(horizontal = 11.dp, vertical = 6.dp),
                                fontSize = 10.5.sp, fontFamily = FontFamily.Monospace,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun PlainRow(ok: Boolean, title: String, sub: String, time: String) {
    Card(
        Modifier.fillMaxWidth().padding(bottom = 8.dp),
        shape = RoundedCornerShape(17.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
    ) {
        Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(11.dp)) {
            Box(Modifier.size(26.dp).clip(CircleShape)
                .background((if (ok) Teal else WarnColor).copy(alpha = .11f)),
                contentAlignment = Alignment.Center) {
                Icon(if (ok) OnvoIcons.Check else OnvoIcons.Alert, null,
                    tint = if (ok) Teal else WarnColor, modifier = Modifier.size(13.dp))
            }
            Column(Modifier.weight(1f)) {
                Text(title, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                Text(sub, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(time, fontSize = 10.sp, fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = .6f))
        }
    }
}

private fun levelColor(l: LogLevel) = when (l) {
    LogLevel.INFO -> Color(0xFF6FB6D8)
    LogLevel.OK -> Teal
    LogLevel.WARN -> WarnColor
    LogLevel.ERROR -> ErrColor
    LogLevel.DEBUG -> Color(0xFF5E7570)
}
