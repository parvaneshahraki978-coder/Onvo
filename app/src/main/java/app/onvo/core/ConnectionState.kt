package app.onvo.core

import androidx.annotation.StringRes
import app.onvo.R

enum class Phase { OFF, CONNECTING, ON, FAILED }

enum class Mode(@StringRes val label: Int) {
    AUTO(R.string.mode_auto),
    FAST(R.string.mode_fast),
    SAFE(R.string.mode_safe)
}

enum class StepState { PENDING, RUNNING, DONE, FAILED }

/** Why a connection attempt ended, so the UI can say something useful. */
enum class FailureReason {
    NONE,
    /** The APK was built without native binaries for this device's ABI. */
    MISSING_NATIVE,
    /** Every strategy was tried and none carried real traffic. */
    ALL_STRATEGIES_FAILED
}

data class Step(@StringRes val label: Int, val state: StepState = StepState.PENDING, val ms: Long? = null)

/** The five steps shown while connecting. Progress of the liquid fill is derived from these. */
val DEFAULT_STEPS = listOf(
    Step(R.string.step_network),
    Step(R.string.step_route),
    Step(R.string.step_tunnel),
    Step(R.string.step_verify),
    Step(R.string.step_leak)
)

/** Result of the six real verification probes. */
data class VerifyReport(
    val ipChanged: Boolean = false,
    val noDnsLeak: Boolean = false,
    val noV6Leak: Boolean = false,
    val throughputOk: Boolean = false,
    val udpOk: Boolean = false,
    val stable: Boolean = false
) {
    val passed: Int get() = listOf(ipChanged, noDnsLeak, noV6Leak, throughputOk, udpOk, stable).count { it }
    val allPassed: Boolean get() = passed == 6
}

data class UiState(
    val phase: Phase = Phase.OFF,
    val mode: Mode = Mode.AUTO,
    val steps: List<Step> = DEFAULT_STEPS,
    val elapsedSec: Long = 0,
    val downMbps: Double = 0.0,
    val upMbps: Double = 0.0,
    val pingMs: Int = 0,
    val serverCity: String = "",
    val serverFlag: String = "",
    val protocol: String = "",
    val exitIp: String = "",
    val verify: VerifyReport = VerifyReport(),
    val failureReason: FailureReason = FailureReason.NONE,
    val hostility: NetworkRadar.Hostility = NetworkRadar.Hostility.UNKNOWN,
    val activeSni: String = "",
    val killSwitch: Boolean = false,
    val blocked: Boolean = false
) {
    /** 0f..1f — drives the liquid fill height of the power button. */
    val progress: Float
        get() = when (phase) {
            Phase.ON -> 1f
            Phase.OFF -> 0f
            else -> steps.count { it.state == StepState.DONE }.toFloat() / steps.size
        }
}
