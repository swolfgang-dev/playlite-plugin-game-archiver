from pathlib import Path
import threading
from PyQt6.QtCore import QSettings, QThreadPool
from PyQt6.QtWidgets import QWidget, QFormLayout, QLineEdit, QPlainTextEdit, QInputDialog, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox
from playlite.providers import GenericPlugin
from playlite.lifecycle import run_dialog, show_warning, choose_directory


class Plugin(GenericPlugin):
    def archive_roots(self):
        settings = self.settings()
        legacy = settings.value('archiveRoot', '')
        return settings.value('archiveRoots', [legacy] if legacy else [], type=list)

    def settings(self):
        return QSettings('Playlite', 'Archiver')

    def create_settings(self, parent=None):
        widget = QWidget(parent)
        form = QFormLayout(widget)
        widget.root = QLineEdit(self.settings().value('sourceRoot', str(Path.home() / 'Games')))
        settings = self.settings()
        roots = self.archive_roots()
        widget.destinations = QPlainTextEdit('\n'.join(roots))
        root_row = QHBoxLayout()
        root_row.addWidget(widget.root)
        browse = QPushButton('Browse…')
        def pick_root():
            folder = choose_directory(widget, 'Game library root', widget.root.text())
            if folder:
                widget.root.setText(folder)
        browse.clicked.connect(pick_root)
        root_row.addWidget(browse)
        form.addRow('Game library root', root_row)
        form.addRow('Archive folders (one per line)', widget.destinations)
        add_folder = QPushButton('Add archive folder…')
        def pick_archive():
            folder = choose_directory(widget, 'Archive folder', '')
            if folder:
                current = widget.destinations.toPlainText().strip()
                widget.destinations.setPlainText(current + ('\n' if current else '') + folder)
        add_folder.clicked.connect(pick_archive)
        form.addRow(add_folder)
        description = QLabel('Moves games into an archive after copying and verifying their files. Restore returns them to their original installation folder. Configure folders before archiving.')
        description.setWordWrap(True)
        form.addRow(description)
        return widget

    def save_settings(self, widget):
        roots = list(dict.fromkeys(line.strip() for line in widget.destinations.toPlainText().splitlines() if line.strip()))
        for value in [widget.root.text()] + roots:
            if not Path(value).is_absolute():
                raise ValueError('Archiver folders must be absolute Linux paths.')
        settings = self.settings()
        settings.setValue('sourceRoot', widget.root.text().strip())
        settings.setValue('archiveRoots', roots)
        settings.sync()

    def game_actions(self, window, game):
        if game.get('ArchivePath'):
            return [('Restore game…', lambda: self.transfer(window, game, True))]
        if game.get('InstallDirectory'):
            return [('Archive game…', lambda: self.transfer(window, game, False))]
        return []

    def before_launch(self, window, game):
        if getattr(self, '_transferring', False):
            show_warning(window, 'Game Archiver', 'Wait for the archive or restore operation to finish before launching a game.')
            return False
        if not game.get('ArchivePath'):
            return True
        if QMessageBox.question(window, 'Restore game', 'Restore this archived game before launching?') != QMessageBox.StandardButton.Yes:
            return False
        return self.transfer(window, game, True)

    def transfer(self, window, game, restore):
        from .transfer import transfer_game
        from playlite.metadata_dialog import Task
        try:
            if getattr(self, '_transferring', False):
                raise ValueError('Another archive or restore operation is already running.')
            if window.game_detection.status(game['Id']) in ('Running', 'Launching'):
                raise ValueError('Stop the game before archiving or restoring it.')
            from playlite.providers import IntegrationPlugin
            for provider in window.game_providers:
                if isinstance(provider, IntegrationPlugin) and provider.owns(game) and game['Id'] in provider.detect_running([game]):
                    raise ValueError('Stop the game before archiving or restoring it.')
            if restore:
                destination = Path(game['ArchiveOriginalDirectory'])
            else:
                root = Path(self.settings().value('sourceRoot', str(Path.home() / 'Games'))).resolve()
                source = Path(game['InstallDirectory']).resolve()
                if source == root or not source.is_relative_to(root):
                    raise ValueError('Game directory must be inside the configured game library root.')
                settings = self.settings()
                roots = self.archive_roots()
                if not roots:
                    raise ValueError('Set an archive folder in Settings → Plugins → General plugins → Game Archiver first.')
                chosen, accepted = QInputDialog.getItem(window, 'Archive destination', 'Archive folder', roots, editable=False)
                if not accepted:
                    return False
                destination = Path(chosen) / source.relative_to(root)
        except (ValueError, KeyError, OSError) as error:
            show_warning(window, 'Cannot transfer game', str(error))
            return False
        if QMessageBox.question(window, 'Restore game' if restore else 'Archive game',
                                f'Copy, verify, and move “{game["Name"]}” to:\n{destination}\n\nThe game must be stopped.') != QMessageBox.StandardButton.Yes:
            return False
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
        dialog.setWindowTitle('Game Archiver')
        layout = QVBoxLayout(dialog)
        status = QLabel('Copying and verifying game files…')
        layout.addWidget(status)
        cancel = threading.Event()
        button = QPushButton('Cancel')
        button.clicked.connect(lambda: (cancel.set(), button.setEnabled(False), status.setText('Cancelling safely…')))
        layout.addWidget(button)
        task = Task(lambda: transfer_game(window.data, game, destination, restore, cancel))
        success = []
        self._transferring = True
        def finished(result):
            import json
            _, message = result
            window.games = json.loads((window.data / 'library.json').read_text())
            self._transferring = False
            dialog.busy = False
            window.update_filter_choices()
            window.refresh_library()
            success.append(True)
            dialog.accept()
            if 'redundant copy' in message:
                show_warning(window, 'Transfer cleanup', message)
        def failed(error):
            self._transferring = False
            dialog.busy = False
            dialog.reject()
            show_warning(window, 'Transfer failed', error)
        task.signals.succeeded.connect(finished)
        task.signals.failed.connect(failed)
        # Keep the worker alive; closing the dialog requests cancellation.
        dialog.finished.connect(lambda: cancel.set())
        dialog.task = task
        QThreadPool.globalInstance().start(task)
        run_dialog(dialog)
        return bool(success)
