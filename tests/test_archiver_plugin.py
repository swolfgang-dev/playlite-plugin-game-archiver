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
        self.assertFalse(hasattr(widget, 'root'))
        self.assertFalse(self.settings.contains('sourceRoot'))
        widget.destinations.item(0, 0).setText('relative')
        with self.assertRaises(ValueError):
            self.plugin.save_settings(widget)

    def test_delete_archive_locations_removes_only_selected_entries(self):
        paths = [self.root / name for name in ('first', 'second', 'third')]
        for path in paths:
            path.mkdir()
            (path / 'keep.dat').write_bytes(b'archived data')
        self.settings.setValue('archiveRoots', [str(path) for path in paths])
        widget = self.plugin.create_settings()
        self.assertFalse(widget.delete_location.isEnabled())
        widget.destinations.item(0, 0).setSelected(True)
        widget.destinations.item(2, 0).setSelected(True)
        self.assertTrue(widget.delete_location.isEnabled())
        widget.delete_location.click()
        self.assertEqual(widget.destinations.rowCount(), 1)
        self.assertEqual(widget.destinations.item(0, 0).text(), str(paths[1]))
        # Changes are staged until the settings dialog is saved.
        self.assertEqual(self.plugin.archive_roots(), [str(path) for path in paths])
        self.plugin.save_settings(widget)
        self.assertEqual(self.plugin.archive_roots(), [str(paths[1])])
        for path in paths:
            self.assertEqual((path / 'keep.dat').read_bytes(), b'archived data')

    def test_adding_existing_location_selects_it_without_duplicate(self):
        path = str(self.root / 'archive')
        self.settings.setValue('archiveRoots', [path])
        widget = self.plugin.create_settings()
        from PyQt6.QtWidgets import QPushButton
        with patch.object(self.module, 'choose_directory', return_value=path):
            next(button for button in widget.findChildren(QPushButton) if button.text().startswith('Add archive')).click()
        self.assertEqual(widget.destinations.rowCount(), 1)
        self.assertTrue(widget.destinations.item(0, 0).isSelected())

    def test_running_game_is_rejected_before_confirmation_or_copy(self):
        window = SimpleNamespace(game_detection=SimpleNamespace(status=lambda _: 'Running'), game_providers=[])
        with patch.object(self.module, 'show_warning') as warning, patch.object(self.module.QMessageBox, 'question') as question:
            self.assertFalse(self.plugin.transfer(window, dict(Id='a'), False))
            question.assert_not_called()
            self.assertIn('Stop the game', warning.call_args.args[2])

    def test_archive_and_restore_through_plugin_worker(self):
        source = self.root / 'games' / 'Category' / 'Example'
        source.mkdir(parents=True)
        (source / 'game.exe').write_bytes(b'example')
        data = self.root / 'data'
        data.mkdir()
        game = dict(Id='a', Name='Example', InstallDirectory=str(source), IsInstalled=True)
        (data / 'library.json').write_text(json.dumps([game]))
        self.settings.setValue('sourceRoot', str(self.root / 'games'))
        self.settings.setValue('archiveRoots', [str(self.root / 'archives')])
        window = LibraryWindow(data)
        window.game_detection.stop()
        with patch.object(self.module.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(
                self.module.QInputDialog, 'getItem', return_value=(str(self.root / 'archives'), True)):
            self.assertTrue(self.plugin.transfer(window, game, False))
            archived = window.games[0]
            self.assertFalse(source.exists())
            self.assertEqual(archived['ArchivePath'], str(self.root / 'archives' / 'Example'))
            self.assertEqual(archived['ArchiveOriginalDirectory'], str(source))
            self.assertTrue(Path(archived['ArchivePath']).is_dir())
            self.assertEqual(self.plugin.game_actions(window, archived)[0][0], 'Restore game…')
            self.assertTrue(self.plugin.before_launch(window, archived))
            self.assertTrue(source.is_dir())
            self.assertTrue(window.games[0]['IsInstalled'])
            self.assertNotIn('ArchivePath', window.games[0])
        window.close()

    def test_batch_archives_multiple_games_and_restores_them(self):
        data = self.root / 'data'
        data.mkdir()
        games = []
        for name in ('First', 'Second'):
            source = self.root / 'games' / name
            source.mkdir(parents=True)
            (source / 'game.exe').write_bytes(name.encode())
            games.append(dict(Id=name, Name=name, InstallDirectory=str(source), IsInstalled=True))
        (data / 'library.json').write_text(json.dumps(games))
        self.settings.setValue('archiveRoots', [str(self.root / 'archives')])
        window = LibraryWindow(data)
        window.game_detection.stop()
        with patch.object(self.module.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(self.module.QInputDialog, 'getItem', return_value=(str(self.root / 'archives'), True)):
            self.assertTrue(self.plugin.transfer_games(window, games, False))
            self.assertTrue(all(game.get('ArchivePath') for game in window.games))
            self.assertTrue(self.plugin.transfer_games(window, list(window.games), True))
            self.assertTrue(all(not game.get('ArchivePath') for game in window.games))
        for game in games:
            self.assertTrue(Path(game['InstallDirectory']).is_dir())
        window.close()

    def test_batch_reports_one_collision_and_still_archives_other_games(self):
        data = self.root / 'data'
        data.mkdir()
        games = []
        for name in ('First', 'Second'):
            source = self.root / 'games' / name
            source.mkdir(parents=True)
            (source / 'game.exe').write_bytes(name.encode())
            games.append(dict(Id=name, Name=name, InstallDirectory=str(source)))
        (data / 'library.json').write_text(json.dumps(games))
        archives = self.root / 'archives'
        (archives / 'First').mkdir(parents=True)
        self.settings.setValue('archiveRoots', [str(archives)])
        window = LibraryWindow(data)
        window.game_detection.stop()
        with patch.object(self.module.QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(self.module.QInputDialog, 'getItem', return_value=(str(archives), True)), patch.object(self.module, 'show_warning') as warning:
            self.assertFalse(self.plugin.transfer_games(window, games, False))
            warning.assert_called_once()
        self.assertTrue(Path(games[0]['InstallDirectory']).is_dir())
        self.assertFalse(Path(games[1]['InstallDirectory']).exists())
        self.assertTrue(window.games[1].get('ArchivePath'))
        window.close()

    def test_play_restores_automatically_without_an_extra_confirmation(self):
        game = dict(Id='a', Name='Example', ArchivePath='/archive/Example')
        window = SimpleNamespace()
        with patch.object(self.plugin, 'transfer_games', return_value=True) as transfer, patch.object(self.module.QMessageBox, 'question') as question:
            self.assertTrue(self.plugin.before_launch(window, game))
            transfer.assert_called_once_with(window, [game], True, confirm=False)
            question.assert_not_called()
