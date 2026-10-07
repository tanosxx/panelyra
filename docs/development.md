# Development and verification

[Home](../README.md) · [Contributing](../CONTRIBUTING.md) · [Distribution](distribution.md)

Use Ubuntu's `/usr/bin/python3` with the dependencies from the README. The Linux
runtime uses PyGObject, GTK 3, GStreamer and the current user's GNOME D-Bus
session. The Android project uses Java 8 source compatibility, JDK 17, Gradle
8.9 and Android Gradle Plugin 8.7.3, with compile/target SDK 35 and minimum SDK 19.

For isolated Python development, create a virtual environment that can see the
distribution's GI bindings:

```bash
sudo apt install python3-venv
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install .
.venv/bin/panelyra --version
```

`pyproject.toml` supplies the `panelyra` and `panelyra-gui` console entry points.
Installing the Python package does not install GStreamer, GTK, codecs, desktop
shortcuts or the Android companion. For normal Ubuntu desktop installation,
prefer the source launcher or Debian package instructions. The package is not
claimed to be published on PyPI.

## Fast checks without a tablet

From the source root:

```bash
/usr/bin/python3 -m compileall -q usbdisplay scripts tests
/usr/bin/python3 -m unittest discover -s tests -v
PANELYRA_TEST_NETWORK=1 android/tests/run.sh
./panelyra --help
./panelyra --version
```

The Python suite checks framing, USB selection/routing, APK server access and
capture lifecycle behavior with mocked D-Bus objects. Its HTTP server tests need
permission to open local sockets. The Java tests check video framing, language
selection/control and USB network selection without an Android SDK or device;
JDK 17 is sufficient. `PANELYRA_TEST_NETWORK=1` also tests the language listener
on loopback port 27184, including malformed requests, timeouts and shutdown.
Omit that variable for parser-only checks where local sockets are prohibited.

GTK language-switching tests use real widgets and isolated preferences. They
need GTK 3 and a display; without those, the GUI cases are skipped. To include
them on a headless machine, as CI does:

```bash
sudo apt install xvfb xauth dbus-x11 gir1.2-gtk-3.0
dbus-run-session -- xvfb-run -a /usr/bin/python3 -m unittest discover -s tests -v
```

For an actual encode → packetize → decode check, install the software decoder
and run the synthetic smoke test:

```bash
sudo apt install gstreamer1.0-libav
/usr/bin/python3 scripts/smoke_test.py --width 1600 --height 1000 \
  --fps 30 --capture-fps 60 --quality 18
```

This does not capture the desktop or require a tablet. It checks dimensions,
timestamp ordering, first-frame configuration, frame delivery and decoded output.
Successful software decoding does not establish compatibility or performance
on a particular hardware decoder.

To exercise encoder replacement and automatic lock recovery without locking the
real desktop or creating any monitors, run:

```bash
/usr/bin/python3 scripts/smoke_test.py --lock-cycle --width 1600 --height 1000 \
  --fps 30 --capture-fps 60 --quality 18
```

This uses synthetic video and the local animated lock screen for two lock/unlock
cycles, including a lock lasting longer than Android's 10-second read timeout.
It verifies continuous delivery, increasing packet timestamps and decoding
across encoder restarts. It cannot
verify a particular GNOME session or Android hardware decoder. For a live check,
restart the sender, lock/unlock the PC normally, and confirm both capture and
cursor resume; individual windows may have moved to the main monitor.

## Real GNOME capture checks

Run these from a terminal inside an unlocked GNOME Wayland desktop, after
stopping any normal Panelyra connection:

```bash
/usr/bin/python3 scripts/smoke_test.py --virtual-screen
/usr/bin/python3 scripts/smoke_test.py --virtual-screen --disconnect-after 3
```

These temporarily add a monitor and capture it into a local test receiver. They
check session cleanup on normal completion and receiver disconnection. They do
not save a desktop recording. Do not run these in ordinary hosted CI or a
headless SSH session; those environments do not provide the needed Mutter session.

## Android builds

The full build, signing and toolchain instructions are in
[android/README.md](../android/README.md). From the source root, the local Ubuntu
toolchain route is:

```bash
scripts/build-android.sh --bootstrap
```

After bootstrapping, `scripts/build-android.sh` can use the cached tools. This
fallback route is specific to Ubuntu amd64 and compiles the API subset used by
the app against Ubuntu's API 23 stubs. The normal Gradle route compiles against
SDK 35. Debug builds are intended for sideload testing and retain a local debug
certificate. A new release key must be kept outside the repository and supplied
explicitly for release signing.

## Linux package checks

```bash
desktop-file-validate packaging/io.github.tanosx.Panelyra.desktop
appstreamcli validate --no-net --override=url-homepage-missing=info \
  packaging/io.github.tanosx.Panelyra.metainfo.xml
/usr/bin/python3 scripts/build-deb.py --output-dir out
```

The AppStream override is deliberately limited to a **preview** with no confirmed
public homepage. It does not make the metadata store-ready. Release validation
must use real public URLs and screenshots and pass without that override; see
[distribution](distribution.md). The package builder does not install the
package, contact a tablet or include a debug APK by default.

## Source archive

Use the allowlisted source builder for release candidates:

```bash
/usr/bin/python3 scripts/build-source.py --check
/usr/bin/python3 scripts/build-source.py --list
/usr/bin/python3 scripts/build-source.py
```

The first two commands audit inputs without creating output. The final command
creates `out/panelyra-0.3.0-source.tar.gz` and its SHA-256 file. It excludes local
toolchains, keys, Git metadata, build outputs and backups, rejects unexpected
risky source inputs, and normalizes archive metadata for deterministic output
from identical source contents. See the [GitHub guide](github.md) for publication.

## Local verification of the 0.3.0 preparation

On 2026-10-06, the Python unit suite and all 70 standalone Java assertions passed.
The synthetic H.264 round trip at 1600 × 1000, 60 Hz capture, 30 fps transmission
and QP 18 decoded every transmitted frame without corruption. English and Russian
Linux windows were rendered for the documentation screenshots, and GUI controls,
profile selection, settings saving and owned-process startup/stop were checked
with a test child process without a tablet.

A Python wheel was built as `out/wheels/panelyra-0.3.0-py3-none-any.whl`; it still
requires the system GI, GTK and GStreamer dependencies. These local results do
not establish a remote CI run, a store review or physical-device testing of the
new Android UI.

## CI

[The GitHub Actions workflow](../.github/workflows/ci.yml) checks Python, a
synthetic video stream, Java parser/network tests, desktop metadata, a preview
Debian package and a Gradle debug build. It uses read-only repository permissions,
fixed action revisions and no production signing secrets. It does not publish
packages or releases. Android SDK packages are installed on the hosted runner.

CI cannot verify the GNOME desktop integration, actual USB hardware, vendor
MediaCodec behavior or the appearance of the Android UI on a real tablet. Those
checks remain explicit release tasks. A workflow present in the source tree is
not evidence that a remote CI run has already passed.

## Physical-device release check

Before publishing a release, use the actual signed candidate and record:

1. Linux/GNOME version, Android model/build, transport and exact video mode.
2. Initial install and upgrade from the previous release certificate.
3. USB tether discovery, APK installation instructions and optional ADB path.
4. Cursor movement, text readability, moving windows and 30 seconds of video.
5. App background/foreground, lock/unlock, USB removal and reconnection.
6. Normal stop and exit, confirming the virtual monitor and owned ADB rule disappear.
7. Sender stats during motion; distinguish them from any measured display latency.
8. Linux and Android screenshots that contain no personal desktop content.

The original video path was checked on Lenovo TB-X606X / Android 10. The 0.3.0
branding/UI build has not yet repeated that physical-device check. Do not turn
one device result into a claim of support for every Android device.
