"""Verified native transfers for the Playlite Archiver plugin."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import uuid
from playlite.storage import atomic_json


def inventory(root, cancel=None):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            if cancel and cancel.is_set():
                raise ValueError('Transfer cancelled. Source files were kept.')
            path = Path(directory) / name
            info = path.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError(f'Symlinks and special files are not supported: {path}')
            relative = str(path.relative_to(root))
            if path.is_dir():
                result[relative] = ('directory',)
            else:
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        if cancel and cancel.is_set():
                            raise ValueError('Transfer cancelled. Source files were kept.')
                        digest.update(chunk)
                result[relative] = (info.st_size, info.st_mtime_ns, digest.hexdigest())
    return result


def transfer_game(data, game, destination, restore=False, cancel=None):
    source = Path(game['ArchivePath'] if restore else game['InstallDirectory']).expanduser()
    if not source.is_absolute() or source.is_symlink() or not source.is_dir():
        raise ValueError('Select an existing absolute game directory.')
    Path(data).mkdir(parents=True, exist_ok=True)
    with (Path(data) / 'archive.lock').open('w') as lock, (source / '.playlite-steamautocrack.lock').open('a') as game_lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(game_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another archive, restore, or game-file processing operation is already running.')
        return _transfer_game(data, game, destination, restore, cancel)


def _transfer_game(data, game, destination, restore=False, cancel=None):
    source = Path(game['ArchivePath'] if restore else game['InstallDirectory']).expanduser()
    target = Path(destination).expanduser()
    if not source.is_absolute() or not target.is_absolute() or source.is_symlink() or not source.is_dir():
        raise ValueError('Select an existing game directory and an absolute destination.')
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Source and destination must be separate directories.')
    if target.exists():
        raise ValueError('Destination already exists; nothing was overwritten.')
    before = inventory(source, cancel)
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < sum(entry[0] for entry in before.values() if len(entry) == 3):
        raise ValueError('Not enough free space at the destination.')
    temporary = target.with_name(target.name + '.partial-' + uuid.uuid4().hex)
    def copy_file(src, dst):
        with open(src, 'rb') as incoming, open(dst, 'xb') as outgoing:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                if cancel and cancel.is_set():
                    raise ValueError('Transfer cancelled. Source files were kept.')
                outgoing.write(chunk)
        shutil.copystat(src, dst)
        return dst
    try:
        shutil.copytree(source, temporary, copy_function=copy_file, symlinks=True)
        if inventory(source, cancel) != before or inventory(temporary, cancel) != before:
            raise ValueError('Source changed or verification failed. Source files were kept.')
        if cancel and cancel.is_set():
            raise ValueError('Transfer cancelled. Source files were kept.')
        if target.exists():
            raise ValueError('Destination appeared during transfer. Source files were kept.')
        temporary.rename(target)
        from playlite.library_storage import library_lock
        with library_lock(data):
            path = Path(data) / 'library.json'
            games = json.loads(path.read_text())
            saved = next((entry for entry in games if entry['Id'] == game['Id']), None)
            source_key = 'ArchivePath' if restore else 'InstallDirectory'
            if saved is None or saved.get(source_key) != game.get(source_key):
                raise ValueError(f'Game installation changed during transfer. Source kept; verified copy remains at {target}.')
            tags = saved.get('Tags') or []
            if restore:
                saved.pop('ArchivePath', None)
                saved.pop('ArchiveOriginalDirectory', None)
                saved['IsInstalled'] = True
                saved['Tags'] = [tag for tag in tags if tag != 'Archived']
            else:
                saved['ArchivePath'] = str(target)
                saved['ArchiveOriginalDirectory'] = str(source)
                saved['IsInstalled'] = False
                saved['Tags'] = list(dict.fromkeys(tags + ['Archived']))
            shutil.copy2(path, path.with_suffix('.json.bak'))
            atomic_json(path, games)
        # The verified destination and its restoration record are committed first.
        quarantine = source.with_name(source.name + '.moving-' + uuid.uuid4().hex)
        try:
            source.rename(quarantine)
            shutil.rmtree(quarantine)
        except OSError:
            return games, f'Transfer finished. A redundant copy remains at {quarantine if quarantine.exists() else source}.'
        return games, 'Restored.' if restore else 'Archived.'
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
