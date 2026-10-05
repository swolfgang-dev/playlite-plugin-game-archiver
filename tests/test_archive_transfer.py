from plugin_test_support import require_plugin
require_plugin('PlayliteArchiver')
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from playlite_plugins.playlitearchiver.transfer import transfer_game


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.source = self.root / 'games' / 'Example'
        self.source.mkdir(parents=True)
        (self.source / 'game.exe').write_bytes(b'example game contents')
        (self.source / 'empty').mkdir()
        self.target = self.root / 'archive' / 'Example'
        self.game = {'Id': 'example', 'Name': 'Example', 'InstallDirectory': str(self.source),
                     'LutrisId': 12, 'Prefix': '/unchanged/prefix', 'Tags': ['RPG'], 'IsInstalled': True}
        (self.data / 'library.json').write_text(json.dumps([self.game]))

    def tearDown(self):
        self.directory.cleanup()

    def test_verified_archive_and_restore_preserve_provider_paths(self):
        games, _ = transfer_game(self.data, self.game, self.target)
        self.assertFalse(self.source.exists())
        self.assertEqual((self.target / 'game.exe').read_bytes(), b'example game contents')
        self.assertTrue((self.target / 'empty').is_dir())
        self.assertEqual(games[0]['InstallDirectory'], str(self.source))
        self.assertEqual(games[0]['LutrisId'], 12)
        self.assertEqual(games[0]['Prefix'], '/unchanged/prefix')
        self.assertIn('Archived', games[0]['Tags'])
        restored, _ = transfer_game(self.data, games[0], self.source, restore=True)
        self.assertFalse(self.target.exists())
        self.assertEqual((self.source / 'game.exe').read_bytes(), b'example game contents')
        self.assertNotIn('ArchivePath', restored[0])
        self.assertEqual(restored[0]['Tags'], ['RPG'])

    def test_collision_and_nested_destination_preserve_source(self):
        self.target.mkdir(parents=True)
        with self.assertRaises(ValueError):
            transfer_game(self.data, self.game, self.target)
        with self.assertRaises(ValueError):
            transfer_game(self.data, self.game, self.source / 'archive')
        self.assertTrue((self.source / 'game.exe').is_file())

    def test_playtime_changes_during_copy_are_preserved(self):
        import shutil
        from playlite.game_detection import record_playtime
        original = shutil.copytree
        def copy_with_history(*args, **kwargs):
            result = original(*args, **kwargs)
            if Path(args[0]) == self.source:
                record_playtime(self.data, [self.game], self.game['Id'], 30, 1, '2026-10-04T12:00:00+00:00')
            return result
        with patch('playlite_plugins.playlitearchiver.transfer.shutil.copytree', side_effect=copy_with_history):
            games, _ = transfer_game(self.data, self.game, self.target)
        self.assertEqual(games[0]['Playtime'], 30)
        self.assertEqual(games[0]['PlayCount'], 1)
        self.assertIn('ArchivePath', games[0])

    def test_deleted_entry_during_copy_keeps_source(self):
        import shutil
        original = shutil.copytree
        def copy_then_delete(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.data / 'library.json').write_text('[]')
            return result
        with patch('playlite_plugins.playlitearchiver.transfer.shutil.copytree', side_effect=copy_then_delete):
            with self.assertRaisesRegex(ValueError, 'Source kept'):
                transfer_game(self.data, self.game, self.target)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.target.exists())
        self.assertEqual(json.loads((self.data / 'library.json').read_text()), [])

    def test_symlinks_are_preserved_without_following_them(self):
        (self.source / 'link').symlink_to('game.exe')
        (self.source / 'broken').symlink_to('missing')
        games, _ = transfer_game(self.data, self.game, self.target)
        self.assertTrue((self.target / 'link').is_symlink())
        self.assertEqual((self.target / 'broken').readlink(), Path('missing'))
        transfer_game(self.data, games[0], self.source, restore=True)
        self.assertEqual((self.source / 'link').readlink(), Path('game.exe'))

    def test_cancellation_preserves_source_and_library(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(ValueError):
            transfer_game(self.data, self.game, self.target, cancel=cancelled)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(json.loads((self.data / 'library.json').read_text()), [self.game])

    def test_corrupt_destination_keeps_originals(self):
        import shutil
        original = shutil.copytree
        def corrupt(*args, **kwargs):
            result = original(*args, **kwargs)
            (Path(args[1]) / 'game.exe').write_bytes(b'corrupted')
            return result
        with patch('playlite_plugins.playlitearchiver.transfer.shutil.copytree', side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, 'verification failed'):
                transfer_game(self.data, self.game, self.target)
        self.assertEqual((self.source / 'game.exe').read_bytes(), b'example game contents')
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.target.parent.glob('*.partial-*')))

    def test_library_commit_failure_restores_quarantined_source(self):
        from playlite_plugins.playlitearchiver import transfer
        original = transfer.atomic_json
        def fail_library(path, value):
            if path.name == 'library.json':
                raise OSError('metadata write failed')
            return original(path, value)
        with patch.object(transfer, 'atomic_json', side_effect=fail_library):
            with self.assertRaises(OSError):
                transfer_game(self.data, self.game, self.target)
        self.assertTrue(self.source.is_dir())
        self.assertTrue(self.target.is_dir())
        self.assertEqual(json.loads((self.data / 'library.json').read_text()), [self.game])
        self.assertFalse(list(self.source.parent.glob('*.moving-*')))

    def test_destination_created_during_copy_is_not_replaced(self):
        import shutil
        original = shutil.copytree
        def collision(*args, **kwargs):
            result = original(*args, **kwargs)
            if Path(args[0]) == self.source:
                self.target.mkdir()
            return result
        with patch('playlite_plugins.playlitearchiver.transfer.shutil.copytree', side_effect=collision):
            with self.assertRaisesRegex(ValueError, 'Destination appeared'):
                transfer_game(self.data, self.game, self.target)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.target.is_dir())

    def test_interrupted_uncommitted_quarantine_is_recovered(self):
        from playlite_plugins.playlitearchiver.transfer import recover_transfers
        quarantine = self.source.with_name('interrupted')
        self.source.rename(quarantine)
        folder = self.data / 'archive-transfers'
        folder.mkdir()
        (folder / 'job.json').write_text(json.dumps(dict(id=self.game['Id'], source=str(self.source),
            target=str(self.target), quarantine=str(quarantine), restore=False)))
        recover_transfers(self.data)
        self.assertTrue(self.source.is_dir())
        self.assertFalse(quarantine.exists())

    def test_permissions_and_extended_attributes_survive_round_trip(self):
        import os
        file = self.source / 'game.exe'
        file.chmod(0o751)
        try:
            os.setxattr(file, 'user.playlite-test', b'preserved')
        except OSError:
            self.skipTest('Filesystem does not support user extended attributes')
        games, _ = transfer_game(self.data, self.game, self.target)
        copied = self.target / 'game.exe'
        self.assertEqual(copied.stat().st_mode & 0o777, 0o751)
        self.assertEqual(os.getxattr(copied, 'user.playlite-test'), b'preserved')
        transfer_game(self.data, games[0], self.source, restore=True)
        self.assertEqual(os.getxattr(file, 'user.playlite-test'), b'preserved')
