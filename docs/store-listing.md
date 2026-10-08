# Panelyra — store listing kit

Copy for a future application store submission. This is **not a published
listing**. Fill in actual publisher, repository, support and screenshot links
before submission; publication status must remain accurate.

## Identity

| Field | Value |
| --- | --- |
| Display name | Panelyra |
| Requested package/store name | `panelyra` (availability must be checked when registering) |
| Linux application ID | `io.github.tanosx.Panelyra` (confirm namespace ownership) |
| Author | TanosX |
| Initial release | 0.4.0 |
| Application / artwork license | MIT |
| AppStream metadata license | MIT |
| Suggested Snap categories | Utilities, Productivity |
| Desktop category | Utility |
| Icon source | `usbdisplay/assets/panelyra.svg` |
| Store icon | `usbdisplay/assets/panelyra-512.png` |
| Homepage, support, source URL | Add real public links before publication |

The working name is distinctive, but a web search is not a trademark clearance
or a Snap Store name reservation. Confirm the namespace before the first
public release so users do not need an application ID migration later.

## English copy

**Summary:** Extend your GNOME desktop to an Android tablet over USB

**Description:**

Panelyra gives your Android tablet a second job: an extra screen for your
GNOME desktop. Connect a USB cable, open the Android receiver and move your
windows onto the tablet. Continue using your computer's mouse and keyboard.

- Use the Linux desktop app or control the same engine from the command line.
- Connect through USB tethering without enabling Android developer mode.
- Use USB debugging with ADB as an alternative connection method.
- Adjust resolution, frame rate and video quality to suit your tablet.
- Work locally, without an account or a cloud service.

The tested setup is Ubuntu 26.04, GNOME 50 on Wayland and Lenovo Tab M10 FHD
Plus (TB-X606X) running Android 10. The default video mode is 1600 × 1000,
up to 30 frames per second with 60 Hz desktop capture. Performance depends on
your computer, USB connection and Android decoder.

Install the Panelyra Android receiver separately. Keep the tablet unlocked and
the receiver open. Panelyra currently sends video only: audio and tablet touch
input are not supported. Other desktop environments and Android models have
not been qualified. The video is compressed and does not replace a direct
HDMI or DisplayPort connection without any loss.

## Русский текст

**Краткое описание:** Android-планшет как второй экран GNOME по USB

**Описание:**

Panelyra превращает Android-планшет в дополнительный экран рабочего стола
GNOME. Подключите USB-кабель, откройте Android-приёмник и перенесите на планшет
нужные окна. Мышь и клавиатура остаются подключены к компьютеру.

- Графическое приложение для Linux и CLI с общим движком передачи.
- Подключение через USB-модем без режима разработчика Android.
- Альтернативное подключение через USB-отладку и ADB.
- Настройка разрешения, частоты кадров и качества для вашего планшета.
- Работа без учётной записи и облачного сервиса.

Проверенная конфигурация: Ubuntu 26.04, GNOME 50 в сеансе Wayland и Lenovo
Tab M10 FHD Plus (TB-X606X) с Android 10. По умолчанию изображение передаётся
в разрешении 1600 × 1000 с частотой до 30 кадров/с; захват рабочего стола
работает на 60 Гц. Плавность зависит от компьютера, USB и декодера планшета.

Android-приёмник Panelyra устанавливается отдельно. Планшет должен оставаться
разблокированным, а приложение — открытым. Звук и касания пока не передаются.
Другие рабочие столы и модели Android пока не проверены. Изображение сжимается
и не полностью повторяет прямое подключение монитора по HDMI или DisplayPort.

## Screenshots and media

Capture the actual Linux application, not a mock-up or a screenshot from
another operating system. Recommended shots:

1. The main window with connection guidance and the Panelyra icon.
2. A connected session showing the chosen quality settings. Take this only
   with a real connected tablet; do not fabricate a successful connection.
3. The Linux desktop and Android receiver side by side, with personal content
   removed from the scene before capture.

Use captions that describe what is visible. Do not include personal desktop
documents, account names, serial numbers or private network details in public
screenshots. Store screenshot URLs must be stable HTTPS links to the actual
files; fill them into MetaInfo only after hosting is available.

For Snap Store, use a square icon no larger than 512 × 512 and 256 KB. Up to
five screenshots can be used; target PNG images between 480 × 480 and
3840 × 2160, with aspect ratios from 1:2 to 2:1 and files below 2 MB. Check the
publisher dashboard's current limits at submission time. Canonical's
[listing guidance](https://forum.snapcraft.io/t/store-listing-and-branding/16397)
also describes summaries, categories and contact links.

## Permissions and privacy copy

Panelyra captures a virtual monitor inside the user's active GNOME session
and transmits the image to the tablet. Windows placed on that monitor become
visible on the tablet. The application uses a local USB network connection or
an ADB USB tunnel. It does not require a cloud account or collect telemetry.
Transport authentication and encryption limitations are described in
[the security documentation](../SECURITY.md); use trusted USB devices and
do not expose the receiver or APK download service to the Internet.

The Linux notification bell can also check public GitHub release metadata and
developer news over HTTPS while the app is open. These optional requests expose
the public IP address and a Panelyra User-Agent to GitHub; they do not
include screen contents or device identifiers. Automatic checks can be disabled,
and packages are never installed automatically. The unpublished build has no
configured news source and makes no such requests. The public listing must match
the configuration actually shipped; see [privacy](privacy.md).

The optional `configure-usb` action temporarily changes the active USB
connection's default-route and DNS settings through NetworkManager so the
computer can keep using its normal Internet connection. It does not edit the
saved network profile. The change lasts until the USB connection is recreated.

Do not claim the app is sandboxed when distributing an ordinary `.deb` or a
classic snap. Do not claim Canonical, GNOME, Google or Lenovo endorsement.

## Submission checklist

- Confirm ownership of the publisher account and the chosen project namespace.
- Reserve the available store name and replace all unpublished link fields.
- Provide real support and source-code URLs; set the MIT license.
- Publish real screenshots and validate AppStream without preview overrides.
- Complete the package and device tests in [distribution.md](distribution.md).
- For Snap, obtain required confinement/interface approvals and verify a clean
  installation before promoting beyond a test channel.
- State supported GNOME/Ubuntu versions, separate Android installation and
  limitations prominently in the final listing.
- Keep checksums, release notes and corresponding sources available for each
  published binary. Do not attach backups, development tools or signing keys.
