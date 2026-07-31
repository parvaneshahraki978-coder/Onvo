#!/usr/bin/env bash
# Restores the build toolchain into /home/user/.toolchain
# Needed because the toolchain is far larger than the workspace snapshot budget,
# so it disappears between sessions while the source tree persists.
set -euo pipefail

T=/home/user/.toolchain
mkdir -p "$T"

if [ ! -d "$T/jdk17" ]; then
  echo "==> JDK 17"
  curl -sL -o /tmp/jdk17.tar.gz \
    "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.13%2B11/OpenJDK17U-jdk_x64_linux_hotspot_17.0.13_11.tar.gz"
  ( cd "$T" && tar xzf /tmp/jdk17.tar.gz && mv jdk-17* jdk17 )
fi

if [ ! -d "$T/gradle" ]; then
  echo "==> Gradle 8.9"
  curl -sL -o /tmp/gradle.zip "https://services.gradle.org/distributions/gradle-8.9-bin.zip"
  ( cd "$T" && unzip -q /tmp/gradle.zip && mv gradle-8.9 gradle )
fi

if [ ! -d "$T/sdk/platforms/android-35" ]; then
  echo "==> Android SDK"
  curl -sL -o /tmp/cmdtools.zip \
    "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
  mkdir -p "$T/sdk/cmdline-tools"
  ( cd "$T/sdk/cmdline-tools" && unzip -q /tmp/cmdtools.zip && mv cmdline-tools latest 2>/dev/null || true )
  export JAVA_HOME="$T/jdk17"
  yes | "$T/sdk/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null 2>&1 || true
  "$T/sdk/cmdline-tools/latest/bin/sdkmanager" \
    "platform-tools" "platforms;android-35" "build-tools;34.0.0" "build-tools;35.0.0" >/dev/null 2>&1
fi

echo "==> ready"
"$T/jdk17/bin/java" -version 2>&1 | head -1
