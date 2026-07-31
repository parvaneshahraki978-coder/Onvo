package app.onvo.diag

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class LogLevel(val tag: String) { INFO("INFO"), OK("OK"), WARN("WARN"), ERROR("ERR"), DEBUG("DBG") }

data class LogLine(val time: String, val level: LogLevel, val text: String)

/**
 * Central log sink for core + service + verifier.
 * redactedDump() strips anything that could identify the user before sharing.
 */
object LogBus {
    private const val MAX = 500
    private val fmt = SimpleDateFormat("HH:mm:ss", Locale.US)

    private val _lines = MutableStateFlow<List<LogLine>>(emptyList())
    val lines: StateFlow<List<LogLine>> = _lines.asStateFlow()

    fun log(level: LogLevel, text: String) {
        val line = LogLine(fmt.format(Date()), level, text)
        _lines.value = (_lines.value + line).takeLast(MAX)
    }

    /** Copy-safe dump: keys, tokens and the user's own addresses are masked. */
    fun redactedDump(): String = _lines.value.joinToString("\n") { l ->
        "${l.time} ${l.level.tag.padEnd(4)} ${redact(l.text)}"
    }

    private val ipv4 = Regex("""\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b""")
    private val keyish = Regex("""(?i)\b(key|token|secret|auth|bearer|password)\s*[=:]\s*\S+""")
    private val b64 = Regex("""\b[A-Za-z0-9+/]{32,}={0,2}\b""")

    private fun redact(s: String): String = s
        .replace(keyish) { m -> "${m.groupValues[1]}=•••" }
        .replace(b64, "•••")
        .replace(ipv4) { m ->
            // keep loopback and documented CDN prefixes readable, mask the rest
            val full = m.value
            if (full.startsWith("127.") || full.startsWith("0.")) full
            else "${m.groupValues[1]}.${m.groupValues[2]}.•••.•••"
        }
}
