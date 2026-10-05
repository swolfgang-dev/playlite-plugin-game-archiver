"""Folder archiving, restoration, and sequential selection batches."""
import json
from pathlib import Path
import threading
from PyQt6.QtCore import QObject, QSettings, QThreadPool, Qt, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QFormLayout, QLineEdit, QPlainTextEdit,
    QInputDialog, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox)
from playlite.providers import GenericPlugin, IntegrationPlugin
from playlite.lifecycle import run_dialog, show_warning, choose_directory


class Progress(QObject):
    changed = pyqtSignal(str)


class Plugin(GenericPlugin):
    def settings(self):
        return QSettings('Playlite', 'Archiver')

    def archive_roots(self):
        settings = self.settings()
        legacy = settings.value('archiveRoot', '')
        return settings.value('archiveRoots', [legacy] if legacy else [], type=list)

    def create_settings(self, parent=None):
        widget = QWidget(parent)
        form = QFormLayout(widget)
        widget.root = QLineEdit(self.settings().value('sourceRoot', str(Path.home() / 'Games')))
        widget.destinations = QPlainTextEdit('\n'.join(self.archive_roots()))
        row = QHBoxLayout()
        row.addWidget(widget.root)
        browse = QPushButton('Browse…')
        def pick_root():
            path = choose_directory(widget, 'Game library root', widget.root.text())
            if path:
                widget.root.setText(path)
        browse.clicked.connect(pick_root)
        row.addWidget(browse)
        form.addRow('Game library root (optional)', row)
        form.addRow('Archive locations (one per line)', widget.destinations)
        add = QPushButton('Add archive folder…')
        def pick_archive():
            path = choose_directory(widget, 'Archive folder', '')
            if path:
                current = widget.destinations.toPlainText().strip()
                widget.destinations.setPlainText(current + ('\n' if current else '') + path)
        add.clicked.connect(pick_archive)
        form.addRow(add)
        explanation = QLabel('Archives copy the entire installation folder, verify its files, permissions and links, then remove the original. Restore returns it to its original location. The optional library root preserves relative folder names; games outside it are supported too.')
        explanation.setWordWrap(True)
        form.addRow(explanation)
        return widget

    def save_settings(self, widget):
        root = widget.root.text().strip()
        roots = list(dict.fromkeys(line.strip() for line in widget.destinations.toPlainText().splitlines() if line.strip()))
        for value in ([root] if root else []) + roots:
            if not Path(value).is_absolute():
                raise ValueError('Archiver folders must be absolute Linux paths.')
            if Path(value).exists() and not Path(value).is_dir():
                raise ValueError('Archive locations must be folders.')
        settings = self.settings()
        settings.setValue('sourceRoot', root)
        settings.setValue('archiveRoots', roots)
        settings.sync()

    def game_actions(self, window, game):
        if game.get('ArchivePath'):
            return [('Restore game…', lambda: self.transfer(window, game, True))]
        if game.get('InstallDirectory'):
            return [('Archive game…', lambda: self.transfer(window, game, False))]
        return []

    def batch_game_actions(self, window, games):
        installed = [game for game in games if game.get('InstallDirectory') and not game.get('ArchivePath')]
        archived = [game for game in games if game.get('ArchivePath')]
        actions = []
        if installed:
            actions.append((f'Archive {len(installed)} games…', lambda: self.transfer_games(window, installed, False)))
        if archived:
            actions.append((f'Restore {len(archived)} games…', lambda: self.transfer_games(window, archived, True)))
        return actions

    def ensure_stopped(self, window, game):
        if window.game_detection.status(game['Id']) in ('Running', 'Launching'):
            raise ValueError('Stop the game before archiving or restoring it.')
        for provider in window.game_providers:
            if isinstance(provider, IntegrationPlugin) and provider.owns(game) and game['Id'] in provider.detect_running([game]):
                raise ValueError('Stop the game before archiving or restoring it.')

    def before_launch(self, window, game):
        if getattr(self, '_transferring', False):
            show_warning(window, 'Game Archiver', 'Wait for the archive or restore operation to finish.')
            return False
        if not game.get('ArchivePath'):
            return True
        return self.transfer_games(window, [game], True, confirm=False)

    def transfer(self, window, game, restore):
        return self.transfer_games(window, [game], restore)

    def transfer_games(self, window, games, restore, confirm=True):
        from .transfer import transfer_game
        from playlite.metadata_dialog import Task
        try:
            if getattr(self, '_transferring', False):
                raise ValueError('Another archive or restore operation is already running.')
            for game in games:
                self.ensure_stopped(window, game)
            root = self.settings().value('sourceRoot', '', type=str)
            chosen = None
            if not restore:
                roots = self.archive_roots()
                if not roots:
                    raise ValueError('Set an archive location in Settings → Plugins → General → Game Archiver first.')
                chosen, accepted = QInputDialog.getItem(window, 'Archive destination', 'Archive location', roots, editable=False)
                if not accepted:
                    return False
            jobs = []
            for game in games:
                if restore:
                    destination = game.get('ArchiveOriginalDirectory') or game.get('InstallDirectory')
                    if not destination:
                        raise ValueError('Set the original installation folder before restoring.')
                else:
                    source = Path(game['InstallDirectory']).resolve()
                    relative = source.relative_to(Path(root).resolve()) if root and source != Path(root).resolve() and source.is_relative_to(Path(root).resolve()) else Path(source.name)
                    destination = str(Path(chosen) / relative)
                jobs.append((dict(game), destination))
            title = 'Restore games' if restore else 'Archive games'
            if confirm and QMessageBox.question(window, title, f'{title} for {len(jobs)} selected game(s)?\n\nEach folder is copied to a temporary destination and verified before its original is removed.') != QMessageBox.StandardButton.Yes:
                return False
        except (ValueError, KeyError, OSError) as error:
            show_warning(window, 'Cannot transfer games', str(error))
            return False
        cancel = threading.Event()
        class TransferDialog(QDialog):
            busy = True
            def reject(self):
                if self.busy:
                    cancel.set()
                    button.setEnabled(False)
                    status.setText('Cancelling safely…')
                    return
                super().reject()
        dialog = TransferDialog(window)
        dialog.setProperty('playliteBackgroundJob', True)
        dialog.setWindowTitle(title)
        dialog.resize(760, 180)
        layout = QVBoxLayout(dialog)
        status = QLabel('Starting verified transfer…')
        status.setWordWrap(True)
        layout.addWidget(status)
        button = QPushButton('Cancel')
        button.clicked.connect(dialog.reject)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        updates = Progress(dialog)
        updates.changed.connect(status.setText)
        results = []
        # Recheck running state immediately before each transfer.
        def perform():
            from playlite.library_storage import library_lock
            for index, (snapshot, destination) in enumerate(jobs):
                if cancel.is_set():
                    results.extend((game['Name'], False, 'Skipped after cancellation.') for game, _ in jobs[index:])
                    break
                updates.changed.emit(f'{index + 1}/{len(jobs)}: {snapshot["Name"]}')
                try:
                    with library_lock(window.data):
                        current = next((entry for entry in json.loads((window.data / 'library.json').read_text()) if entry['Id'] == snapshot['Id']), None)
                    if current is None:
                        raise ValueError('Game is no longer in the library.')
                    self.ensure_stopped(window, current)
                    _, message = transfer_game(window.data, current, destination, restore, cancel,
                        lambda text, name=current['Name']: updates.changed.emit(name + ': ' + text))
                    results.append((current['Name'], True, message))
                except Exception as error:
                    results.append((snapshot['Name'], False, str(error)))
            return results
        task = Task(perform)
        success = []
        self._transferring = True
        def finished(result):
            window.games = json.loads((window.data / 'library.json').read_text())
            self._transferring = False
            dialog.busy = False
            window.update_filter_choices()
            window.refresh_library()
            success.append(all(ok for _, ok, _ in result))
            dialog.accept()
            problems = [f'{name}: {message}' for name, ok, message in result if not ok or 'redundant copy' in message]
            if problems:
                show_warning(window, 'Transfer results', '\n'.join(problems))
        def failed(error):
            self._transferring = False
            dialog.busy = False
            dialog.accept()
            show_warning(window, 'Transfer failed', error)
        task.signals.succeeded.connect(finished)
        task.signals.failed.connect(failed)
        dialog.finished.connect(lambda: cancel.set())
        dialog.task = task
        QThreadPool.globalInstance().start(task)
        run_dialog(dialog)
        return bool(success and success[0])
