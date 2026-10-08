# User guide

[Home](../README.md) · [Русский обзор](README.ru.md) · [Troubleshooting](troubleshooting.md)

Run commands below from the source directory. After installing a Linux package,
replace `./panelyra` with `panelyra`. The GUI and CLI use the same encoder and
transport; neither needs root for normal screen sharing.

## Everyday use

Connect the USB cable, enable USB tethering and open Panelyra on the tablet.
Open Panelyra on Linux, select a video mode and connect. Set the monitor's
position in GNOME Settings → Displays after connecting. The resolution belongs
to the Panelyra connection; monitor placement belongs to GNOME.

The Linux window has two tabs. **Connection** contains the tablet status,
connection help, the Android app download button and the connection log.
**Settings** groups image, connection and general preferences. Each tab scrolls
independently when needed; switching tabs or expanding a section does not enlarge
the window. The stream status and **Connect tablet / Stop** buttons remain
visible below both tabs.

Locking the PC displays an animated lock screen on the tablet, with a glowing
orb, drifting particles and the PC's local clock. The animation is rendered on
the PC and requires no Android update. If Cairo is unavailable, Panelyra shows
the static **Computer locked** image instead. Unlock
the PC normally: Panelyra reconnects the virtual screen automatically, keeping
the Android connection alive. It also attempts to restore the previous monitor
arrangement if the other displays have not changed. GNOME may move windows to
the main monitor during lock; returning those windows is manual in this mode.
This feature does not cover a disconnected USB cable or system suspension.

The **Connected device** panel refreshes automatically, or with its refresh
button. It shows the USB device name, whether tethering is enabled, and the
tablet's USB address when available. A detected USB link does not yet mean the
Android receiver is running. Recognisable Android devices can also appear before
tethering is enabled, with a prompt to enable it. Device names come from USB
descriptors, so some tablets expose only a generic Android name. This check does
not open a video connection or require USB debugging.

Choose **Settings → General → Language** to switch between English, Russian and the
system language. The window updates immediately, including while a stream is
running. Your choice is saved for the next launch. When the GUI connects, it also
sends that language to Android. Subsequent changes apply to Android during the
stream started by this window, without restarting the video. **System language**
means the PC's system language: Russian for a Russian locale, English otherwise.

Before Android receives its first language setting from a PC, it uses the
tablet's system language (Russian, or English for other locales). The last
received choice is saved on the tablet across app restarts and cable disconnects.
Clearing Panelyra's app data in Android settings restores the system-language
fallback until the next setting arrives. Synchronization needs the updated
Android companion; older receivers still play video without language control.

Maximizing the Linux window and restoring it returns to its previous normal
size. You can still resize it manually; that size is remembered for subsequent
maximize/fullscreen cycles while the window is open. This does not change the
tablet's display resolution.

The GUI saves its video and APK-server settings in `$XDG_CONFIG_HOME/panelyra/settings.json`
(normally `~/.config/panelyra/settings.json`). The CLI does not read these GUI
preferences: it uses the balanced profile unless another profile or explicit
options are supplied. Stop the stream before changing its mode, then connect
again. Closing the GUI stops the stream it started. An existing CLI
stream is detected rather than duplicated. `panelyra status` checks connection
state and `panelyra stop` stops a running Panelyra stream for the current user.

Keep the Android app visible. Unplugging USB or closing the Android app ends the
connection. Reopen the app and connect again after restoring the USB link.
The virtual monitor is removed when its owning stream ends.

## Notification bell

Use the bell in the Linux header to read version notices and developer news.
The counter shows unread items; the panel includes manual refresh, an action to
mark notices as read and an automatic-check switch. Checks happen while the
window is open, when due at startup and about every six hours. They are not
instant push messages. A missing Internet connection does not affect USB video.
Cached notices remain available, and a failed refresh is reported in the panel.

In this unpublished build the public source is not configured and no requests
are made. Once configured by the publisher, checks contact GitHub over HTTPS.
Turning automatic checks off leaves manual refresh available. Opening a notice's
link takes you to its GitHub page in your browser; no installer runs or package
downloads automatically. The CLI and Android app do not perform these checks.
See [updates and developer news](notifications.md) and [privacy](privacy.md).

## Commands

| Command | Purpose |
| --- | --- |
| `gui` | Open the Linux window |
| `start` | Create a monitor and send video |
| `status` | Inspect the running connection |
| `stop` | Request that the current user's stream stop |
| `profiles` | List the available video profiles |
| `doctor` | Check the desktop, plugins and USB/ADB transport |
| `configure-usb` | Temporarily keep a tether connection local-only |
| `serve-apk` | Serve one APK to the tablet over its USB link |
| `install` | Install an APK over authorized USB ADB |
| `--version` | Print the application version |

Use `--help` after any command for its complete current options.

`status --json` and `profiles --json` provide machine-readable output. The GUI
opens with its saved auto-connect preference; use `gui --no-connect` to open it
without starting a connection. `gui --language en` or `gui --language ru` chooses
an interface language; `auto` follows the environment.

To set the Android interface language from the CLI:

```bash
./panelyra start --language ru
./panelyra start --language en
./panelyra start --language auto
```

Here `auto` resolves the PC's system language. Omitting `--language` leaves the
Android language unchanged: its saved PC choice, or its own system language if
no choice has been received. The GUI does not change the language of a separate
stream started from a terminal.

## Video profiles

| Profile | Resolution | Transmission | Capture | Quality |
| --- | --- | --- | --- | --- |
| `balanced` (default) | 1600 × 1000 | 30 fps | 60 Hz | QP 18 |
| `economy` | 1280 × 800 | 20 fps | 60 Hz | QP 20 |
| `crisp` | 1600 × 1000 | 30 fps | 60 Hz | QP 14 |

```bash
./panelyra profiles
./panelyra start --profile economy
./panelyra start --profile balanced --width 1280 --height 800
```

Explicit width, height, FPS, capture FPS and quality options override the selected
profile. Economy lowers resolution and transmission load; crisp preserves more
detail at a higher variable bitrate. Profile names describe settings rather than
performance guarantees for every device.

## Resolution and smoothness

The tested default is:

```bash
./panelyra start --width 1600 --height 1000 --fps 30 --capture-fps 60 --quality 18
```

`--fps` limits transmission and decoder load. `--capture-fps` sets the desktop
capture rate, from the transmission rate up to 60 Hz. Capturing at 60 Hz while
sending 30 fps improved video smoothness on the tested Lenovo. It still sends
at most 30 fps and does not turn a 30 fps source video into 60 fps video.

For a weaker decoder or a busy PC, reduce resolution first while preserving
constant quality:

```bash
./panelyra start --width 1280 --height 800 --fps 30 --capture-fps 60 --quality 18
```

To reduce capture and decoding work further:

```bash
./panelyra start --width 1024 --height 640 --fps 20 --capture-fps 30 --quality 18
```

Width and height must be even numbers from 160 to 2560, and transmission must
be 5–60 fps. These are protocol limits, **not a guarantee that a tablet can decode
every permitted mode**. A 1920 × 1200 panel does not prove that its H.264 decoder
can handle a stream of that size. On this encoder, 1600 × 1000 at 30 fps uses
H.264 Level 4; 1920 × 1200 needs a higher level and is unverified on the Lenovo.

## Quality and bandwidth

The default `--rate-control quality` uses constant quantization, QP 18. A smaller
`--quality` value keeps more detail and uses more bandwidth; allowed values are
10–35. Bitrate varies with picture complexity, and `--bitrate` does not cap it.
Equal I/P quantization avoids periodic changes in detail in the tested workload.

If bandwidth must be limited, select bitrate mode explicitly:

```bash
./panelyra start --rate-control bitrate --bitrate 5000
```

`--bitrate` is in kbit/s, from 100 to 20000. This mode can visibly lose detail on
complex images or during motion; it was the source of quality fluctuation in the
initial local prototype. Constant quality remains the preferred desktop mode.

`--stats` prints sender frame rate, throughput, raw-frame filtering and the
longest socket write every five seconds. Measure during moving video: a still
desktop produces fewer updates. These counters do not measure Android's rendered
frame rate or end-to-end latency.

## USB tethering

Automatic detection selects an active, USB-backed RNDIS interface and determines
the tablet address from routes or DHCP data. The host checks the peer subnet and
outgoing route before connecting. MTP alone cannot carry the live display.

For multiple USB links, or a tablet using NCM/CDC Ethernet, provide the interface
and the IPv4 shown by the Android app:

```bash
./panelyra start --transport usb --interface INTERFACE --tablet-ip TABLET_IPV4
```

Replace both uppercase values with your actual connection values. Generic USB
Ethernet interfaces are not chosen automatically because they may be adapters
unrelated to a tablet. No address is hard-coded.

`--wait 120` retries a USB receiver connection for up to two minutes. It does
not enable tethering on Android. If tethering was enabled after the Android app
opened, refresh the app's connection display or reopen it.

Open **Settings → Connection → Internet via tablet**. Click **Turn off**
to remove the tablet's Internet default routes and DNS contribution while keeping
its local USB display connection. Click **Turn on** to restore the previous
settings. It is available during streaming and does not require an Android update.

**Allowed** means the PC may use the tablet for Internet access; routing priorities
still decide which connection carries traffic. When it is off, another PC
connection such as Wi-Fi is needed for Internet access. This changes only the
current USB connection, until it reconnects. Closing Panelyra leaves the chosen
state in place, and reopening it reads the actual state. The saved NetworkManager
profile is not edited. A connection disabled earlier with `configure-usb` can
also be enabled here. With multiple USB tethers, the control is unavailable so it
cannot accidentally change a different tablet. NetworkManager may request normal
desktop authorization; a rejected change leaves the previous state displayed.

`configure-usb` uses NetworkManager to temporarily remove the tablet's default
route and DNS contribution. It preserves the local subnet and does not edit the
saved profile. It may require desktop authorization under the machine's normal
NetworkManager policy. Internet access should continue over your existing Wi-Fi
or Ethernet connection.

## ADB alternative

1. Enable Android developer options and USB debugging, connect a data cable, then
   approve your computer's authorization request on the unlocked tablet.
2. Install ADB on Ubuntu and the companion APK:

   ```bash
   sudo apt install adb
   ./panelyra install --apk out/panelyra-0.4.0-android-debug.apk
   ./panelyra doctor --transport adb
   ```

3. Start the connection:

   ```bash
   ./panelyra start --transport adb
   ```

ADB transport opens the Android app automatically. Use `--no-launch` if it is
already open. With more than one ADB device, add `--serial DEVICE_SERIAL` to
`start` or `install`. The default ADB selection targets a USB device rather than
a network ADB session. The program uses free local forwarding ports for video
and, when requested, language control, and removes only its own forwarding rules
when it exits.

## Installing the companion without ADB

Click **Android app** in the Linux window's device panel. Choose the APK, USB
interface (automatic with one tablet), port and server lifetime, then click
**Start server**. The default port is 8765 and lifetime is 30 minutes. The file
choice and server options are saved; opening the app does not start the server.
The automatic APK choice looks for a local build or a bundled companion; use
the file chooser if the APK is elsewhere.

Open the displayed URL in the **tablet's browser**, download the APK and open
it to update the existing Android app. Allow installation from that browser if
Android asks. An update needs the same application ID and signing key as the
installed app. After installation, open Panelyra and connect from Linux. The
download count confirms file delivery, not installation.

The webpage initially uses the PC app's language; its **RU / EN** links let the
person using the tablet choose either language. It requires no Internet access.
**Copy address** copies the URL. **Stop server** closes only APK sharing and
does not stop a display stream. Hiding the download window keeps sharing active;
closing the main app, reaching the time limit or losing the selected USB link
stops it. If the port is occupied, stop the server that owns it or select another
port. No other process is stopped automatically.

**По-русски:** нажмите «Приложение Android» в окне на ПК, выберите APK, порт и
время работы, затем «Запустить сервер». Откройте адрес в браузере планшета,
скачайте APK и установите поверх старой версии. При необходимости разрешите
установку из браузера, затем откройте Panelyra. Страница доступна на русском
и английском, язык переключается ссылками RU / EN. Кнопка «Остановить сервер»
завершает скачивание, не прерывая передачу рабочего стола.

`serve-apk --apk PATH` exposes only the selected APK and its landing page, bound
to the PC's USB address. It accepts the selected tablet and the PC itself, with
no directory listing. Open the printed HTTP URL on the tablet. The server stops
with Ctrl+C or after 30 minutes by default. `--duration` changes that timeout and
`--port` chooses a port from 1024 to 65535.

A standard Linux package can be distributed without an APK. In that case,
download a trusted companion APK separately or build it, then select it in the
GUI or provide `--apk`.
See the [Android documentation](../android/README.md) for debug and release signing.
