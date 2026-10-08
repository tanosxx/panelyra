# Changelog

## 0.4.0 — unreleased

### Added

- English/Russian control for Internet access through the USB tablet, available
  while streaming. Turning it off removes the tablet's default routes and DNS
  contribution; turning it on restores the previous settings without reconnecting
  the display. Changes apply to the current USB connection, not the saved
  NetworkManager profile.
- Connection identity checks and versioned NetworkManager updates prevent a
  stale button state from changing a replaced connection. The previous settings
  remain available after reopening Panelyra during the same USB connection.

### Changed

- Reorganized the Linux window into **Connection** and **Settings** tabs with
  separate scrolling, keeping stream status and connect/stop controls visible
  below both tabs. Image, connection and general preferences have their own
  sections; the tablet Internet control is in **Settings → Connection**.
- Compact layout and wrapping labels prevent device details, Internet state,
  expanded sections and language changes from enlarging the window.
- Refreshed English/Russian Linux screenshots for the Connection tab, each
  settings section and Android app installation, with collapsible README
  galleries using sample device values.

## 0.3.0 — source preview, 2026-10-07

### Added

- Optional GNOME 50 extension to restore still-open tablet windows after unlock,
  including their position, size, workspace and maximized/fullscreen/minimized
  state. The installer preserves other extensions and backs up an older version;
  a newly installed extension requires one logout/login to load.
- Animated local lock screen with a glowing orb, drifting particles and the
  PC's local clock; a static lock image remains available when Cairo is missing.
- Automatic GNOME lock/unlock recovery without a Shell extension: a local opaque
  lock image keeps Android connected, capture restarts after unlocking, and the
  previous monitor layout is temporarily restored when the other displays still
  match. Individual windows can be restored with the optional extension above.
- Panelyra name and original icon for Linux and Android.
- Linux GUI with connection controls, video settings and English/Russian UI.
- Instant language switching without restarting the window or interrupting a stream.
- APK download server settings in the Linux GUI: file and USB-interface choice,
  port, time limit, start/stop, copyable tablet URL and download count. English/
  Russian download page with a language switch; sharing stops on expiry, USB
  disconnect or closing the main GUI.
- Android English/Russian interface follows the Linux GUI's language on
  connection and during its stream, with a persistent last choice and Android
  system-language fallback before the first sync. Optional CLI `start --language`
  and a separate control channel preserve UTD1 video compatibility.
- Restore the previous normal window size after maximizing or leaving fullscreen,
  including the first maximize cycle; manual window resizing remains available.
- Linux notification bell with unread counts, saved read state, optional periodic
  checks for stable GitHub releases and bilingual developer news, and manual
  refresh. No update installation or requests until a real source is configured.
- Offline news-feed validator and instructions for publishing announcements
  without rebuilding the application.
- Automatically refreshed USB device panel with hardware name, tethering state
  and tablet address; compact numeric buttons that do not overlap.
- Named CLI entry point, version information and connection status/stop commands.
- Linux packaging metadata and a local Debian package builder.
- English and Russian documentation, MIT license, contribution and security
  policies, issue templates and CI checks.
- Android companion branding while retaining the existing application ID.

### Preserved

- USB tethering without root or USB debugging, with optional ADB transport.
- Tested video defaults: 1600 × 1000, up to 30 transmitted frames/s, 60 Hz capture
  and constant quantization QP 18.
- Cursor capture workaround and cleanup of owned virtual monitor sessions.
- Compatibility entry points for existing USB Display users.

The redesigned Android build requires a fresh physical-device check before a
public release. App Center publication is not part of this unreleased entry.

## 0.2.0 — local prototype, 2026-10-06

- Created an additional GNOME Wayland monitor and transmitted H.264 over USB.
- Added the Java/MediaCodec Android receiver and optional ADB forwarding.
- Added USB-scoped APK installation, route diagnostics and temporary local-only
  tether configuration.
- Worked around a GNOME 50.1 cursor capture issue using `RecordMonitor`.
- Replaced constrained bitrate defaults with constant quality to stop visible
  changes in detail and color during motion on the tested Lenovo tablet.
- Improved video smoothness with 30 fps delivery and 60 Hz capture.
- Added the initial one-click Linux launcher.

This prototype was used locally; this entry does not imply a published release.
