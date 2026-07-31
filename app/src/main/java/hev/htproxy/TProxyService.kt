package hev.htproxy

/**
 * JNI bridge to the hev-socks5-tunnel shared library.
 *
 * The upstream project (heiher/hev-socks5-tunnel, MIT) ships an Android
 * target (`Android.mk`) whose JNI layer — `src/hev-jni.c` — registers these
 * exact methods against the class `hev/htproxy/TProxyService` via
 * `RegisterNatives` in `JNI_OnLoad`. The package and class name below are
 * therefore not our choice: they must match upstream's defaults
 * (`PKGNAME=hev/htproxy`, `CLSNAME=TProxyService`) or `loadLibrary` fails
 * with an UnsatisfiedLinkError.
 *
 * This is the piece that was missing in previous builds: the bridge .so was
 * never produced by CI, and even when it was discussed, the Kotlin side
 * declared its own `external fun nativeStart/nativeStop` which do not exist
 * in the library. With this class in place the app and the library finally
 * speak the same JNI contract.
 */
class TProxyService {
    /** Starts the tunnel bridge. Returns true when the worker thread is up. */
    external fun TProxyStartService(configPath: String, tunFd: Int): Boolean

    /** Stops the bridge and joins the worker thread. */
    external fun TProxyStopService(): Boolean

    external fun TProxyIsRunning(): Boolean

    external fun TProxyGetStats(): LongArray
}
