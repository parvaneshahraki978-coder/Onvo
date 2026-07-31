package app.onvo.core

import android.content.Context
import android.os.Build
import app.onvo.ai.RouteBrain
import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import app.onvo.vpn.DesyncEngine
import app.onvo.vpn.NativeCore
import app.onvo.vpn.SniScanner
import app.onvo.vpn.Tun2Socks
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Orchestrates a real connection attempt and exposes one UiState to the screen.
 *
 * The flow, and where each of the five visible steps really happens:
 *
 *   1. Checking network   – capture the direct IP baseline for later comparison
 *   2. Finding best route – RouteBrain picks an arm; desync layer comes up
 *   3. Secure tunnel      – the Aether binary runs until it reports SOCKS ready
 *   4. Testing connection – six probes, all forced through the proxy
 *   5. Leak check         – DNS and IPv6 results folded into the report
 *
 * If verification fails, the arm is marked down and the next-best one is tried.
 * That retry loop is the whole point of measuring instead of trusting: a tunnel
 * that connects but does not pass traffic is treated as a failure, not success.
 */
class ConnectionController(
    private val ctx: Context,
    private val scope: CoroutineScope,
    private val onTunnelUp: (NativeCore.Config, DesyncEngine.Config?) -> Boolean = { _, _ -> true },
    private val onTunnelDown: () -> Unit = {},
    private val onKillSwitchChanged: (Boolean) -> Unit = {}
) {
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    private val core = NativeCore(ctx)
    private val desync = DesyncEngine(ctx)
    private val brain = RouteBrain(ctx)
    private val scanner = SniScanner()

    /** Live latency and censorship readings, surfaced on the home screen. */
    val radar = NetworkRadar(SOCKS_PORT)
    val radarReading get() = radar.reading

    /**
     * Decoy SNI selection.
     *
     * AUTO measures which names this network actually lets through, because a
     * decoy that is itself blocked is worse than none. MANUAL honours whatever
     * the user set, for people who already know their operator.
     */
    var sniMode: SniMode = SniMode.AUTO
    var manualSni: String = DesyncEngine.DEFAULT_FAKE_SNI
    private var scannedSni: SniScanner.Result? = null

    private var job: Job? = null
    private var ticker: Job? = null

    /** Set false in settings to stop after the first failed attempt. */
    var autoRetry: Boolean = true

    /**
     * When armed, a dropped tunnel blocks traffic rather than falling back to
     * the open network. Mirrored into the service, which owns the tun device.
     */
    var killSwitch: Boolean = false
        set(value) {
            field = value
            onKillSwitchChanged(value)
            _state.value = _state.value.copy(killSwitch = value)
        }
    var maxAttempts: Int = 3

    fun setMode(m: Mode) { _state.value = _state.value.copy(mode = m) }

    fun toggle() {
        if (_state.value.phase == Phase.ON || _state.value.phase == Phase.CONNECTING) disconnect()
        else connect()
    }

    // ---------------------------------------------------------------- connect

    private fun connect() {
        job?.cancel()
        job = scope.launch {
            _state.value = UiState(phase = Phase.CONNECTING, mode = _state.value.mode)

            // Preflight. If a native component is missing, every strategy will
            // fail for the same reason, so say so once instead of rotating
            // through the whole list and blaming the network.
            val missing = mutableListOf<String>()
            if (!core.isAvailable) missing += "tunnel core (libonvocore.so)"
            if (!Tun2Socks.isSupported) missing += "bridge (libhevsocks5.so)"
            if (missing.isNotEmpty()) {
                LogBus.log(LogLevel.ERROR, "cannot connect, missing: ${missing.joinToString()}")
                LogBus.log(LogLevel.ERROR, "this build has no native payload for ${Build.SUPPORTED_ABIS.firstOrNull()}")
                _state.value = _state.value.copy(
                    phase = Phase.FAILED,
                    failureReason = FailureReason.MISSING_NATIVE
                )
                return@launch
            }

            val tried = mutableSetOf<String>()
            var attempt = 0

            // ---- step 1: baseline + network profile, taken direct ----
            mark(0, StepState.RUNNING)
            val baseline = withTimeoutOrNull(6000) { ConnectionVerifier().baselineIp() }
            LogBus.log(LogLevel.INFO, "baseline ip=${baseline?.let { maskIp(it) } ?: "unknown"}")

            // How hostile is this network? The answer narrows the search: no
            // point paying for heavy desync on an open network, no point
            // starting cheap on one that blocks everything.
            val hostility = withTimeoutOrNull(15_000) { radar.probeHostility() }
                ?: NetworkRadar.Hostility.UNKNOWN
            _state.value = _state.value.copy(hostility = hostility)

            // Pick a decoy name this network actually tolerates.
            val fakeSni = when (sniMode) {
                SniMode.MANUAL -> manualSni
                SniMode.AUTO -> {
                    val cached = scannedSni
                    if (cached != null && System.currentTimeMillis() - cached.scannedAt < SNI_CACHE_MS) {
                        cached.best
                    } else {
                        val r = withTimeoutOrNull(20_000) { scanner.scan() }
                        r?.also { scannedSni = it }?.best ?: DesyncEngine.DEFAULT_FAKE_SNI
                    }
                }
            }
            _state.value = _state.value.copy(activeSni = fakeSni)
            mark(0, StepState.DONE)

            while (attempt < maxAttempts && isActive) {
                attempt++

                // ---- step 2: choose a strategy ----
                mark(1, StepState.RUNNING)
                val arm = brain.choose(
                    userMode = when (_state.value.mode) {
                        Mode.AUTO -> RouteBrain.Mode.AUTO
                        Mode.FAST -> RouteBrain.Mode.FAST
                        Mode.SAFE -> RouteBrain.Mode.SAFE
                    },
                    exclude = tried
                )
                tried += arm.id
                _state.value = _state.value.copy(protocol = arm.label)

                val desyncCfg = arm.desync?.takeIf { desync.isAvailable }?.let {
                    DesyncEngine.Config(
                        strategy = it,
                        fakeSni = fakeSni,
                        listenPort = DESYNC_PORT,
                        // Route every desynced connection through the tunnel
                        // core, so the chain is: tun -> ciadpi(1080) -> core(1819)
                        // -> Cloudflare. Without this ciadpi would go direct and
                        // the whole evasion layer would be a no-op.
                        upstreamSocksPort = SOCKS_PORT
                    )
                }
                val coreCfg = NativeCore.Config(
                    protocol = arm.protocol,
                    scan = arm.scan,
                    socksPort = SOCKS_PORT,
                    fragment = arm.fragment
                )

                // The port traffic actually rides differs per arm: desync arms
                // land on ciadpi's listener, plain arms on the core's. All
                // probes must measure the same path the user's packets take.
                val effectivePort = desyncCfg?.listenPort ?: coreCfg.socksPort
                val verifier = ConnectionVerifier(effectivePort)
                radar.usePort(effectivePort)

                if (desyncCfg != null && desync.isAvailable) desync.start(desyncCfg, scope)
                mark(1, StepState.DONE)

                // ---- step 3: bring the tunnel up ----
                mark(2, StepState.RUNNING)
                val ok = startCore(coreCfg, desyncCfg)
                if (!ok) {
                    mark(2, StepState.FAILED)
                    brain.record(arm, success = false)
                    cleanupAttempt()
                    if (!autoRetry) return@launch fail()
                    LogBus.log(LogLevel.WARN, "attempt $attempt failed to establish, trying next")
                    resetStepsFrom(1)
                    continue
                }
                mark(2, StepState.DONE)

                // ---- step 4 & 5: prove it actually carries traffic ----
                mark(3, StepState.RUNNING)
                val report = verifier.runAll(baseline)
                mark(3, if (report.throughputOk && report.ipChanged) StepState.DONE else StepState.FAILED)

                mark(4, StepState.RUNNING)
                delay(200)
                val leakOk = report.noDnsLeak && report.noV6Leak
                mark(4, if (leakOk) StepState.DONE else StepState.FAILED)

                // A tunnel that connects but moves no traffic is a failure.
                val usable = report.ipChanged && report.throughputOk
                brain.record(arm, success = usable, qualityPassed = report.passed)

                if (usable) {
                    _state.value = _state.value.copy(
                        phase = Phase.ON,
                        verify = report,
                        serverCity = "—",
                        serverFlag = "🌐"
                    )
                    LogBus.log(LogLevel.OK, "connected via ${arm.label} (${report.passed}/6)")
                    radar.startMonitoring(scope)
                    startTicker()
                    return@launch
                }

                LogBus.log(LogLevel.WARN, "verification failed (${report.passed}/6), rotating strategy")
                cleanupAttempt()
                if (!autoRetry) return@launch fail()
                resetStepsFrom(1)
            }

            fail()
        }
    }

    /** Runs the core and waits until it reports a usable SOCKS listener. */
    private suspend fun startCore(
        cfg: NativeCore.Config,
        desyncCfg: DesyncEngine.Config?
    ): Boolean {
        if (!onTunnelUp(cfg, desyncCfg)) return false
        if (!core.isAvailable) {
            LogBus.log(LogLevel.ERROR, "core binary not bundled for this ABI")
            return false
        }

        var ready = false
        var failed = false
        core.start(cfg, scope) { ev ->
            when (ev) {
                is NativeCore.Event.SocksReady -> ready = true
                is NativeCore.Event.Failed -> failed = true
                is NativeCore.Event.Exited -> failed = true
                is NativeCore.Event.EndpointFound ->
                    _state.value = _state.value.copy(pingMs = ev.rttMs ?: 0)
                else -> Unit
            }
        }

        // Aether scans before it settles; thorough modes legitimately take a while
        val budget = when (cfg.scan) {
            NativeCore.Scan.TURBO -> 20_000L
            NativeCore.Scan.BALANCED -> 35_000L
            else -> 60_000L
        }
        val start = System.currentTimeMillis()
        while (System.currentTimeMillis() - start < budget) {
            if (ready) return true
            if (failed) return false
            delay(250)
        }
        LogBus.log(LogLevel.WARN, "core did not report ready within ${budget / 1000}s")
        return false
    }

    // ------------------------------------------------------------- disconnect

    private fun disconnect() {
        job?.cancel(); ticker?.cancel()
        radar.stopMonitoring()
        cleanupAttempt()
        onTunnelDown()

        // Drop the quick-reconnect record so the next attempt genuinely
        // rescans and lands on a different exit instead of pinning us to the
        // same endpoint session after session.
        SessionHygiene.clearRouteCache(ctx)

        LogBus.log(LogLevel.INFO, "tunnel down, direct route restored")
        _state.value = UiState(mode = _state.value.mode)
    }

    private fun cleanupAttempt() {
        core.stop()
        desync.stop()
    }

    private fun fail(reason: FailureReason = FailureReason.ALL_STRATEGIES_FAILED) {
        cleanupAttempt(); onTunnelDown()
        LogBus.log(LogLevel.ERROR, "all strategies exhausted")
        _state.value = _state.value.copy(phase = Phase.FAILED, failureReason = reason)
    }

    // ------------------------------------------------------------------ utils

    private fun startTicker() {
        ticker?.cancel()
        ticker = scope.launch {
            while (isActive) {
                delay(1000)
                _state.value = _state.value.copy(elapsedSec = _state.value.elapsedSec + 1)
            }
        }
    }

    private fun mark(i: Int, s: StepState, ms: Long? = null) {
        val list = _state.value.steps.toMutableList()
        if (i in list.indices) {
            list[i] = list[i].copy(state = s, ms = ms)
            _state.value = _state.value.copy(steps = list)
        }
    }

    private fun resetStepsFrom(i: Int) {
        val list = _state.value.steps.toMutableList()
        for (k in i until list.size) list[k] = list[k].copy(state = StepState.PENDING, ms = null)
        _state.value = _state.value.copy(steps = list)
    }

    private fun maskIp(ip: String): String {
        val p = ip.split('.')
        return if (p.size == 4) "${p[0]}.${p[1]}.•••.•••" else "•••"
    }

    fun brainSummary(): String = brain.summary()
    fun resetBrain() = brain.reset()

    enum class SniMode { AUTO, MANUAL }

    /** Re-scan decoy names periodically; networks change their minds. */
    fun invalidateSniCache() { scannedSni = null }

    fun lastSniScan(): SniScanner.Result? = scannedSni

    companion object {
        const val SOCKS_PORT = 1819
        const val DESYNC_PORT = 1080
        private const val SNI_CACHE_MS = 30 * 60 * 1000L
    }
}
