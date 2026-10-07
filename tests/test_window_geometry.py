"""Regression traces for client sizes and asynchronous WM state changes."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from usbdisplay import window_geometry as geometry
except (ImportError, ValueError):
    geometry = None


class Scheduler:
    def __init__(self):
        self.sources = {}
        self.serial = 0

    def add(self, kind, callback):
        self.serial += 1
        self.sources[self.serial] = (kind, callback)
        return self.serial

    def remove(self, source):
        self.sources.pop(source)

    def run(self, kind):
        for source, (queued_kind, callback) in list(self.sources.items()):
            if queued_kind == kind and source in self.sources:
                del self.sources[source]
                callback()


class Window:
    def __init__(self):
        self.state = 0
        self.size = (880, 820)
        self.requests = []
        self.handlers = {}

    def connect(self, signal, handler):
        self.handlers[signal] = handler

    def get_window(self):
        return self

    def get_state(self):
        return self.state

    def get_size(self):
        return self.size

    def resize(self, width, height):
        self.requests.append((width, height))


@unittest.skipIf(geometry is None, 'GDK 3 is not installed')
class WindowGeometryTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler()
        for name, replacement in (
                ('idle_add', lambda callback: self.scheduler.add('idle', callback)),
                ('timeout_add', lambda delay, callback: self.scheduler.add('timer', callback)),
                ('source_remove', self.scheduler.remove)):
            patcher = patch.object(geometry.GLib, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = Window()
        self.geometry = geometry.WindowGeometry(self.window, self.window.size)
        self.addCleanup(self.geometry.close)

    def state(self, flags):
        self.window.state = flags
        self.geometry.on_state(self.window, SimpleNamespace(new_window_state=flags))

    def configure(self, client_size, event_size=(2032, 1281)):
        self.window.size = client_size
        self.geometry.on_configure(self.window,
                                   SimpleNamespace(width=event_size[0], height=event_size[1]))

    def maximize(self):
        self.state(geometry.Gdk.WindowState.MAXIMIZED)
        self.configure((1920, 1111))
        self.scheduler.run('idle')

    def test_first_restore_rejects_the_shadow_subtraction_regression(self):
        self.configure((880, 820), (992, 990))
        self.scheduler.run('idle')
        self.maximize()
        # Actual GNOME 50 trace: the restore configure precedes unmaximize,
        # then CSD layout temporarily shrinks the original client dimensions.
        self.configure((880, 820), (880, 877))
        self.state(0)
        self.configure((773, 707), (885, 877))
        self.scheduler.run('idle')
        self.assertEqual(self.window.requests, [(880, 820)])
        self.assertEqual(self.geometry.normal_size, (880, 820))
        self.configure((880, 820), (992, 990))
        self.scheduler.run('timer')
        self.assertEqual(self.geometry.normal_size, (880, 820))
        self.assertFalse(self.geometry.restoring)

    def test_manual_resize_is_the_size_restored_next_time(self):
        self.configure((1040, 740))
        self.scheduler.run('idle')
        self.assertEqual(self.geometry.normal_size, (1040, 740))
        self.maximize()
        self.state(0)
        self.scheduler.run('idle')
        self.assertEqual(self.window.requests, [(1040, 740)])

    def test_configure_before_state_cannot_save_monitor_dimensions(self):
        self.configure((1920, 1111))
        self.state(geometry.Gdk.WindowState.MAXIMIZED)
        self.scheduler.run('idle')
        self.assertEqual(self.geometry.normal_size, (880, 820))

    def test_native_state_protects_size_even_before_state_signal(self):
        self.window.state = geometry.Gdk.WindowState.FULLSCREEN
        self.configure((1920, 1200))
        self.scheduler.run('idle')
        self.assertEqual(self.geometry.normal_size, (880, 820))

    def test_fullscreen_from_maximized_restores_only_after_return_to_normal(self):
        self.maximize()
        maximum = geometry.Gdk.WindowState.MAXIMIZED
        self.state(maximum | geometry.Gdk.WindowState.FULLSCREEN)
        self.configure((1920, 1200))
        self.state(maximum)
        self.scheduler.run('idle')
        self.assertEqual(self.window.requests, [])
        self.state(0)
        self.scheduler.run('idle')
        self.assertEqual(self.window.requests, [(880, 820)])

    def test_remaximize_before_restore_cancels_the_queued_resize(self):
        self.maximize()
        self.state(0)
        self.state(geometry.Gdk.WindowState.MAXIMIZED)
        self.scheduler.run('idle')
        self.scheduler.run('timer')
        self.assertEqual(self.window.requests, [])
        self.assertEqual(self.geometry.normal_size, (880, 820))

    def test_tiled_and_minimized_states_do_not_overwrite_normal_size(self):
        for state in (geometry.Gdk.WindowState.TILED, geometry.Gdk.WindowState.LEFT_TILED,
                      geometry.Gdk.WindowState.ICONIFIED):
            with self.subTest(state=state):
                self.state(state)
                self.configure((960, 1111))
                self.scheduler.run('idle')
                self.assertEqual(self.geometry.normal_size, (880, 820))
        self.state(0)
        self.scheduler.run('idle')
        self.assertEqual(self.window.requests, [(880, 820)])

    def test_compositor_constraint_is_respected_without_a_resize_loop(self):
        self.maximize()
        self.state(0)
        self.scheduler.run('idle')
        self.configure((800, 650))
        self.scheduler.run('timer')
        self.assertEqual(self.geometry.normal_size, (800, 650))
        self.assertEqual(self.window.requests, [(880, 820)])
        self.assertEqual(self.scheduler.sources, {})

    def test_user_resize_during_settling_is_not_undone(self):
        self.maximize()
        self.state(0)
        self.scheduler.run('idle')
        self.configure((880, 820))
        self.configure((1010, 770))
        self.scheduler.run('timer')
        self.assertEqual(self.geometry.normal_size, (1010, 770))
        self.assertEqual(self.window.requests, [(880, 820)])

    def test_destroy_cancels_callbacks_that_could_resize_a_closed_window(self):
        self.maximize()
        self.state(0)
        self.geometry.close()
        self.scheduler.run('idle')
        self.scheduler.run('timer')
        self.assertEqual(self.scheduler.sources, {})
        self.assertEqual(self.window.requests, [])

    def test_destroy_during_restore_cancels_the_settle_callback(self):
        self.maximize()
        self.state(0)
        self.scheduler.run('idle')
        self.assertTrue(self.scheduler.sources)
        self.geometry.close()
        self.assertEqual(self.scheduler.sources, {})


if __name__ == '__main__':
    unittest.main()
