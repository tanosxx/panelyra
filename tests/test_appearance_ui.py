"""Appearance previews must never persist until Apply is pressed."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    from usbdisplay.appearance_ui import AppearancePanel
    from usbdisplay.themes import THEMES
    from gtk_support import get_test_application
except (ImportError, ValueError):
    Gtk = None


@unittest.skipIf(Gtk is None, 'GTK 3 is not installed')
class AppearancePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = get_test_application()

    def setUp(self):
        self.app = SimpleNamespace(
            russian=False, settings=SimpleNamespace(theme='classic'),
            preview_theme=Mock(), commit_theme=Mock(return_value=False))
        self.app.tr = lambda en, ru: ru if self.app.russian else en
        self.panel = AppearancePanel(self.app)
        self.addCleanup(self.panel.widget.destroy)
        self.addCleanup(self.panel.close)

    def test_build_and_translation_do_not_apply_or_save_a_theme(self):
        self.assertEqual(set(self.panel.tiles), set(THEMES))
        self.assertEqual(len(self.panel.tiles), 6)
        self.assertEqual(self.panel.selected_theme, 'classic')
        self.assertFalse(self.panel.apply_button.get_sensitive())
        self.assertFalse(self.panel.cancel_button.get_sensitive())
        self.app.russian = True
        self.panel.render()
        self.assertEqual(self.panel.apply_button.get_label(), 'Применить')
        for theme_id, tile in self.panel.tiles.items():
            self.assertEqual(tile.name.get_text(), THEMES[theme_id].name[1])
        self.app.preview_theme.assert_not_called()
        self.app.commit_theme.assert_not_called()

    def test_tile_selection_previews_without_persisting(self):
        self.panel.tiles['light'].button.clicked()
        self.app.preview_theme.assert_called_once_with('light')
        self.app.commit_theme.assert_not_called()
        self.assertEqual(self.app.settings.theme, 'classic')
        self.assertEqual(self.panel.selected_theme, 'light')
        self.assertIn('Not saved', self.panel.status.get_text())
        self.assertEqual(self.panel.tiles['light'].badge.get_text(), 'Preview')
        self.assertEqual(self.panel.tiles['classic'].badge.get_text(), 'Current style')
        self.assertTrue(self.panel.tiles['light'].button.get_style_context().has_class('selected'))
        self.assertFalse(self.panel.tiles['classic'].button.get_style_context().has_class('selected'))
        self.assertTrue(self.panel.apply_button.get_sensitive())
        self.assertTrue(self.panel.cancel_button.get_sensitive())

    def test_cancel_restores_saved_theme_without_a_save(self):
        self.app.settings.theme = 'midnight'
        self.panel.cancel()
        self.app.preview_theme.reset_mock()
        self.panel.select('aurora')
        self.panel.cancel_button.clicked()
        self.assertEqual(self.app.preview_theme.call_args_list[0].args, ('aurora',))
        self.assertEqual(self.app.preview_theme.call_args_list[1].args, ('midnight',))
        self.assertEqual(self.panel.selected_theme, 'midnight')
        self.assertFalse(self.panel.apply_button.get_sensitive())
        self.app.commit_theme.assert_not_called()

    def test_apply_updates_saved_state_and_survives_later_cancel(self):
        def save(theme_id):
            self.app.settings.theme = theme_id
            return True
        self.app.commit_theme.side_effect = save
        self.panel.select('editorial')
        self.panel.apply_button.clicked()
        self.app.commit_theme.assert_called_once_with('editorial')
        self.assertEqual(self.panel.status.get_text(), 'Style saved')
        self.assertFalse(self.panel.apply_button.get_sensitive())
        self.app.preview_theme.reset_mock()
        self.panel.cancel()
        self.app.preview_theme.assert_not_called()
        self.assertEqual(self.panel.selected_theme, 'editorial')

    def test_failed_save_keeps_preview_pending_and_can_be_retried(self):
        self.panel.select('graphite')
        self.panel.apply()
        self.assertEqual(self.app.settings.theme, 'classic')
        self.assertEqual(self.panel.selected_theme, 'graphite')
        self.assertIn('Could not save', self.panel.status.get_text())
        self.assertTrue(self.panel.apply_button.get_sensitive())
        self.app.russian = True
        self.panel.render()
        self.assertIn('Не удалось сохранить', self.panel.status.get_text())
        self.panel.apply()
        self.assertEqual(self.app.commit_theme.call_count, 2)
        self.panel.cancel()
        self.assertEqual(self.panel.selected_theme, 'classic')
        self.assertNotIn('Не удалось', self.panel.status.get_text())

    def test_no_false_success_if_callback_has_not_updated_saved_settings(self):
        self.app.commit_theme.return_value = True
        self.panel.select('light')
        self.panel.apply()
        self.assertIn('Could not save', self.panel.status.get_text())
        self.assertTrue(self.panel.apply_button.get_sensitive())

    def test_same_or_invalid_choice_and_clean_apply_are_noops(self):
        self.panel.select('classic')
        self.panel.select('nonexistent-theme')
        self.panel.apply()
        self.panel.cancel()
        self.app.preview_theme.assert_not_called()
        self.app.commit_theme.assert_not_called()

    def test_close_restores_preview_once_and_ignores_later_events(self):
        self.panel.select('aurora')
        self.app.preview_theme.reset_mock()
        self.panel.close()
        self.panel.close()
        self.panel.select('graphite')
        self.panel.apply()
        self.panel.cancel()
        self.panel.render()
        self.app.preview_theme.assert_called_once_with('classic')
        self.app.commit_theme.assert_not_called()
        self.assertEqual(self.panel.selected_theme, 'classic')

    def test_responsive_choices_do_not_require_a_wide_window(self):
        self.panel.widget.show_all()
        minimum, _natural = self.panel.widget.get_preferred_width()
        # A choice list and one preview replace the tall card gallery. Both
        # fit side by side inside the normal 780 px application window.
        self.assertLessEqual(minimum, 560)
        self.assertEqual(len(self.panel.choices.get_children()), 6)
        self.assertEqual(self.panel.interface_preview.theme_id, 'classic')
        self.panel.select('aurora')
        self.assertEqual(self.panel.interface_preview.theme_id, 'aurora')
        self.assertEqual(self.panel.lock_preview.theme, 'aurora')


if __name__ == '__main__':
    unittest.main()
