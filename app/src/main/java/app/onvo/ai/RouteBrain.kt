package app.onvo.ai

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.telephony.TelephonyManager
import app.onvo.diag.LogBus
import app.onvo.diag.LogLevel
import app.onvo.vpn.DesyncEngine
import app.onvo.vpn.NativeCore
import org.json.JSONObject
import java.util.Calendar
import kotlin.math.ln
import kotlin.math.sqrt
import kotlin.random.Random

/**
 * Picks the connection strategy, and gets better at it over time.
 *
 * This is a contextual multi-armed bandit using Thompson sampling over Beta
 * posteriors. Each arm is a full strategy (tunnel protocol + scan depth +
 * desync mode); the context is the network you are on right now.
 *
 * Why Thompson sampling rather than "remember the last thing that worked":
 * censorship is not static. A strategy that dies today may work next week, so
 * the sampler keeps a little probability mass on every arm and rediscovers them
 * on its own, without ever needing an explicit "try everything" pass.
 *
 * Everything stays on the device. The context key is a coarse bucket — carrier
 * name, wifi vs mobile, and a 6-hour slot — never a location, never an IP, and
 * it is never transmitted anywhere. Cloud assist is a separate, off-by-default
 * setting.
 */
class RouteBrain(private val ctx: Context) {

    data class Arm(
        val protocol: NativeCore.Protocol,
        val scan: NativeCore.Scan,
        val desync: DesyncEngine.Strategy?,
        val fragment: Boolean
    ) {
        val id: String get() =
            "${protocol.value}|${scan.value}|${desync?.name ?: "none"}|${if (fragment) "f" else "n"}"

        val label: String get() = buildString {
            append(protocol.value.uppercase())
            desync?.let { append(" + ").append(it.name.lowercase()) }
        }
    }

    /** Beta(alpha, beta) posterior over "this arm works here". */
    private data class Stats(
        var alpha: Double = 1.0,
        var beta: Double = 1.0,
        var lastRttMs: Int = 0,
        var trials: Int = 0
    )

    private val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    private val table = HashMap<String, HashMap<String, Stats>>()

    init { load() }

    // ---------- context ----------

    /**
     * A coarse fingerprint of the current network. Deliberately low resolution:
     * enough to separate "home wifi at night" from "mobile data at noon",
     * not enough to identify a person.
     */
    fun contextKey(): String {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        val caps = cm?.activeNetwork?.let { cm.getNetworkCapabilities(it) }
        val transport = when {
            caps == null -> "none"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cell"
            else -> "other"
        }
        val carrier = if (transport == "cell") {
            runCatching {
                ctx.getSystemService(TelephonyManager::class.java)
                    ?.simOperator?.takeIf { it.isNotBlank() }
            }.getOrNull() ?: "unk"
        } else "-"
        val slot = Calendar.getInstance().get(Calendar.HOUR_OF_DAY) / 6   // 0..3
        return "$transport:$carrier:$slot"
    }

    // ---------- arms ----------

    private fun arms(): List<Arm> = listOf(
        // fast and quiet first
        Arm(NativeCore.Protocol.MASQUE, NativeCore.Scan.TURBO, null, false),
        Arm(NativeCore.Protocol.MASQUE, NativeCore.Scan.BALANCED, null, true),
        Arm(NativeCore.Protocol.WIREGUARD, NativeCore.Scan.BALANCED, null, false),
        // add desync as the network gets hostile
        Arm(NativeCore.Protocol.MASQUE, NativeCore.Scan.BALANCED, DesyncEngine.Strategy.SPLIT, true),
        Arm(NativeCore.Protocol.MASQUE, NativeCore.Scan.BALANCED, DesyncEngine.Strategy.FAKE, true),
        Arm(NativeCore.Protocol.MASQUE, NativeCore.Scan.THOROUGH, DesyncEngine.Strategy.FAKE_MD5, true),
        // last resort
        Arm(NativeCore.Protocol.GOOL, NativeCore.Scan.THOROUGH, DesyncEngine.Strategy.AGGRESSIVE, true),
        Arm(NativeCore.Protocol.MASQUE, NativeCore.Scan.STEALTH, DesyncEngine.Strategy.AGGRESSIVE, true)
    )

    /**
     * Draw one sample from each arm's posterior and take the best.
     *
     * Untried arms sit at Beta(1,1), a uniform prior, so they get explored
     * naturally without a hand-written exploration rule. `exclude` lets the
     * retry loop walk down the ranking after a failure.
     */
    fun choose(userMode: Mode = Mode.AUTO, exclude: Set<String> = emptySet()): Arm {
        val key = contextKey()
        val stats = table.getOrPut(key) { HashMap() }

        val pool = arms().filter { it.id !in exclude }.let { list ->
            when (userMode) {
                // Fast: never pay the latency cost of heavy desync
                Mode.FAST -> list.filter { it.desync == null || it.desync == DesyncEngine.Strategy.SPLIT }
                // Safe: always keep an evasion layer on
                Mode.SAFE -> list.filter { it.desync != null }
                Mode.AUTO -> list
            }.ifEmpty { list }
        }.ifEmpty { arms() }

        val pick = pool.maxByOrNull { arm ->
            val s = stats.getOrPut(arm.id) { Stats() }
            sampleBeta(s.alpha, s.beta)
        } ?: pool.first()

        val s = stats[pick.id]
        LogBus.log(
            LogLevel.INFO,
            "brain: ctx=$key pick=${pick.label} " +
                "p=${"%.2f".format(s?.let { it.alpha / (it.alpha + it.beta) } ?: 0.5)} " +
                "n=${s?.trials ?: 0}"
        )
        return pick
    }

    /** Feed the outcome back. `quality` is how many of the six probes passed. */
    fun record(arm: Arm, success: Boolean, qualityPassed: Int = 0, rttMs: Int = 0) {
        val key = contextKey()
        val s = table.getOrPut(key) { HashMap() }.getOrPut(arm.id) { Stats() }
        if (success) {
            // a partial pass is partial credit, not a win
            s.alpha += (qualityPassed / 6.0).coerceIn(0.34, 1.0)
        } else {
            s.beta += 1.0
        }
        if (rttMs > 0) s.lastRttMs = rttMs
        s.trials++

        // Slow decay keeps old knowledge from freezing the sampler: the network
        // changes, so certainty should erode if it is not refreshed.
        if (s.trials % DECAY_EVERY == 0) {
            s.alpha = 1.0 + (s.alpha - 1.0) * DECAY
            s.beta = 1.0 + (s.beta - 1.0) * DECAY
        }
        save()
        LogBus.log(
            LogLevel.DEBUG,
            "brain: ${arm.label} -> ${if (success) "ok" else "fail"} ($qualityPassed/6), " +
                "posterior=${"%.2f".format(s.alpha / (s.alpha + s.beta))}"
        )
    }

    fun reset() {
        table.clear(); prefs.edit().clear().apply()
        LogBus.log(LogLevel.INFO, "brain: learning reset")
    }

    /** Human-readable summary for the diagnostics console. */
    fun summary(): String {
        val key = contextKey()
        val stats = table[key] ?: return "no data for $key yet"
        return buildString {
            appendLine("context: $key")
            stats.entries
                .sortedByDescending { it.value.alpha / (it.value.alpha + it.value.beta) }
                .take(5)
                .forEach { (id, s) ->
                    appendLine(
                        "  ${"%.0f%%".format(100 * s.alpha / (s.alpha + s.beta))}  " +
                            "n=${s.trials}  $id"
                    )
                }
        }
    }

    // ---------- sampling ----------

    /**
     * Beta sampling via two Gammas. Marsaglia-Tsang for shape >= 1, with the
     * standard boost for shape < 1, which our decay can produce.
     */
    private fun sampleBeta(a: Double, b: Double): Double {
        val x = sampleGamma(a); val y = sampleGamma(b)
        return if (x + y <= 0) 0.5 else x / (x + y)
    }

    private fun sampleGamma(shape: Double): Double {
        if (shape < 1.0) {
            val u = Random.nextDouble().coerceAtLeast(1e-12)
            return sampleGamma(shape + 1.0) * Math.pow(u, 1.0 / shape)
        }
        val d = shape - 1.0 / 3.0
        val c = 1.0 / sqrt(9.0 * d)
        while (true) {
            var x: Double; var v: Double
            do { x = gaussian(); v = 1.0 + c * x } while (v <= 0)
            v = v * v * v
            val u = Random.nextDouble()
            if (u < 1 - 0.0331 * x * x * x * x) return d * v
            if (ln(u) < 0.5 * x * x + d * (1 - v + ln(v))) return d * v
        }
    }

    private fun gaussian(): Double {
        val u1 = Random.nextDouble().coerceAtLeast(1e-12)
        val u2 = Random.nextDouble()
        return sqrt(-2.0 * ln(u1)) * kotlin.math.cos(2.0 * Math.PI * u2)
    }

    // ---------- persistence ----------

    private fun save() = runCatching {
        val root = JSONObject()
        table.forEach { (ctxKey, arms) ->
            val o = JSONObject()
            arms.forEach { (armId, s) ->
                o.put(armId, JSONObject().apply {
                    put("a", s.alpha); put("b", s.beta)
                    put("r", s.lastRttMs); put("n", s.trials)
                })
            }
            root.put(ctxKey, o)
        }
        prefs.edit().putString(KEY, root.toString()).apply()
    }

    private fun load() = runCatching {
        val raw = prefs.getString(KEY, null) ?: return@runCatching
        val root = JSONObject(raw)
        root.keys().forEach { ctxKey ->
            val arms = root.getJSONObject(ctxKey)
            val map = HashMap<String, Stats>()
            arms.keys().forEach { armId ->
                val o = arms.getJSONObject(armId)
                map[armId] = Stats(
                    o.optDouble("a", 1.0), o.optDouble("b", 1.0),
                    o.optInt("r", 0), o.optInt("n", 0)
                )
            }
            table[ctxKey] = map
        }
    }

    enum class Mode { AUTO, FAST, SAFE }

    companion object {
        private const val PREFS = "onvo_brain"
        private const val KEY = "posteriors"
        private const val DECAY = 0.85
        private const val DECAY_EVERY = 10
    }
}
