# Privacy

Panelyra has no account system, advertising, analytics or cloud relay. Normal
display operation sends the additional monitor's picture directly to the
connected Android device over USB tethering or USB ADB.

## Data used by the application

- **Screen contents:** captured from the virtual monitor, compressed on the PC,
  transmitted to the tablet and decoded in memory. Normal operation does not
  save screenshots or video recordings.
- **Local connection details:** USB interface names, local IP addresses and, for
  ADB, device identifiers are used to select and validate the connection.
- **Preferences:** the Linux GUI saves settings in
  `$XDG_CONFIG_HOME/panelyra/settings.json` (normally
  `~/.config/panelyra/settings.json`) for the next launch. These include the
  selected APK path, USB interface, download-server port and time limit. The
  server starts only when requested. Delete this file while
  the app is closed to reset them.
- **Android language:** the PC can send a two-letter interface-language choice
  (`en` or `ru`) over the local USB/ADB connection. Android saves the last valid
  choice in its private app preferences. Before any choice is received, it uses
  its own system locale. Clear Panelyra's app data in Android settings to reset
  this preference. Language selection does not contact any online service.
- **News and update notices:** the Linux GUI keeps the automatic-check
  preference, cached notices, read markers and last-check time locally in
  `$XDG_CONFIG_HOME/panelyra/notifications.json` (normally
  `~/.config/panelyra/notifications.json`). Deleting that file while the app is
  closed clears the cache and read markers and resets that preference.
- **Connection state:** a private runtime lock records the sender process and
  its mode so GUI and CLI commands can share connection ownership. The normal
  location is `$XDG_RUNTIME_DIR/panelyra/stream.lock`; its data is cleared on exit.
- **Diagnostics:** the terminal and GUI can show local status/error logs,
  addresses, selected mode and optional sender performance counters. No automatic
  upload is implemented. Logs pasted into an issue become information you share
  with that issue's audience.

The Android app requests `INTERNET` because Android requires it for TCP,
including USB and loopback connections. It does not require camera, microphone,
contacts or storage access for video reception. It runs while its activity is
visible and keeps the screen awake during display.

## Network scope

The Android video receiver (TCP 27183) and language-control listener (TCP 27184)
bind to a detected USB address or ADB loopback, not Wi-Fi or all interfaces. Both
apply the same local peer restrictions. The optional APK server on Linux binds
to the USB address and serves the APK selected by the user. There is no application-level password
or encryption; use a direct cable and devices you trust. See the
[security policy](../SECURITY.md).

The APK server reads the selected file into memory for that server run. It serves
no directory listing or other files. Its bilingual landing page loads no remote
assets; language selection stays on the local server. The GUI shows successful
downloads in memory, without claiming that Android installed the file. Sharing
ends when stopped, when the main GUI closes, when its time limit expires or
when the selected USB connection is lost.

The Linux GUI can separately check GitHub for a newer stable version and
developer news. Once the publisher configures a public repository, automatic
checks run while the window is open, when due at startup and about every six
hours. They request public release metadata from `api.github.com` and the news
feed from `raw.githubusercontent.com` over HTTPS. GitHub sees ordinary network
request information, including the public IP address and a User-Agent identifying
Panelyra. No screen contents, logs, USB identifiers, account credentials,
analytics events or unique installation identifiers are sent in these requests.

Automatic checks can be disabled in the bell panel. Manual refresh still makes
an explicit request; clicking a notice's link opens its GitHub page in the
normal browser, subject to that browser's cookies and settings. No package is
automatically downloaded or installed. There is no background check service
when Panelyra is closed. The CLI sender and Android receiver do not fetch news.
No source is configured in the unpublished build, so it makes no update/news
requests at all. USB display sharing remains usable without Internet access.
See [updates and developer news](notifications.md) for details.

Installing dependencies, building Android with downloaded tools, using GitHub
or downloading updates from a distributor involves those external services.
Their data handling is separate from Panelyra's local display operation.

To stop sharing, stop the Linux stream, close the Android app or disconnect USB.
Closing a GUI window stops the stream that window started. Review diagnostic
logs before sharing them, and avoid moving sensitive windows onto the tablet
when another person can see it.
