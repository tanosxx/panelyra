"""The lock illustration is local, reversible, and idle when it is hidden."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    import cairo
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import GLib, Gtk
    from usbdisplay.appearance_ui import AppearancePanel
    from usbdisplay.lock_preview import LockPreview
    from usbdisplay.themes import THEME_IDS
    from gtk_support import get_test_application
except (ImportError, ValueError):
    Gtk = None


@unittest.skipIf(Gtk is None, 'GTK 3 is not installed')
class LockPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = get_test_application()

    def make_preview(self):
        preview = LockPreview()
        self.addCleanup(preview.destroy)
        self.addCleanup(preview.close)
        return preview

    @staticmethod
    def flush_events():
        for _ in range(100):
            if not Gtk.events_pending():
                break
            Gtk.main_iteration_do(False)

    def test_scene_cache_is_lazy_and_uses_selected_theme_and_language(self):
        preview = self.make_preview()
        with patch('usbdisplay.lock_preview.LockScene') as scene_factory:
            preview.set_style('aurora', 'ru')
            scene_factory.assert_not_called()
            first = preview.scene()
            self.assertIs(preview.scene(), first)
            scene_factory.assert_called_once_with(800, 500, language='ru', theme='aurora')
            preview.set_style('editorial', 'en')
            preview.scene()
            preview.set_style('aurora', 'ru')
            self.assertIs(preview.scene(), first)
            self.assertEqual(scene_factory.call_count, 2)

    def test_scene_cache_does_not_grow_for_every_selection(self):
        preview = self.make_preview()
        with patch('usbdisplay.lock_preview.LockScene') as scene_factory:
            for theme in ('classic', 'light', 'midnight', 'aurora', 'editorial', 'graphite'):
                preview.set_style(theme, 'en')
                preview.scene()
            self.assertEqual(scene_factory.call_count, 6)
            self.assertEqual(len(preview.scenes), 3)
            preview.close()
            self.assertFalse(preview.scenes)

    def test_no_timer_until_both_mapped_and_expanded(self):
        preview = self.make_preview()
        with patch.object(preview, 'get_mapped', return_value=False), \
                patch('usbdisplay.lock_preview.GLib.timeout_add') as add:
            preview.set_active(True)
            preview.on_map()
            add.assert_not_called()
        preview.set_active(False)
        with patch.object(preview, 'get_mapped', return_value=True), \
                patch('usbdisplay.lock_preview.GLib.timeout_add', return_value=77) as add, \
                patch('usbdisplay.lock_preview.GLib.source_remove') as remove:
            preview.on_map()
            add.assert_not_called()
            preview.set_active(True)
            preview.on_map()
            add.assert_called_once_with(100, preview.tick)
            self.assertEqual(preview.source_id, 77)
            preview.set_active(False)
            remove.assert_called_once_with(77)
            self.assertIsNone(preview.source_id)

    def test_real_map_unmap_and_destroy_clean_up_the_animation(self):
        preview = self.make_preview()
        window = Gtk.OffscreenWindow()
        expander = Gtk.Expander()
        expander.add(preview)
        expander.connect('notify::expanded',
                         lambda *_: preview.set_active(expander.get_expanded()))
        window.add(expander)
        self.addCleanup(window.destroy)
        with patch('usbdisplay.lock_preview.LockScene'):
            window.show_all()
            self.flush_events()
            self.assertFalse(preview.enabled)
            self.assertIsNone(preview.source_id)
            expander.set_expanded(True)
            self.flush_events()
            self.assertTrue(preview.get_mapped())
            self.assertIsNotNone(preview.source_id)
            window.hide()
            self.flush_events()
            self.assertIsNone(preview.source_id)
            window.show_all()
            self.flush_events()
            self.assertIsNotNone(preview.source_id)
            expander.set_expanded(False)
            self.flush_events()
            self.assertIsNone(preview.source_id)
            expander.set_expanded(True)
            self.flush_events()
            self.assertIsNotNone(preview.source_id)
            preview.destroy()
            self.assertTrue(preview.closed)
            self.assertIsNone(preview.source_id)

    def test_tick_stops_when_visibility_changes(self):
        preview = self.make_preview()
        preview.enabled = True
        preview.source_id = 91
        with patch.object(preview, 'get_mapped', return_value=False):
            self.assertEqual(preview.tick(), GLib.SOURCE_REMOVE)
            self.assertIsNone(preview.source_id)

    def test_render_uses_live_clock_and_letterboxes_the_real_scene(self):
        preview = self.make_preview()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 640, 240)
        with patch('usbdisplay.lock_preview.LockScene') as scene_factory, \
                patch.object(preview, 'get_allocated_width', return_value=640), \
                patch.object(preview, 'get_allocated_height', return_value=240):
            matrices = []
            scene_factory.return_value.render.side_effect = (
                lambda ctx, elapsed: matrices.append(ctx.get_matrix()))
            preview.on_draw(preview, cairo.Context(surface))
            render = scene_factory.return_value.render
            render.assert_called_once()
            # The renderer receives no fake datetime; its live default is used.
            self.assertEqual(len(render.call_args.args), 2)
            self.assertEqual(render.call_args.kwargs, {})
            self.assertGreaterEqual(render.call_args.args[1], 0)
            self.assertFalse(preview.failed)
            self.assertEqual(matrices[0].xx, matrices[0].yy)
            self.assertAlmostEqual(matrices[0].xx * 800, 384)
            self.assertAlmostEqual(matrices[0].x0, (640 - 384) / 2)

    def test_draw_error_falls_back_once_and_can_recover_on_new_style(self):
        preview = self.make_preview()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 640, 400)
        with patch('usbdisplay.lock_preview.LockScene') as scene_factory, \
                patch.object(preview, 'get_allocated_width', return_value=640), \
                patch.object(preview, 'get_allocated_height', return_value=400):
            scene_factory.return_value.render.side_effect = RuntimeError('No scene')
            preview.on_draw(preview, cairo.Context(surface))
            preview.on_draw(preview, cairo.Context(surface))
            self.assertTrue(preview.failed)
            self.assertIsNone(preview.source_id)
            self.assertEqual(scene_factory.return_value.render.call_count, 1)
            surface.flush()
            self.assertTrue(all(alpha == 255 for alpha in bytes(surface.get_data())[3::4]))
            preview.set_style('light', 'ru')
            self.assertFalse(preview.failed)
            scene_factory.return_value.render.side_effect = None
            preview.on_draw(preview, cairo.Context(surface))
            self.assertFalse(preview.failed)

    def test_actual_scenes_render_in_both_languages(self):
        preview = self.make_preview()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 640, 400)
        with patch.object(preview, 'get_allocated_width', return_value=640), \
                patch.object(preview, 'get_allocated_height', return_value=400):
            for language in ('en', 'ru'):
                for theme in THEME_IDS:
                    with self.subTest(theme=theme, language=language):
                        preview.set_style(theme, language)
                        preview.on_draw(preview, cairo.Context(surface))
                        self.assertFalse(preview.failed)

    def test_panel_previews_selection_language_and_cancel_without_saving(self):
        app = SimpleNamespace(russian=False, settings=SimpleNamespace(theme='classic'),
                              preview_theme=Mock(), commit_theme=Mock())
        app.tr = lambda en, ru: ru if app.russian else en
        panel = AppearancePanel(app)
        self.addCleanup(panel.widget.destroy)
        self.addCleanup(panel.close)
        self.assertEqual(panel.preview_stack.get_visible_child_name(), 'interface')
        self.assertFalse(panel.lock_preview.enabled)
        panel.select('graphite')
        self.assertEqual(panel.lock_preview.theme, 'graphite')
        self.assertEqual(app.settings.theme, 'classic')
        app.commit_theme.assert_not_called()
        app.russian = True
        panel.render()
        self.assertEqual(panel.lock_preview.language, 'ru')
        self.assertEqual(panel.preview_stack.child_get_property(panel.lock_preview, 'title'), 'Заставка')
        panel.preview_stack.set_visible_child_name('lock')
        self.assertTrue(panel.lock_preview.enabled)
        panel.cancel()
        self.assertEqual(panel.lock_preview.theme, 'classic')
        app.commit_theme.assert_not_called()
        panel.close()
        self.assertTrue(panel.lock_preview.closed)
        self.assertIsNone(panel.lock_preview.source_id)

    def test_leaving_appearance_stops_timer_and_both_preview_modes_stay_compact(self):
        app = SimpleNamespace(russian=True, settings=SimpleNamespace(theme='classic'),
                              preview_theme=Mock(), commit_theme=Mock())
        app.tr = lambda en, ru: ru if app.russian else en
        panel = AppearancePanel(app)
        stack = Gtk.Stack()
        stack.set_transition_duration(0)
        stack.add_named(panel.widget, 'appearance')
        stack.add_named(Gtk.Label(label='Connection'), 'connection')
        window = Gtk.OffscreenWindow()
        window.add(stack)
        self.addCleanup(window.destroy)
        self.addCleanup(panel.close)
        window.show_all()
        panel.preview_stack.set_visible_child_name('lock')
        self.flush_events()
        self.assertIsNotNone(panel.lock_preview.source_id)
        panel.preview_stack.set_visible_child_name('interface')
        self.flush_events()
        self.assertIsNone(panel.lock_preview.source_id)
        panel.preview_stack.set_visible_child_name('lock')
        self.flush_events()
        self.assertIsNotNone(panel.lock_preview.source_id)
        for theme in THEME_IDS:
            panel.select(theme)
            minimum, _natural = panel.widget.get_preferred_width()
            self.assertLessEqual(minimum, 560)
        stack.set_visible_child_name('connection')
        self.flush_events()
        self.assertIsNone(panel.lock_preview.source_id)
        stack.set_visible_child_name('appearance')
        self.flush_events()
        self.assertIsNotNone(panel.lock_preview.source_id)
        app.commit_theme.assert_not_called()


if __name__ == '__main__':
    unittest.main()
