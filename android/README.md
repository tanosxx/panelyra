# Panelyra for Android

The small receiver for [Panelyra](../README.md): turn an Android tablet into an
extra Linux screen over a USB cable. Plain Java and platform Views, with no
Compose, WebView, analytics, ads, or third-party runtime libraries. The app feeds
H.264 into Android MediaCodec and renders directly to a SurfaceView.

Version **0.3.0**, version code **4**. Package ID stays `dev.usbdisplay.client`, so
an APK signed with the same key can update earlier USB Display installations.
The welcome screen and connection instructions support English and Russian.
Before the first language setting arrives from Linux, they follow the tablet's
system language (Russian for a Russian locale, English otherwise). A received
choice is saved in the app's private preferences and survives restarts.
Diagnostic messages from the codec and network can remain English.

Android 4.4 / API 19 is the minimum declared version. A working H.264 decoder is
required; device compatibility and decoder performance need physical testing.
The established streaming code was tested on a **Lenovo Tab M10 FHD Plus
TB-X606X, Android 10**, using USB tethering. The user confirmed smooth improvement
with **1600×1000, 30 transmitted frames/s, 60 Hz capture, constant quality QP 18**.
This is an observed usable configuration, not a measured latency guarantee.
The new 0.3.0 welcome screen and language synchronization still need a confirmed
physical-device check before release.

## Connect

1. Connect a USB cable that supports data and enable **USB tethering** in Android
   settings. MTP / file transfer alone is insufficient.
2. Open **Panelyra** on the tablet and keep it in the foreground.
3. The connection card should show **USB tethering** and the tablet's USB address.
   The **Connection settings** button opens Android's network settings; the exact
   location of USB tethering depends on the manufacturer. Use **Refresh connection**
   if you changed USB mode and the address has not updated.
4. Launch Panelyra on Linux and start streaming. The welcome screen disappears
   on the first decoded frame. USB debugging is not needed for this connection.

You can alternatively enable USB debugging, authorize your own computer, and
use the Linux sender's ADB transport. The app displays the ADB waiting address
when USB tethering is unavailable.

The app stays in landscape and fits the video without stretching; black bars
can appear when aspect ratios differ. The screen stays awake while the app is
open. Leaving the app closes its sockets immediately and releases the decoder
on its worker thread. Returning to the app starts listening again.

## Interface language

The Linux GUI sends its chosen language when it connects. Changing **More options
→ Language** during the stream started by that window updates the Android
interface without restarting the activity, video surface or decoder. **System
language** in the Linux GUI means the PC's language, resolved to Russian or
English; it does not restore Android's system-language fallback.

For the CLI, use `panelyra start --language en`, `--language ru`, or
`--language auto` (the PC's system language). Without this option, CLI streaming
leaves the Android language unchanged. The GUI cannot change the language of an
independent CLI stream.

To restore the tablet's system-language fallback, clear Panelyra's app data in
Android settings. The next explicit language received from a PC will override
that fallback again. Only the two-letter choice is sent over the local USB/ADB
connection; no online language service is used.

**По-русски:** до первой настройки с ПК приложение использует язык системы
планшета: русский для русской локали, иначе английский. Затем оно запоминает
язык, выбранный в Linux, и меняет его при подключении или во время передачи,
запущенной из этого окна. «Язык системы» на ПК означает язык системы ПК. Из CLI
язык можно передать параметром `start --language ru`, `en` или `auto`; без
параметра сохраняется текущий язык Android. Очистка данных приложения Android
возвращает системный выбор до следующей настройки с ПК.

## Install a debug build

Build outputs are not committed. The local build produces both:

- `out/panelyra-0.3.0-android-debug.apk`
- `out/usb-tablet-display-debug.apk` — identical compatibility copy for the sender's
  install / APK sharing commands.

In the Linux GUI, click **Android app**, choose the APK and click **Start server**.
The port, USB interface and time limit can be changed in that window. Alternatively,
from the repository root use `./panelyra serve-apk` over USB tethering. Open the
displayed address in the tablet's browser, download the APK, and allow installation
from that browser if Android asks. The download server binds to the USB address.
Install the APK over the existing app, then open Panelyra again. The webpage
supports English and Russian with a **RU / EN** switch; its default follows the
Linux GUI language when started there. No USB debugging is needed for this route.
With authorized ADB, use `./panelyra install` or `adb install -r path/to/app.apk`.

The original local debug keystore remains at `.tools/debug.keystore` and is
excluded from source archives and Git. A fresh checkout normally creates a new
debug key. Android will reject an update signed by a different key; keep the
existing key for local upgrades, or explicitly uninstall the old app before
installing a differently signed build. Public releases should use a private,
stable release key, never the shared Android debug credentials.

## Standard Android build

Use JDK 17, Gradle 8.9, Android SDK Platform 35, and Android Gradle Plugin 8.7.3.
The source declares minSdk 19, targetSdk 35, and compileSdk 35. No Gradle Wrapper
binary is included. Open `android/` in Android Studio or configure `JAVA_HOME`
and `ANDROID_HOME`, then run from `android/`:

```bash
gradle :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n dev.usbdisplay.client/.MainActivity
```

The first Gradle build downloads its dependencies. To add a wrapper yourself:

```bash
gradle wrapper --gradle-version 8.9 --distribution-type bin
./gradlew :app:assembleDebug
```

Normal Gradle builds include PNG launcher icons for older Android versions and
adaptive icons for API 26+. The adaptive resources live in `app/src/modern/res`
and are included through the Gradle source-set configuration.

## Small offline-capable Ubuntu build

From the repository root:

```bash
scripts/build-android.sh --bootstrap  # first run; downloads build dependencies
scripts/build-android.sh              # subsequent offline builds
```

The bootstrap currently supports **Ubuntu amd64**. It extracts tools into
`.tools/` without installing system packages or accepting an Android SDK license.
It uses Ubuntu's API 23 compilation stubs, OpenJDK 17, Java 8 bytecode, and
D8/R8 8.7.18 desugaring. All app APIs used at compile time exist in API 23;
the output manifest still has minSdk 19 / targetSdk 35. This route includes the
PNG icons only; adaptive-icon resources require the standard SDK 35 build.

The build verifies the APK signature, zip alignment, and manifest. Reports are
written to `out/apk-info-debug.txt`, `out/build-info-debug.txt`, and a `.sha256`
file next to the APK. These checks do not replace physical decoder and UI tests.

## Signing a release

Both build routes read the same four environment variables:

| Variable | Value |
| --- | --- |
| `PANELYRA_KEYSTORE` | Absolute path to your private release keystore, outside the repository |
| `PANELYRA_KEY_ALIAS` | Existing key alias inside that keystore |
| `PANELYRA_STORE_PASSWORD` | Keystore password |
| `PANELYRA_KEY_PASSWORD` | Key password |

Keep your release key and a secure backup outside the project. Neither build
route generates a release key or falls back to a debug key. Do not put passwords
in scripts, a committed `.env`, shell command arguments, or CI logs. In CI, inject
them through the platform's secret store. For a local Bash session, read passwords
without adding them to shell history:

```bash
export PANELYRA_KEYSTORE=/absolute/private/path/panelyra-release.jks
export PANELYRA_KEY_ALIAS=panelyra
read -r -s -p 'Keystore password: ' PANELYRA_STORE_PASSWORD; printf '\n'
read -r -s -p 'Key password: ' PANELYRA_KEY_PASSWORD; printf '\n'
export PANELYRA_STORE_PASSWORD PANELYRA_KEY_PASSWORD
scripts/build-android.sh --release
unset PANELYRA_STORE_PASSWORD PANELYRA_KEY_PASSWORD
```

The local builder outputs `out/panelyra-0.3.0-android-release.apk`, with
`debuggable=false`, and passes password **environment references** to apksigner.
The existing debug APK and debug reports are preserved.

For the standard build, set the same variables and run from `android/`:

```bash
gradle :app:assembleRelease
```

The Gradle release artifact is `app/build/outputs/apk/release/app-release.apk`.
Packaging a release without all four variables fails deliberately. Release signing
is prepared but has not been exercised with the author's private release key;
no signed public release is claimed. No build command uploads or publishes files.

## Tests and artwork

The protocol and USB boundary tests need only a JDK, no Android SDK or emulator.
From the repository root:

```bash
PANELYRA_TEST_NETWORK=1 android/tests/run.sh
```

If using the extracted Ubuntu toolchain:

```bash
JAVA_HOME="$PWD/.tools/jdk/usr/lib/jvm/java-17-openjdk-amd64" PANELYRA_TEST_NETWORK=1 android/tests/run.sh
```

The network flag enables real loopback tests on port 27184: valid language
requests, malformed/truncated data, timeouts, shutdown and port reuse. Omit the
flag where opening local sockets is prohibited; the parser tests still run.

The tests cover malformed/truncated headers and packets, frame bounds, monotonic
timestamps, SPS/PPS extraction, preserved AUD/SEI/multiple IDR slices, interface
allowlisting, IPv4 subnet boundaries, and loopback fallback. An optional argument
to `run.sh` is the path to a real first H.264 Annex B access unit for additional
parser checks. The tests do not run MediaCodec or exercise Android UI lifecycle.

Launcher assets are rendered from the shared editable
[`panelyra.svg`](../usbdisplay/assets/panelyra.svg). To regenerate them on Ubuntu
with `python3-gi`, `python3-cairo`, and `gir1.2-rsvg-2.0` installed:

```bash
/usr/bin/python3 android/scripts/generate-icons.py
```

## Connection boundaries and lifecycle

The app detects IPv4 addresses on active `rndis*`, `usb*`, or `ncm*` interfaces
and binds **one concrete USB address on TCP port 27183** for video, with a
separate listener on **27184** for language control. It never binds a wildcard
address or a Wi-Fi interface. Accepted peers must belong to the selected USB
IPv4 subnet. Interface detection is repeated approximately every 1.5 seconds
while waiting; it does not interrupt active playback.

If no USB interface is found, the listeners bind to **127.0.0.1** on the same
ports for ADB and accept only IPv4 loopback peers. The Linux sender creates its
own ADB forwarding rules.
Manufacturer-specific USB interface names outside the allowlist require an
explicit, verified code update. The only Android permission is `INTERNET`, which
TCP requires even for loopback. There are no runtime permission requests.

A disconnected cable, invalid packet, decoder error, or 10 seconds without
incoming data ends the connection; the app can then accept another stream.
Video and language control are not encrypted and have no separate authentication.
Use a direct USB connection to a computer you trust; ADB authorization also grants that computer
broader device access. This is a display receiver, not a public network service.

## UTD1 wire protocol

Each connection starts a new stream. All integers are big-endian.

| Header field | Size | Value |
| --- | --- | --- |
| Magic | 4 bytes | ASCII `UTD1` |
| Width | uint32 | 160–2560, even |
| Height | uint32 | 160–2560, even |
| FPS | uint32 | 5–60 |

Packets follow: `uint32 payload_length`, `uint64 pts_microseconds`, and
`payload_length` bytes of H.264 Annex B. One packet carries one access unit;
length is 1 byte to 8 MiB. Timestamps must be monotonic and fit the nonnegative
signed 64-bit range. The first packet must contain SPS, PPS, and an IDR keyframe.
The sender uses Baseline H.264, no B-frames, and a low-latency encoder mode.

The client extracts SPS/PPS into MediaCodec `csd-0`/`csd-1` and removes those NALs
from the first submitted access unit to avoid feeding configuration twice.
Input and output are processed on separate workers without an application-side
video queue. Stale **decoded** frames can be skipped to limit delay; encoded
reference frames are not discarded.

Audio, touch forwarding, background streaming, and changing stream dimensions
within an existing connection are not implemented. Linux creates the extra
monitor; this Android app only receives its image.

Language control uses a separate, bounded request and acknowledgement; it does
not modify UTD1 video framing. Old receivers without this listener can still play
video from the updated sender. See the
[language control protocol](../docs/protocol.md#language-control) for its exact
bytes and validation rules.

Build requirements: [AGP 8.7 documentation](https://developer.android.com/build/releases/agp-8-7-0-release-notes).
Codec contract: [MediaCodec documentation](https://developer.android.com/reference/android/media/MediaCodec).
License and author: [MIT, TanosX](../LICENSE).
