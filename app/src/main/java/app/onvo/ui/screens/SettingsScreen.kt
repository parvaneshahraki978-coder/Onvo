package app.onvo.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.onvo.ui.theme.OnvoIcons
import app.onvo.R
import app.onvo.core.ConnectionController
import app.onvo.core.DeviceQuirks
import app.onvo.core.LocaleHelper
import app.onvo.core.SessionHygiene
import app.onvo.vpn.SniScanner
import kotlinx.coroutines.launch
import app.onvo.ui.theme.Teal
import app.onvo.ui.theme.WarnColor

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    currentLang: String = LocaleHelper.FA,
    onLanguageChange: (String) -> Unit = {},
    controller: ConnectionController? = null
) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val lang = currentLang
    var killSwitch by rememberSaveable { mutableStateOf(false) }
    var freshRoute by rememberSaveable { mutableStateOf(true) }
    var sniAuto by rememberSaveable { mutableStateOf(true) }
    var scanning by rememberSaveable { mutableStateOf(false) }
    var scannedName by rememberSaveable { mutableStateOf("") }
    var cleared by rememberSaveable { mutableStateOf(false) }
    var aiLocal by rememberSaveable { mutableStateOf(true) }
    var aiCloud by rememberSaveable { mutableStateOf(false) }   // off by default, as agreed
    var aiRetry by rememberSaveable { mutableStateOf(true) }
    var sni by rememberSaveable { mutableStateOf(true) }
    var frag by rememberSaveable { mutableStateOf(true) }
    var notifyOnly by rememberSaveable { mutableStateOf(true) }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.menu_advanced), fontSize = 16.sp) },
                navigationIcon = { IconButton(onBack) { Icon(OnvoIcons.Back, null) } },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent)
            )
        }
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().verticalScroll(rememberScrollState())) {

            Section(stringResource(R.string.set_general)) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 15.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(stringResource(R.string.set_language), fontSize = 13.5.sp,
                        modifier = Modifier.weight(1f))
                    listOf(LocaleHelper.FA to R.string.lang_fa, LocaleHelper.EN to R.string.lang_en)
                        .forEach { (code, label) ->
                            val sel = lang == code
                            Surface(
                                onClick = { if (!sel) onLanguageChange(code) },
                                shape = RoundedCornerShape(20.dp),
                                color = if (sel) Teal else Color.Transparent,
                                contentColor = if (sel) Color(0xFF042A25)
                                    else MaterialTheme.colorScheme.onSurfaceVariant,
                                border = if (sel) null else androidx.compose.foundation.BorderStroke(
                                    1.dp, MaterialTheme.colorScheme.outline),
                                modifier = Modifier.padding(start = 6.dp)
                            ) {
                                Text(stringResource(label),
                                    Modifier.padding(horizontal = 14.dp, vertical = 6.dp),
                                    fontSize = 12.sp,
                                    fontWeight = if (sel) FontWeight.SemiBold else FontWeight.Normal)
                            }
                        }
                }
            }

            if (DeviceQuirks.needsAutostartHelp) {
                Section(stringResource(R.string.set_device)) {
                    Column(Modifier.padding(horizontal = 15.dp, vertical = 13.dp)) {
                        Text(stringResource(R.string.quirk_title), fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold)
                        Text(stringResource(R.string.quirk_body), fontSize = 11.5.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            lineHeight = 19.sp, modifier = Modifier.padding(top = 5.dp))
                    }
                    Divider()
                    ActionRow(stringResource(R.string.quirk_autostart),
                        stringResource(R.string.quirk_autostart_sub)) {
                        DeviceQuirks.openAutostartSettings(ctx)
                    }
                    Divider()
                    ActionRow(stringResource(R.string.quirk_battery),
                        stringResource(R.string.quirk_battery_sub)) {
                        DeviceQuirks.requestIgnoreBatteryOptimizations(ctx)
                    }
                    if (DeviceQuirks.isXiaomi) {
                        Divider()
                        ActionRow(stringResource(R.string.quirk_other),
                            stringResource(R.string.quirk_other_sub)) {
                            DeviceQuirks.openMiuiPermissionEditor(ctx)
                        }
                    }
                    Divider()
                    ActionRow(stringResource(R.string.quirk_alwayson),
                        stringResource(R.string.quirk_alwayson_sub)) {
                        DeviceQuirks.openVpnSettings(ctx)
                    }
                }
            }

            Section(stringResource(R.string.set_killswitch)) {
                ToggleRow(
                    stringResource(R.string.set_killswitch),
                    stringResource(R.string.set_killswitch_sub),
                    killSwitch
                ) {
                    killSwitch = it
                    controller?.killSwitch = it
                }
                Divider()
                ToggleRow(
                    stringResource(R.string.set_fresh),
                    stringResource(R.string.set_fresh_sub),
                    freshRoute
                ) { freshRoute = it }
                Divider()
                ActionRow(
                    stringResource(if (cleared) R.string.cleared else R.string.set_clear_now),
                    stringResource(R.string.set_clear_sub)
                ) {
                    SessionHygiene.clearEverything(ctx)
                    controller?.invalidateSniCache()
                    cleared = true
                }
            }

            Section(stringResource(R.string.sni_mode)) {
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 15.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(stringResource(R.string.sni_mode), fontSize = 13.5.sp)
                        Text(
                            stringResource(R.string.sni_auto_sub),
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            lineHeight = 17.sp,
                            modifier = Modifier.padding(top = 3.dp)
                        )
                    }
                }
                Row(
                    Modifier.fillMaxWidth().padding(start = 15.dp, end = 15.dp, bottom = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(7.dp)
                ) {
                    listOf(true to R.string.sni_auto, false to R.string.sni_manual)
                        .forEach { (auto, label) ->
                            val sel = sniAuto == auto
                            Surface(
                                onClick = {
                                    sniAuto = auto
                                    controller?.sniMode = if (auto)
                                        ConnectionController.SniMode.AUTO
                                    else ConnectionController.SniMode.MANUAL
                                },
                                shape = RoundedCornerShape(20.dp),
                                color = if (sel) Teal else Color.Transparent,
                                contentColor = if (sel) Color(0xFF042A25)
                                    else MaterialTheme.colorScheme.onSurfaceVariant,
                                border = if (sel) null
                                    else androidx.compose.foundation.BorderStroke(
                                        1.dp, MaterialTheme.colorScheme.outline)
                            ) {
                                Text(
                                    stringResource(label),
                                    Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
                                    fontSize = 12.sp,
                                    fontWeight = if (sel) FontWeight.SemiBold else FontWeight.Normal
                                )
                            }
                        }
                }
                if (sniAuto) {
                    Divider()
                    ActionRow(
                        stringResource(if (scanning) R.string.sni_scanning else R.string.sni_scan_now),
                        if (scannedName.isNotEmpty())
                            stringResource(R.string.sni_active, scannedName) else null
                    ) {
                        if (!scanning && controller != null) {
                            scanning = true
                            controller.invalidateSniCache()
                            scope.launch {
                                val r = SniScanner().scan()
                                scannedName = r.best
                                scanning = false
                            }
                        }
                    }
                } else {
                    Divider()
                    ValueRow(stringResource(R.string.set_fakename), "auth.vercel.com")
                }
            }

            Section(stringResource(R.string.set_ai)) {
                ToggleRow(stringResource(R.string.set_ai_local),
                    stringResource(R.string.set_ai_local_sub), aiLocal) { aiLocal = it }
                Divider()
                ToggleRow(stringResource(R.string.set_ai_cloud),
                    stringResource(R.string.set_ai_cloud_sub), aiCloud) { aiCloud = it }
                Divider()
                ToggleRow(stringResource(R.string.set_ai_retry),
                    stringResource(R.string.set_ai_retry_sub), aiRetry) { aiRetry = it }
            }

            Section(stringResource(R.string.set_dpi)) {
                ToggleRow(stringResource(R.string.set_sni),
                    stringResource(R.string.set_sni_sub), sni) { sni = it }
                Divider()
                ToggleRow(stringResource(R.string.set_frag), null, frag) { frag = it }
                Divider()
                ValueRow(stringResource(R.string.set_scan), "balanced")
            }

            Section(stringResource(R.string.set_update)) {
                ToggleRow(stringResource(R.string.set_notify_only),
                    stringResource(R.string.set_notify_only_sub), notifyOnly) { notifyOnly = it }
                Divider()
                ValueRow(stringResource(R.string.set_sig), stringResource(R.string.always_on),
                    sub = stringResource(R.string.set_sig_sub))
            }

            Card(
                Modifier.fillMaxWidth().padding(16.dp),
                shape = RoundedCornerShape(17.dp),
                colors = CardDefaults.cardColors(containerColor = WarnColor.copy(alpha = .06f)),
                border = androidx.compose.foundation.BorderStroke(1.dp, WarnColor.copy(alpha = .18f))
            ) {
                Row(Modifier.padding(15.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Icon(OnvoIcons.Warning, null, tint = WarnColor, modifier = Modifier.size(15.dp))
                    Text(stringResource(R.string.update_warning), fontSize = 11.5.sp,
                        color = Color(0xFFE3CB98), lineHeight = 19.sp)
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.padding(horizontal = 16.dp).padding(top = 14.dp)) {
        Text(title, fontSize = 11.5.sp, color = Teal, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(start = 4.dp, bottom = 8.dp))
        Card(shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            content = content)
    }
}

@Composable private fun Divider() = HorizontalDivider(color = Color.White.copy(alpha = .03f))

@Composable
private fun ToggleRow(title: String, sub: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 15.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 13.5.sp)
            sub?.let {
                Text(it, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    lineHeight = 17.sp, modifier = Modifier.padding(top = 3.dp))
            }
        }
        Switch(checked, onChange)
    }
}

@Composable
private fun ActionRow(title: String, sub: String?, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            
            .padding(horizontal = 15.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 13.5.sp)
            sub?.let {
                Text(it, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    lineHeight = 17.sp, modifier = Modifier.padding(top = 3.dp))
            }
        }
        Surface(
            onClick = onClick,
            shape = RoundedCornerShape(10.dp),
            color = Teal.copy(alpha = .12f),
            contentColor = Teal
        ) {
            Text(stringResource(R.string.quirk_open),
                Modifier.padding(horizontal = 14.dp, vertical = 6.dp), fontSize = 11.5.sp)
        }
    }
}

@Composable
private fun ValueRow(title: String, value: String, sub: String? = null) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 15.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 13.5.sp)
            sub?.let {
                Text(it, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 3.dp))
            }
        }
        Text(value, fontSize = 11.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
