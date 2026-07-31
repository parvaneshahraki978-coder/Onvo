plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.onvo"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.onvo"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        resourceConfigurations += listOf("en", "fa")
        multiDexEnabled = true
    }

    signingConfigs {
        create("release") {
            // CI provides these; locally the debug-signed build is used instead
            val ks = file("../onvo-release.jks")
            if (ks.exists()) {
                storeFile = ks
                storePassword = System.getenv("ONVO_STORE_PASSWORD")
                keyAlias = System.getenv("ONVO_KEY_ALIAS")
                keyPassword = System.getenv("ONVO_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            if (file("../onvo-release.jks").exists())
                signingConfig = signingConfigs.getByName("release")
            // R8 — as you asked
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
        debug {
            isMinifyEnabled = false
        }
    }

    // one APK per ABI so users only download their own architecture
    splits {
        abi {
            isEnable = true
            reset()
            include("arm64-v8a", "armeabi-v7a", "x86_64")
            isUniversalApk = true
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    lint { checkReleaseBuilds = false }

    buildFeatures { compose = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }

    packaging {
        resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" }
        jniLibs { useLegacyPackaging = true }   // native core must be extractable + executable
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.03")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-service:2.8.6")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.core:core-splashscreen:1.0.1")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.navigation:navigation-compose:2.8.1")

    implementation("androidx.datastore:datastore-preferences:1.1.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.1")

    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.compose.ui:ui-tooling-preview")
}
