#!/usr/bin/env python3
"""Render documentation screenshots from the real GTK widgets, using sample data.

Run with the system Python and a GTK display (or dbus-run-session + xvfb-run).
Only offscreen windows are built; no application activation, device discovery,
stream, download server, notification fetch or user-preference write is allowed.
The ready state illustrates the UI, not evidence of a successful display session.
"""

import argparse
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from usbdisplay import __version__, launcher
from usbdisplay.devices import Device
from usbdisplay.network import UsbLink
from usbdisplay.notifications import Source, Store
from usbdisplay.notifications_ui import NotificationCenter
from usbdisplay.settings import Settings


class ScreenshotWindow(Gtk.OffscreenWindow):
    # Offscreen surfaces have no native titlebar. Keep the real header widget
    # for prepare_window() instead of asking GDK to decorate a non-native window.
    def set_titlebar(self, header):
        self.header = header

    def get_titlebar(self):
        return self.header


def forbidden(*_args, **_kwargs):
    raise RuntimeError("Screenshot rendering must not access live services or preferences")


def settle():
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.005)


def prepare_window(window, width, height):
    # OffscreenWindow omits client-side window decorations. Reuse its actual
    # HeaderBar inside the offscreen surface, preserving the application UI.
    header = window.get_titlebar()
    window.set_titlebar(None)
    content = window.get_child()
    window.remove(content)
    frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    frame.pack_start(header, False, False, 0)
    frame.pack_start(content, True, True, 0)
    window.add(frame)
    window.set_size_request(width, height)
    window.show_all()
    # Avoid a focus underline/caret in a documentation still.
    window.set_focus(None)
    settle()


def capture(window, path):
    settle()
    pixels = window.get_pixbuf()
    if pixels is None:
        raise RuntimeError("GTK did not render the offscreen window")
    pixels.savev(str(path), "png", [], [])
    print(f"{path.name}: {pixels.get_width()} × {pixels.get_height()}")


def reveal_card(app, control):
    scroll = app.pages.get_child_by_name("settings")
    content = scroll.get_child().get_child()
    card = control
    while card.get_parent() is not content:
        card = card.get_parent()
        if card is None:
            raise RuntimeError("Settings card is no longer in the screenshot page")
    settle()
    _, top = card.translate_coordinates(content, 0, 0)
    adjustment = scroll.get_vadjustment()
    adjustment.set_value(min(top, adjustment.get_upper() - adjustment.get_page_size()))
    settle()


def render_language(language, output, temporary):
    settings = Settings(language=language, auto_connect=False,
                        apk_path=f"panelyra-{__version__}-android-debug.apk",
                        apk_interface="usb0")
    source = Source("")
    store = Store(path=temporary / f"notifications-{language}.json", source=source)
    with ExitStack() as guards:
        guards.enter_context(patch.object(launcher, "load", return_value=(settings, None)))
        guards.enter_context(patch.object(launcher, "save", side_effect=forbidden))
        guards.enter_context(patch.object(launcher, "WindowGeometry"))
        guards.enter_context(patch.object(launcher, "NotificationCenter", side_effect=lambda app:
                             NotificationCenter(app, source=source, store=store, fetcher=forbidden)))
        guards.enter_context(patch.object(Gtk, "ApplicationWindow", side_effect=lambda **_:
                             ScreenshotWindow()))
        for name in ("start", "refresh_devices", "save_apk_settings"):
            guards.enter_context(patch.object(launcher.Launcher, name, side_effect=forbidden))
        for target in ("usbdisplay.network.discover_links", "usbdisplay.devices.discover_devices",
                       "usbdisplay.internet.InternetController.inspect",
                       "usbdisplay.internet.InternetController.set_enabled",
                       "usbdisplay.download.DownloadServer.start"):
            guards.enter_context(patch(target, side_effect=forbidden))

        app = launcher.Launcher(auto_connect=False)
        app.build_window()
        try:
            app.on_devices([Device("Android tablet", "usb", "usb-ready", "usb0", "192.0.2.2")], None)
            app.internet.state = SimpleNamespace(enabled=False)
            app.internet.render()
            app.pages.set_transition_duration(0)
            prepare_window(app.window, 780, 760)
            capture(app.window, output / f"linux-{language}.png")

            app.pages.set_visible_child_name("settings")
            reveal_card(app, app.transport)
            capture(app.window, output / f"linux-settings-network-{language}.png")
            # Show the capture-rate option as part of the picture-settings view.
            app.capture.get_parent().get_parent().set_expanded(True)
            reveal_card(app, app.profile)
            capture(app.window, output / f"linux-settings-picture-{language}.png")
            reveal_card(app, app.language)
            capture(app.window, output / f"linux-settings-general-{language}.png")

            with patch.object(Gtk, "Window", side_effect=lambda **_: ScreenshotWindow()):
                app.downloads.build_window()
            app.downloads.links = [UsbLink("usb0", "192.0.2.1", 24, "192.0.2.2")]
            app.downloads.render_interfaces()
            app.downloads.render()
            prepare_window(app.downloads.window, 700, 900)
            capture(app.downloads.window, output / f"linux-android-app-{language}.png")
        finally:
            app.notifications.close()
            app.internet.close()
            app.downloads.close()
            app.window_geometry.close()
            app.window.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/images")
    args = parser.parse_args()
    if not Gtk.init_check(None)[0]:
        parser.exit(1, "GTK display unavailable; run inside a desktop session or xvfb-run.\n")
    Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="panelyra-screenshots-") as temporary:
        for language in ("en", "ru"):
            render_language(language, args.output_dir, Path(temporary))


if __name__ == "__main__":
    main()
