"""Keep the client size while GTK changes Wayland window decorations.

GTK's first unmaximize can subtract the client-side shadows from the normal
size a second time. Configure events also contain those shadows, so their
width/height must never be remembered as the application's client size.
"""

import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib


NON_NORMAL = (Gdk.WindowState.MAXIMIZED | Gdk.WindowState.FULLSCREEN
              | Gdk.WindowState.ICONIFIED | Gdk.WindowState.TILED
              | Gdk.WindowState.TOP_TILED | Gdk.WindowState.RIGHT_TILED
              | Gdk.WindowState.BOTTOM_TILED | Gdk.WindowState.LEFT_TILED)


class WindowGeometry:
    """Remember normal sizes for this window's lifetime, allowing manual resize."""

    def __init__(self, window, initial_size):
        self.window = window
        self.normal_size = initial_size
        self.blocked = False
        self.restoring = False
        self.closed = False
        self.sample_source = self.restore_source = self.settle_source = None
        window.connect('configure-event', self.on_configure)
        window.connect('window-state-event', self.on_state)
        window.connect('destroy', self.close)

    def cancel(self, name):
        source = getattr(self, name)
        if source is not None:
            GLib.source_remove(source)
            setattr(self, name, None)

    def is_normal(self):
        native = self.window.get_window()
        return (not self.closed and not self.blocked and native is not None
                and not native.get_state() & NON_NORMAL)

    def remember(self):
        self.sample_source = None
        if self.is_normal() and not self.restoring:
            size = tuple(self.window.get_size())
            if min(size) > 1:
                self.normal_size = size
        return False

    def on_configure(self, *_):
        if self.is_normal():
            if self.restoring:
                self.settle()
            elif self.sample_source is None:
                # Sample after GTK has allocated decorations. A state event
                # in the same batch can cancel this before screen-sized data
                # replaces the user's last normal size.
                self.sample_source = GLib.idle_add(self.remember)
        return False

    def on_state(self, _window, event):
        blocked = bool(event.new_window_state & NON_NORMAL)
        if blocked == self.blocked or self.closed:
            return False
        self.blocked = blocked
        for name in ('sample_source', 'restore_source', 'settle_source'):
            self.cancel(name)
        self.restoring = not blocked
        if not blocked:
            # GTK must finish switching its CSD extents before resize().
            self.restore_source = GLib.idle_add(self.restore)
        return False

    def restore(self):
        self.restore_source = None
        if self.is_normal():
            self.window.resize(*self.normal_size)
            self.settle()
        return False

    def settle(self):
        self.cancel('settle_source')
        # Several configure events can accompany a single CSD transition.
        # Ignore them until allocation has settled; never force repeated
        # resizes, since a smaller monitor or a user resize must take effect.
        self.settle_source = GLib.timeout_add(200, self.finish_restore)

    def finish_restore(self):
        self.settle_source = None
        self.restoring = False
        self.remember()
        return False

    def close(self, *_):
        self.closed = True
        for name in ('sample_source', 'restore_source', 'settle_source'):
            self.cancel(name)
