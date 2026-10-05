import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import importlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock
from types import SimpleNamespace
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMessageBox
from playlite.providers import discover_plugins
from playlite.app import LibraryWindow

APP = QApplication.instance() or QApplication([])


class ArchiverPluginTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.plugin = discover_plugins()['PlayliteArchiver']
        self.module = importlib.import_module(self.plugin.__class__.__module__)
        self.settings = QSettings(str(self.root / 'archiver.ini'), QSettings.Format.IniFormat)
        self.plugin.settings = lambda: self.settings

    def tearDown(self):
        self.directory.cleanup()

    def test_settings_folder_pickers_and_unconfigured_archive_locations(self):
        self.assertEqual(self.plugin.archive_roots(), [])
        widget = self.plugin.create_settings()
        self.plugin.save_settings(widget)  # Does not block saving unrelated settings.
        with patch.object(self.module, 'choose_directory', return_value=str(self.root / 'archives')):
            from PyQt6.QtWidgets import QPushButton
            next(button for button in widget.findChildren(QPushButton) if button.text().startswith('Add archive')).click()
        self.plugin.save_settings(widget)
        self.assertEqual(self.plugin.archive_roots(), [str(self.root / 'archives')])
        widget.root.setText('relative')
        with self.assertRaises(ValueError):
            self.plugin.save_settings(widget)

    def test_running_game_is_rejected_before_confirmation_or_copy(self):
        window = SimpleNamespace(game_detection=SimpleNamespace(status=lambda _: 'Running'), game_providers=[])
        with patch.object(self.module, 'show_warning') as warning, patch.object(self.module.QMessageBox, 'question') as question:
            self.assertFalse(self.plugin.transfer(window, dict(Id='a'), False))
            question.assert_not_called()
            self.assertIn('Stop the game', warning.call_args.args[2])

    def test_archive_and_restore_through_plugin_worker(self):
        source = self.root / 'games' / 'Example'
        source.mkdir(parents=True)
        (source / 'game.exe').write_bytes(b'example')
        data = self.root / 'data'
        data.mkdir()
        game = dict(Id='a', Name='Example', InstallDirectory=str(source), IsInstalled=True)
        (data / 'library.json').write_text(json.dumps([game]))
        self.settings.setValue('sourceRoot', str(source.parent))
        self.settings.setValue('archiveRoots', [str(self.root / 'archives')])
        window = LibraryWindow(data)
        window.game_detection.stop()
        with patch.object(self.module.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(
                self.module.QInputDialog, 'getItem', return_value=(str(self.root / 'archives'), True)):
            self.assertTrue(self.plugin.transfer(window, game, False))
            archived = window.games[0]
            self.assertFalse(source.exists())
            self.assertTrue(Path(archived['ArchivePath']).is_dir())
            self.assertEqual(self.plugin.game_actions(window, archived)[0][0], 'Restore game…')
            self.assertTrue(self.plugin.before_launch(window, archived))
            self.assertTrue(source.is_dir())
            self.assertTrue(window.games[0]['IsInstalled'])
            self.assertNotIn('ArchivePath', window.games[0])
        window.close()
