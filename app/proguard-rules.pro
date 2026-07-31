# Keep the VPN service entry point — Android instantiates it by name
-keep class app.onvo.vpn.OnvoVpnService { *; }
-keep class app.onvo.update.** { *; }
# hev-socks5-tunnel binds these methods at runtime via RegisterNatives in
# JNI_OnLoad; R8 must not rename or strip the class or its method names.
-keep class hev.htproxy.TProxyService { *; }
-keepattributes *Annotation*, Signature, Exception
-dontwarn kotlinx.serialization.**
