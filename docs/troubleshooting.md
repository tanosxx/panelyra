# Troubleshooting

[Home](../README.md) · [User guide](usage.md)

Start with `./panelyra doctor --transport usb` from a terminal in your GNOME
session. For ADB, replace `usb` with `adb`. A disconnected tablet is expected to
fail connection checks; it does not mean the installed program is broken.

## The tablet is connected, but no USB link is found

Check that the cable supports data and **USB tethering** is enabled on Android.
Charging mode and MTP file transfer alone do not provide the required network.
Open the Android app and confirm that it shows a USB address. Refresh or reopen
it after changing the USB mode.

If Android reports ADB mode while tethering is enabled, the firmware may use an
unrecognized interface name. Include the tablet model and Android version in a
bug report. Do not expose the server on all interfaces as a workaround.

On Linux, `ip -brief address` and `nmcli device status` show active interfaces.
If there are multiple candidates, explicitly set `--interface` and `--tablet-ip`.
NCM/CDC Ethernet requires an explicit interface. See [USB selection](usage.md#usb-tethering).

## USB tethering interrupts the PC's Internet access

Open **Settings → Connection → Internet via tablet** and click **Turn off**. The same
button can turn it back on without stopping the display. Alternatively, run
`./panelyra configure-usb` with the tablet connected. This temporarily removes
the tether's Internet route and DNS contribution without disconnecting the
display link. It can be repeated after reconnecting. On machines without
NetworkManager, configure the USB link's routing in that machine's network
manager instead; do not remove the connected USB subnet route.

## ADB says unauthorized, offline or no device

Unlock Android and approve the computer's debugging authorization. Check
`adb devices -l`. For no device, check the cable, USB port and USB debugging
setting. If debugging cannot be enabled on this firmware, use USB tethering;
Panelyra does not require developer mode for that transport.

## Screen capture is unavailable

Panelyra's current backend needs a logged-in **GNOME Wayland** desktop and access
to its session D-Bus and PipeWire. Check:

```bash
echo "$XDG_SESSION_TYPE"
echo "$XDG_CURRENT_DESKTOP"
./panelyra doctor
```

Do not launch it with `sudo`, from a headless SSH session or a service account.
The current backend does not support X11 or KDE. While GNOME is locked, Panelyra
shows a local lock placeholder on the tablet and waits. It recreates capture
automatically after unlocking; no Android update or GNOME extension is needed.
Stop/closing the Linux app still ends the connection while locked. USB removal,
Android backgrounding or computer suspension can break the connection and may
require a manual reconnect.

GNOME removes this virtual monitor on lock, so it may move its windows onto the
main display. Panelyra restores the monitor layout when the other connected
displays and modes still match; it does not restore individual window positions.
After updating the Linux source, stop and reconnect once to load the new sender.

For missing `gi` or GStreamer elements, install the dependencies in the
[README](../README.md#install-from-source). Use `/usr/bin/python3`; another Python
environment may not see Ubuntu's PyGObject installation.

## A desktop appears, but the cursor is frozen

The default backend contains a GNOME 50.1 cursor workaround. Do not use
`--no-cursor-workaround` for everyday work on the tested system. Confirm that you
are running the current source and that no second old sender is running.
See the [architecture](architecture.md#cursor-and-capture) for implementation details.

## Video is slow or quality changes during movement

Keep constant quality enabled (`--rate-control quality --quality 18`) and try
1280 × 800. The tested Lenovo default is 1600 × 1000 with `--fps 30
--capture-fps 60`. Higher transmission rates can overload an old decoder; more
fps is not automatically smoother.

Use `--stats` while playing the same moving video for 20–30 seconds. A low rate
on a motionless desktop is normal. Sender counters distinguish capture/send
behavior, but cannot establish the tablet's display latency.

If colors and detail vary at each scene change, check that bitrate mode has not
been selected. A small fixed bitrate budget can force visible changes in
compression. For remaining problems, compare a synthetic stream:

```bash
./panelyra start --test-pattern --duration 10
```

This sends a test image to the tablet without creating a monitor. Stop any
existing stream before running it.

## Android reports a decoder error

Try 1280 × 800, then 1024 × 640, and reconnect. Keep Panelyra visible and the
tablet unlocked. The physical screen's native resolution is not proof of the
decoder's supported H.264 size or level. Include the exact decoder error and
mode in a report; a successful PC-only smoke test does not verify that decoder.

## A new APK will not install over the old one

Android updates require the same application ID and signing certificate. The
project keeps `dev.usbdisplay.client` for upgrade compatibility, but a build made
with a different debug key or a release key cannot update an existing debug-key
installation. Preserve your original key for local updates, or intentionally
uninstall the old app before installing the differently signed build. Uninstalling
removes that app's local data. See [Android signing](../android/README.md).

## What to include in a bug report

Include the app version, Linux/GNOME/Android versions, device model, transport,
video mode and whether the problem also occurs with the test pattern. Add the
relevant log excerpt after removing serial numbers, addresses, usernames and
unrelated private content. State whether the picture, cursor or whole application
freezes; these have different causes.
