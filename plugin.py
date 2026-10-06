"""Folder archiving, restoration, and sequential selection batches."""
import json
from pathlib import Path
import threading
from PyQt6.QtGui import QIcon, QDesktopServices
from PyQt6.QtCore import QUrl, QObject, QSettings, QThreadPool, Qt, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QFormLayout, QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QCheckBox, QLineEdit, QInputDialog, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox)
from playlite.providers import GenericPlugin, IntegrationPlugin
from playlite.lifecycle import run_dialog, show_warning, choose_directory


class Progress(QObject):
    changed = pyqtSignal(str)


class Plugin(GenericPlugin):
    def augment_editor(self, editor):
        game = editor.game
        installation = editor.installation_form
        if not hasattr(editor, 'installation_plugin'):
            installation.addRow(QLabel('Archive information'))
            editor.archived = QCheckBox('Archived')
            editor.archived.setChecked(bool(game.get('ArchivePath')))
            editor.archive_path = QLineEdit(game.get('ArchivePath') or '')
            editor.archive_path.setPlaceholderText('Folder containing this archived game')
            archive_row = QHBoxLayout()
            archive_row.setContentsMargins(0, 0, 0, 0)
            archive_row.setSpacing(8)
            archive_row.addWidget(editor.archive_path)
            archive_browse = QPushButton('Browse…')
            archive_browse.setFixedHeight(40)
            def choose_archive():
                path = choose_directory(editor, 'Archived game folder', editor.archive_path.text())
                if path:
                    editor.archive_path.setText(path)
            archive_browse.clicked.connect(choose_archive)
            archive_row.addWidget(archive_browse)
            installation.addRow(editor.archived)
            installation.addRow('Archive folder', archive_row)
            note = QLabel('This records archive information. Changing these fields does not move files. Restore returns the game to its installation folder.')
            note.setWordWrap(True)
            installation.addRow(note)

    def collect_editor(self, editor, result):
        if hasattr(editor, 'archived'):
            if editor.archived.isChecked():
                archive = editor.archive_path.text().strip()
                original = result.get('InstallDirectory') or ''
                if not archive or not Path(archive).is_absolute() or not original or not Path(original).is_absolute():
                    raise ValueError('Archived games need absolute archive and original installation folders.')
                source, target = Path(original).resolve(), Path(archive).resolve()
                if source == target or source in target.parents or target in source.parents:
                    raise ValueError('Archive and installation folders must be separate.')
                result.update(ArchivePath=archive, ArchiveOriginalDirectory=original, IsInstalled=False)
                result['Tags'] = list(dict.fromkeys((result.get('Tags') or []) + ['Archived']))
            else:
                result.pop('ArchivePath', None)
                result.pop('ArchiveOriginalDirectory', None)
                result['Tags'] = [tag for tag in result.get('Tags') or [] if tag != 'Archived']
                if editor.game.get('ArchivePath'):
                    result['IsInstalled'] = True
            archive_keys = ('ArchivePath', 'ArchiveOriginalDirectory')
            if any(result.get(key) != editor.game.get(key) for key in archive_keys):
                result['_ArchiveEditBase'] = {key: editor.game.get(key) for key in archive_keys}

    def prepare_edit_save(self, previous, result):
        archive_edit = result.pop('_ArchiveEditBase', None)
        if archive_edit is not None:
            current_archive = {key: previous.get(key) for key in ('ArchivePath', 'ArchiveOriginalDirectory')}
            if current_archive != archive_edit:
                raise ValueError('Archive information changed while editing. Reopen the editor before changing it.')
        if previous and archive_edit is None:
            # Archive state belongs to the archiver, not a stale editor snapshot.
            for key in ('ArchivePath', 'ArchiveOriginalDirectory'):
                if key in previous:
                    result[key] = previous[key]
                else:
                    result.pop(key, None)
            if previous.get('ArchivePath'):
                result['IsInstalled'] = False
                result['Tags'] = list(dict.fromkeys((result.get('Tags') or []) + ['Archived']))

    def augment_game_view(self, window, game, heading, installation_form):
        if game.get('ArchivePath'):
            badge = QLabel('Archived')
            badge.setObjectName('archiveIndicator')
            badge.setToolTip(game['ArchivePath'])
            archive_icon = QLabel()
            archive_icon.setPixmap(QIcon(str(Path(__file__).parent / 'assets/archive.svg')).pixmap(20, 20))
            heading.addWidget(archive_icon)
            heading.addWidget(badge)
        if game.get('ArchivePath'):
            archived = QPushButton(game['ArchivePath'])
            archived.setToolTip(game['ArchivePath'])
            archived.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(game['ArchivePath'])))
            installation_form.addRow(QLabel('Archive'), archived)

    def settings(self):
        return QSettings('Playlite', 'Archiver')

    def archive_roots(self):
        settings = self.settings()
        legacy = settings.value('archiveRoot', '')
        return settings.value('archiveRoots', [legacy] if legacy else [], type=list)

    def create_settings(self, parent=None):
        widget = QWidget(parent)
        form = QFormLayout(widget)
        widget.destinations = QTableWidget(0, 1)
        table = widget.destinations
        table.setHorizontalHeaderLabels(['Archive location'])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for path in self.archive_roots():
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(path))
        form.addRow('Archive locations', table)
        buttons = QHBoxLayout()
        buttons.addStretch()
        add = QPushButton('Add archive folder…')
        def pick_archive():
            path = choose_directory(widget, 'Archive folder', '')
            if path:
                for row in range(table.rowCount()):
                    if table.item(row, 0).text().strip() == path:
                        table.selectRow(row)
                        return
                row = table.rowCount()
                table.insertRow(row)
                table.setItem(row, 0, QTableWidgetItem(path))
                table.selectRow(row)
        add.clicked.connect(pick_archive)
        buttons.addWidget(add)
        widget.delete_location = QPushButton('Delete selected')
        widget.delete_location.setToolTip('Remove selected locations from settings. Folders and archived games are kept.')
        widget.delete_location.setEnabled(False)
        table.itemSelectionChanged.connect(lambda: widget.delete_location.setEnabled(bool(table.selectedItems())))
        def delete_selected():
            for row in sorted({item.row() for item in table.selectedItems()}, reverse=True):
                table.removeRow(row)
        widget.delete_location.clicked.connect(delete_selected)
        buttons.addWidget(widget.delete_location)
        buttons.addStretch()
        form.addRow(buttons)
        explanation = QLabel('Archives copy the entire installation folder, verify its files, permissions and links, then remove the original. Restore returns it to its original location. Each game folder goes directly inside the selected archive location.')
        explanation.setWordWrap(True)
        form.addRow(explanation)
        return widget

    def save_settings(self, widget):
        roots = list(dict.fromkeys(widget.destinations.item(row, 0).text().strip()
            for row in range(widget.destinations.rowCount()) if widget.destinations.item(row, 0).text().strip()))
        for value in roots:
            if not Path(value).is_absolute():
                raise ValueError('Archiver folders must be absolute Linux paths.')
            if Path(value).exists() and not Path(value).is_dir():
                raise ValueError('Archive locations must be folders.')
        settings = self.settings()
        settings.remove('sourceRoot')
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
                    destination = str(Path(chosen) / source.name)
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
