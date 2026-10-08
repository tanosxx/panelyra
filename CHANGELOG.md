# Changelog

## 0.4.0 — unreleased

### Added

- Six independently composed animated lock scenes: an orbital planet, kinetic
  paper sculpture, violet light studio, aurora landscape, editorial analog
  clock and a perspective circuit grid. Each has its own typography, animation
  and landscape/portrait layout. Appearance includes a live lock-screen preview;
  applying a design updates the running sender for the next lock without
  reconnecting or updating Android. Unsaved previews remain local to the chooser.
- Local language changes also update the next lock screen immediately, even if
  the Android language-control channel is unavailable.
- A separate English/Russian **Appearance** page for the Linux window, with
  Panelyra original plus Light minimal, Dark studio, Aurora glass, Warm editorial
  and Graphite console. Selecting a design previews its colors and layout immediately;
  Apply saves it, while Cancel or leaving the page restores the saved design.
  Switching designs does not interrupt the display stream or require an Android
  update. Existing preferences keep the original design; unknown saved themes
  fall back to it without discarding otherwise valid video or APK-server preferences.
- English/Russian control for Internet access through the USB tablet, available
  while streaming. Turning it off removes the tablet's default routes and DNS
  contribution; turning it on restores the previous settings without reconnecting
  the display. Changes apply to the current USB connection, not the saved
  NetworkManager profile.
- Connection identity checks and versioned NetworkManager updates prevent a
  stale button state from changing a replaced connection. The previous settings
  remain available after reopening Panelyra during the same USB connection.

### Changed

- Restored each design's composition in the normal application window: Light
  minimal has a centered heading and airy vertical form; Dark studio has a navy
  sidebar and device workspace; Aurora glass uses a mint/lilac hero and glass
  panels; Warm editorial pairs large typography with cream and charcoal surfaces;
  Graphite console places the device beside compact controls.
- The five alternative designs include image settings and the tablet Internet
  control on **Connection**. Opening **Settings** shows the same controls there,
  preserving edits and the running connection.
- Appearance shows all six choices beside one preview, with **Interface** and
  **Lock screen** views, instead of a long gallery. Apply/Cancel remain visible.
- Reorganized the Linux window into **Connection** and **Settings** tabs with
  separate scrolling, keeping stream status and connect/stop controls visible
  below both tabs. Image, connection and general preferences have their own
  sections. The original design keeps the tablet Internet control in
  **Settings → Connection**.
- Compact layout and wrapping labels prevent device details, Internet state,
  expanded sections and language changes from enlarging the window.
- Refreshed English/Russian screenshots for all six Linux designs, both
  Appearance previews, each settings section and Android app installation.
  README galleries pair each design with a still frame of its lock animation;
  the main screenshot shows Aurora glass. Device values and clock times are
  examples; Panelyra original remains the default design.

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
