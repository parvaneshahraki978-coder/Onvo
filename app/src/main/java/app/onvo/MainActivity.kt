package app.onvo

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import app.onvo.ui.theme.OnvoIcons
import app.onvo.core.ConnectionController
import app.onvo.core.LocaleHelper
import app.onvo.core.LocalizedContent
import app.onvo.core.Phase
import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import app.onvo.ui.screens.ConsoleScreen
import app.onvo.ui.screens.HomeScreen
import app.onvo.ui.screens.SettingsScreen
import app.onvo.ui.theme.OnvoTheme
import app.onvo.vpn.OnvoVpnService
import app.onvo.ui.theme.Teal
import kotlinx.coroutines.launch

private enum class Dest { HOME, CONSOLE, SETTINGS }

class MainActivity : ComponentActivity() {

    private lateinit var controller: ConnectionController

    private var vpn: OnvoVpnService? = null
    private var pendingConnect = false

    private val conn = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, b: IBinder?) {
            vpn = (b as? OnvoVpnService.LocalBinder)?.service
            if (pendingConnect) { pendingConnect = false; controller.toggle() }
        }
        override fun onServiceDisconnected(name: ComponentName?) { vpn = null }
    }

    /** System VPN consent. Must be granted before establish() will work. */
    private val consent = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { res ->
        if (res.resultCode == RESULT_OK) startAndBind(thenConnect = true)
        else LogBus.log(LogLevel.WARN, "user declined vpn consent")
    }

    private fun startAndBind(thenConnect: Boolean) {
        pendingConnect = thenConnect
        val i = Intent(this, OnvoVpnService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(i)
        else startService(i)
        bindService(i, conn, Context.BIND_AUTO_CREATE)
    }

    /** Ask for consent if needed, otherwise go straight through. */
    private fun requestConnect() {
        val intent = VpnService.prepare(this)
        if (intent != null) consent.launch(intent) else startAndBind(thenConnect = true)
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        // The branded animation lives in SplashActivity (Compose-drawn, so it
        // behaves the same on every ROM). MainActivity just starts fresh.
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        controller = ConnectionController(
            ctx = applicationContext,
            scope = lifecycleScope,
            // Traffic rides ciadpi's listener (1080) on desync arms and the
            // core's (1819) on plain arms; the bridge must follow the same hop.
            onTunnelUp = { core, desync -> vpn?.bringUp(desync?.listenPort ?: core.socksPort) ?: false },
            onTunnelDown = { vpn?.bringDown() },
            onKillSwitchChanged = { armed -> vpn?.killSwitchEnabled = armed }
        )

        setContent {
            var lang by rememberSaveable { mutableStateOf(LocaleHelper.current(this)) }

            // Wrapping the tree instead of restarting the Activity is what
            // fixes both reported bugs: the direction flips in the same
            // recomposition, and there is no window teardown to flash black.
            LocalizedContent(lang) {
              OnvoTheme {
                val state by controller.state.collectAsStateWithLifecycle()
                val radar by controller.radarReading.collectAsStateWithLifecycle()
                var dest by rememberSaveable { mutableStateOf(Dest.HOME) }
                val drawer = rememberDrawerState(DrawerValue.Closed)
                val scope = rememberCoroutineScope()

                ModalNavigationDrawer(
                    drawerState = drawer,
                    drawerContent = {
                        OnvoDrawer(
                            connected = state.phase == Phase.ON,
                            protocol = state.protocol.ifEmpty { "—" },
                            ip = state.exitIp.ifEmpty { "—" },
                            city = state.serverCity.ifEmpty { "—" },
                            current = dest,
                            onSelect = { d -> dest = d; scope.launch { drawer.close() } }
                        )
                    }
                ) {
                    when (dest) {
                        Dest.HOME -> HomeScreen(
                            state = state,
                            radar = radar,
                            onToggle = {
                                if (state.phase == Phase.OFF || state.phase == Phase.FAILED) {
                                    if (vpn == null) requestConnect() else controller.toggle()
                                } else controller.toggle()
                            },
                            onMode = controller::setMode,
                            onMenu = { scope.launch { drawer.open() } }
                        )
                        Dest.CONSOLE -> ConsoleScreen { dest = Dest.HOME }
                        Dest.SETTINGS -> SettingsScreen(
                            onBack = { dest = Dest.HOME },
                            currentLang = lang,
                            onLanguageChange = { picked ->
                                LocaleHelper.save(this@MainActivity, picked)
                                lang = picked          // recomposes, no restart
                            },
                            controller = controller
                        )
                    }
                }
              }
            }
        }
    }

    override fun onDestroy() {
        runCatching { unbindService(conn) }
        super.onDestroy()
    }

}

@Composable
private fun OnvoDrawer(
    connected: Boolean,
    protocol: String,
    ip: String,
    city: String,
    current: Dest,
    onSelect: (Dest) -> Unit
) {
    ModalDrawerSheet(
        drawerContainerColor = MaterialTheme.colorScheme.surface,
        modifier = Modifier.width(280.dp)
    ) {
        // live status header
        Box(
            Modifier.fillMaxWidth().background(
                Brush.linearGradient(listOf(Color(0xFF123A33), Color(0xFF0B231F)))
            ).padding(18.dp)
        ) {
            Column {
                Row(verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box(Modifier.size(7.dp).clip(CircleShape)
                        .background(if (connected) Teal else Color(0xFF4A5C58)))
                    Text(
                        if (connected) stringResource(R.string.pill_on) else stringResource(R.string.pill_off),
                        fontSize = 15.5.sp, fontWeight = FontWeight.SemiBold
                    )
                }
                Spacer(Modifier.height(10.dp))
                InfoRow("Protocol", protocol)
                InfoRow("IP", ip)
                InfoRow("Location", city)
            }
        }
        Spacer(Modifier.height(10.dp))

        DrawerItem(OnvoIcons.Home, stringResource(R.string.menu_home),
            current == Dest.HOME) { onSelect(Dest.HOME) }
        DrawerItem(OnvoIcons.Shield, stringResource(R.string.menu_health), false) {}
        DrawerItem(OnvoIcons.Globe, stringResource(R.string.menu_servers), false) {}
        DrawerItem(OnvoIcons.Apps, stringResource(R.string.menu_apps), false) {}

        HorizontalDivider(Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
            color = MaterialTheme.colorScheme.outline)
        Text(stringResource(R.string.grp_advanced), fontSize = 10.5.sp,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 24.dp, top = 4.dp, bottom = 6.dp))

        DrawerItem(OnvoIcons.Settings, stringResource(R.string.menu_advanced),
            current == Dest.SETTINGS) { onSelect(Dest.SETTINGS) }
        DrawerItem(OnvoIcons.Terminal, stringResource(R.string.menu_console),
            current == Dest.CONSOLE) { onSelect(Dest.CONSOLE) }
        DrawerItem(OnvoIcons.Download, stringResource(R.string.menu_update), false, badge = "1") {}

        HorizontalDivider(Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
            color = MaterialTheme.colorScheme.outline)
        DrawerItem(OnvoIcons.Info, stringResource(R.string.menu_about), false) {}
    }
}

@Composable
private fun InfoRow(k: String, v: String) {
    Row(Modifier.fillMaxWidth().padding(top = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween) {
        Text(k, fontSize = 11.5.sp, color = Color(0xFF9FC4BC))
        Text(v, fontSize = 10.5.sp, fontFamily = FontFamily.Monospace, color = Color(0xFFCFE9E3))
    }
}

@Composable
private fun DrawerItem(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    selected: Boolean,
    badge: String? = null,
    onClick: () -> Unit
) {
    NavigationDrawerItem(
        icon = { Icon(icon, null, Modifier.size(19.dp)) },
        label = { Text(label, fontSize = 13.5.sp) },
        badge = badge?.let { {
            Box(Modifier.clip(RoundedCornerShape(9.dp)).background(Teal)
                .padding(horizontal = 7.dp, vertical = 2.dp)) {
                Text(it, fontSize = 10.sp, color = Color(0xFF042A25), fontWeight = FontWeight.Bold)
            }
        } },
        selected = selected,
        onClick = onClick,
        colors = NavigationDrawerItemDefaults.colors(
            selectedContainerColor = Teal.copy(alpha = .09f),
            selectedTextColor = Teal,
            selectedIconColor = Teal
        ),
        modifier = Modifier.padding(horizontal = 10.dp, vertical = 1.dp)
    )
}