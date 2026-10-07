#!/usr/bin/env bash
# Small, offline-capable build of this Java-only app on Ubuntu 26.04 amd64.
# --bootstrap downloads open-source Ubuntu packages without installing them or
# accepting the Google SDK agreement. Normal Gradle builds compile against API35;
# this fallback compiles against the API23 stubs (all used APIs exist there), with
# the SAME minSdk19/targetSdk35 manifest and D8 Java8 lambda desugaring.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TOOLS="$ROOT/.tools"
DOWNLOADS="$TOOLS/downloads"
ANDROID_TOOLS="$TOOLS/android"
JDK_ROOT="$TOOLS/jdk"
JAVA_HOME="$JDK_ROOT/usr/lib/jvm/java-17-openjdk-amd64"
R8="$DOWNLOADS/r8-8.7.18.jar"
R8_SHA256=58366f77067207c39a17d469de7b05701d2877212a9c55201bcb0af43e59e903

BOOTSTRAP=false
BUILD_TYPE=debug
for argument in "$@"; do
    case "$argument" in
        --bootstrap) BOOTSTRAP=true ;;
        --release) BUILD_TYPE=release ;;
        -h|--help)
            echo 'Usage: scripts/build-android.sh [--bootstrap] [--release]'
            echo 'Release: set PANELYRA_KEYSTORE, PANELYRA_KEY_ALIAS, PANELYRA_STORE_PASSWORD,'
            echo 'and PANELYRA_KEY_PASSWORD. Keys are never generated for release builds.'
            exit 0 ;;
        *) echo "Unknown argument: $argument" >&2; exit 2 ;;
    esac
done
if [[ "$BUILD_TYPE" == release ]]; then
    for variable in PANELYRA_KEYSTORE PANELYRA_KEY_ALIAS PANELYRA_STORE_PASSWORD PANELYRA_KEY_PASSWORD; do
        [[ -n "${!variable:-}" ]] || { echo "Missing release signing variable: $variable" >&2; exit 2; }
    done
    [[ -f "$PANELYRA_KEYSTORE" ]] || { echo 'Release keystore does not exist.' >&2; exit 2; }
fi

if "$BOOTSTRAP"; then
    [[ "$(dpkg --print-architecture)" == amd64 ]] || {
        echo 'This local build bootstrap currently supports Ubuntu amd64.' >&2; exit 1;
    }
    mkdir -p "$DOWNLOADS" "$ANDROID_TOOLS" "$JDK_ROOT"
    (
        cd "$DOWNLOADS"
        # apt verifies downloads against the signed Ubuntu repository metadata.
        apt download openjdk-17-jdk-headless openjdk-17-jre-headless \
            android-sdk-platform-23 libandroid-23-java aapt android-libaapt \
            android-libandroidfw android-libbase android-libutils android-liblog \
            android-libziparchive android-libbacktrace android-libcutils \
            apksigner libapksig-java zipalign libzopfli1 7zip
        if [[ ! -f "$R8" ]]; then
            curl --fail --location --connect-timeout 20 --output "$R8.tmp" \
                https://dl.google.com/dl/android/maven2/com/android/tools/r8/8.7.18/r8-8.7.18.jar
            mv "$R8.tmp" "$R8"
        fi
    )
    for archive in "$DOWNLOADS"/*.deb; do
        case "$(basename "$archive")" in
            openjdk-17-*) dpkg-deb -x "$archive" "$JDK_ROOT" ;;
            *) dpkg-deb -x "$archive" "$ANDROID_TOOLS" ;;
        esac
    done
    # Debian JDK configuration symlinks normally point into /etc. Relocate them.
    python3 - "$JDK_ROOT" <<'PY'
import os
import sys
from pathlib import Path
root = Path(sys.argv[1])
for link in root.rglob('*'):
    if link.is_symlink() and str(link.readlink()).startswith('/etc/java-17-openjdk/'):
        target = root / str(link.readlink()).lstrip('/')
        if target.exists():
            link.unlink()
            link.symlink_to(os.path.relpath(target, link.parent))
PY
fi

for required in "$JAVA_HOME/bin/java" "$JAVA_HOME/bin/javac" "$R8" \
    "$ANDROID_TOOLS/usr/bin/aapt" "$ANDROID_TOOLS/usr/bin/zipalign" \
    "$ANDROID_TOOLS/usr/share/java/com.android.android-23.jar" \
    "$ANDROID_TOOLS/usr/share/java/apksigner.jar" "$ANDROID_TOOLS/usr/share/java/apksig.jar"; do
    [[ -f "$required" ]] || {
        echo "Missing build dependency: $required" >&2
        echo 'Run scripts/build-android.sh --bootstrap once with network access.' >&2
        exit 1
    }
done
printf '%s  %s\n' "$R8_SHA256" "$R8" | sha256sum --check --status
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$ANDROID_TOOLS/usr/lib/x86_64-linux-gnu/android:$ANDROID_TOOLS/usr/lib/x86_64-linux-gnu:$ANDROID_TOOLS/usr/lib/7zip:$ANDROID_TOOLS/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
PLATFORM_JAR="$ANDROID_TOOLS/usr/share/java/com.android.android-23.jar"
AAPT="$ANDROID_TOOLS/usr/bin/aapt"
SIGNER_CP="$ANDROID_TOOLS/usr/share/java/apksigner.jar:$ANDROID_TOOLS/usr/share/java/apksig.jar"
mkdir -p "$TOOLS/build" "$ROOT/out"
BUILD_DIR="$(mktemp -d "$TOOLS/build/android.XXXXXXXX")"
trap 'rm -rf -- "$BUILD_DIR"' EXIT
mkdir -p "$BUILD_DIR/gen" "$BUILD_DIR/classes" "$BUILD_DIR/dex"

# Read metadata from the Gradle project, keeping both build routes consistent.
python3 - "$ROOT" "$BUILD_DIR" "$BUILD_TYPE" <<'PY'
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
root, build = map(Path, sys.argv[1:3])
build_type = sys.argv[3]
gradle = (root / 'android/app/build.gradle').read_text()
def value(name):
    match = re.search(r'(?m)^\s*' + name + r'\s+(?:[\x27\x22]([^\x27\x22]+)[\x27\x22]|(\d+))\s*$', gradle)
    if not match:
        raise SystemExit('Cannot find Gradle property ' + name)
    return match.group(1) or match.group(2)
ns = 'http://schemas.android.com/apk/res/android'
ET.register_namespace('android', ns)
manifest = ET.parse(root / 'android/app/src/main/AndroidManifest.xml')
element = manifest.getroot()
element.set('package', value('applicationId'))
element.set(f'{{{ns}}}versionCode', value('versionCode'))
element.set(f'{{{ns}}}versionName', value('versionName'))
sdk = ET.SubElement(element, 'uses-sdk')
sdk.set(f'{{{ns}}}minSdkVersion', value('minSdk'))
sdk.set(f'{{{ns}}}targetSdkVersion', value('targetSdk'))
element.find('application').set(f'{{{ns}}}debuggable', str(build_type == 'debug').lower())
manifest.write(build / 'AndroidManifest.xml', encoding='utf-8', xml_declaration=True)
(build / 'min-sdk').write_text(value('minSdk'))
(build / 'version-name').write_text(value('versionName'))
PY
MIN_SDK="$(cat "$BUILD_DIR/min-sdk")"
VERSION_NAME="$(cat "$BUILD_DIR/version-name")"
"$AAPT" package -f -m -M "$BUILD_DIR/AndroidManifest.xml" \
    -S "$ROOT/android/app/src/main/res" -I "$PLATFORM_JAR" \
    -J "$BUILD_DIR/gen" -F "$BUILD_DIR/resources.apk"
mapfile -d '' SOURCES < <(find "$ROOT/android/app/src/main/java" "$BUILD_DIR/gen" -name '*.java' -print0)
javac --release 8 -encoding UTF-8 -classpath "$PLATFORM_JAR" \
    -d "$BUILD_DIR/classes" "${SOURCES[@]}"
jar --create --file "$BUILD_DIR/classes.jar" -C "$BUILD_DIR/classes" .
java -cp "$R8" com.android.tools.r8.D8 "--$BUILD_TYPE" --min-api "$MIN_SDK" \
    --lib "$PLATFORM_JAR" --output "$BUILD_DIR/dex" "$BUILD_DIR/classes.jar"
python3 - "$BUILD_DIR" <<'PY'
import shutil
import sys
import zipfile
from pathlib import Path
build = Path(sys.argv[1])
shutil.copyfile(build / 'resources.apk', build / 'unsigned.apk')
with zipfile.ZipFile(build / 'unsigned.apk', 'a', compression=zipfile.ZIP_DEFLATED) as apk:
    for dex in sorted((build / 'dex').glob('*.dex')):
        apk.write(dex, dex.name)
PY
"$ANDROID_TOOLS/usr/bin/zipalign" -f 4 "$BUILD_DIR/unsigned.apk" "$BUILD_DIR/aligned.apk"
if [[ "$BUILD_TYPE" == debug ]]; then
    KEYSTORE="$TOOLS/debug.keystore"
    if [[ ! -f "$KEYSTORE" ]]; then
        keytool -genkeypair -keystore "$KEYSTORE" -storepass android -keypass android \
            -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
            -dname 'CN=Android Debug,O=Android,C=US'
        chmod 600 "$KEYSTORE"
    fi
    SIGNING=(--ks "$KEYSTORE" --ks-pass pass:android --key-pass pass:android --ks-key-alias androiddebugkey)
else
    SIGNING=(--ks "$PANELYRA_KEYSTORE" --ks-pass env:PANELYRA_STORE_PASSWORD \
        --key-pass env:PANELYRA_KEY_PASSWORD --ks-key-alias "$PANELYRA_KEY_ALIAS")
fi
APK="$ROOT/out/panelyra-$VERSION_NAME-android-$BUILD_TYPE.apk"
java -cp "$SIGNER_CP" com.android.apksigner.ApkSignerTool sign \
    "${SIGNING[@]}" --out "$BUILD_DIR/signed.apk" "$BUILD_DIR/aligned.apk"
java -cp "$SIGNER_CP" com.android.apksigner.ApkSignerTool verify --verbose "$BUILD_DIR/signed.apk"
"$ANDROID_TOOLS/usr/bin/zipalign" -c 4 "$BUILD_DIR/signed.apk"
"$AAPT" dump badging "$BUILD_DIR/signed.apk" > "$ROOT/out/apk-info-$BUILD_TYPE.txt"
mv "$BUILD_DIR/signed.apk" "$APK"
(cd "$ROOT/out" && sha256sum "$(basename "$APK")" > "$(basename "$APK").sha256")
if [[ "$BUILD_TYPE" == debug ]]; then
    # Preserve the existing install / serve-apk integration and upgrade signature.
    cp "$APK" "$ROOT/out/usb-tablet-display-debug.apk"
    cp "$ROOT/out/apk-info-debug.txt" "$ROOT/out/apk-info.txt"
    (cd "$ROOT/out" && sha256sum usb-tablet-display-debug.apk > usb-tablet-display-debug.apk.sha256)
fi
cat > "$ROOT/out/build-info-$BUILD_TYPE.txt" <<EOF
Build route: scripts/build-android.sh (open-source local Ubuntu toolchain).
Variant: $BUILD_TYPE. Version: $VERSION_NAME. Application ID: dev.usbdisplay.client.
Java: OpenJDK17, Java8 bytecode; D8/R8 8.7.18 desugars lambdas for minSdk19.
Compilation stubs: Ubuntu Android API23. All app API references exist in API23.
Manifest: minSdk19 and targetSdk35, values read from android/app/build.gradle.
Icons: PNG fallback. Use the normal compileSdk35 Gradle route for adaptive icons.
Signature: $BUILD_TYPE key; passwords are never written to this build report.
Verification: apksigner signature verification, zipalign, aapt manifest inspection.
No Android emulator/device playback test is performed by this build script.
EOF
if [[ "$BUILD_TYPE" == debug ]]; then
    cp "$ROOT/out/build-info-debug.txt" "$ROOT/out/build-info.txt"
fi
echo "Built: $APK"
