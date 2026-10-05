"""Verified folder transfers with recoverable source quarantine."""
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid
from playlite.storage import atomic_json
from playlite.library_storage import library_lock


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise ValueError('Transfer cancelled. Source files were kept.')


def scan_error(error):
    raise error


def attributes(path):
    return tuple((name, os.getxattr(path, name, follow_symlinks=False))
                 for name in sorted(os.listxattr(path, follow_symlinks=False)))


def folder_metadata(path):
    info = path.stat()
    return stat.S_IMODE(info.st_mode), info.st_mtime_ns, attributes(path)


def inventory(root, cancel=None):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
        for name in dirs + files:
            check_cancel(cancel)
            path = Path(directory) / name
            info = path.lstat()
            relative = str(path.relative_to(root))
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                result[relative] = ('link', os.readlink(path), info.st_mtime_ns, attributes(path))
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = ('directory', mode, info.st_mtime_ns, attributes(path))
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        check_cancel(cancel)
                        digest.update(chunk)
                result[relative] = ('file', info.st_size, mode, info.st_mtime_ns, digest.hexdigest(), attributes(path))
            else:
                raise ValueError(f'Special files cannot be archived: {path}')
    return result


def sync_path(path, directory=False):
    descriptor = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if directory else 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_tree(root):
    for folder, dirs, files in os.walk(root, topdown=False, followlinks=False, onerror=scan_error):
        for name in files:
            path = Path(folder) / name
            if not path.is_symlink():
                sync_path(path)
        sync_path(folder, directory=True)


def promote(source, target):
    """Atomically publish a directory without replacing a concurrently created target."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise ValueError('Destination appeared during transfer. Source files were kept.')
        raise OSError(error, os.strerror(error), str(target))


def recover_transfers(data):
    """Restore uncommitted quarantines after interruption; never delete recovery copies."""
    journal = Path(data) / 'archive-transfers'
    games = json.loads((Path(data) / 'library.json').read_text())
    for path in journal.glob('*.json'):
        record = json.loads(path.read_text())
        source, target, quarantine = (Path(record[key]) for key in ('source', 'target', 'quarantine'))
        saved = next((game for game in games if game['Id'] == record['id']), {})
        committed = (saved.get('ArchivePath') == str(target) if not record['restore'] else
                     not saved.get('ArchivePath') and saved.get('InstallDirectory') == str(target))
        if quarantine.exists() and not committed:
            if source.exists():
                raise ValueError(f'Interrupted transfer needs recovery. Original files remain at {quarantine}.')
            promote(quarantine, source)
            path.unlink()
        elif not quarantine.exists():
            path.unlink()


def transfer_game(data, game, destination, restore=False, cancel=None, progress=lambda text: None):
    data = Path(data)
    source = Path(game['ArchivePath'] if restore else game['InstallDirectory']).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    with (data / 'archive.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another archive or restore operation is already running.') from None
        recover_transfers(data)
        if not source.is_absolute() or source.is_symlink() or not source.is_dir():
            raise ValueError('Select an existing absolute game directory.')
        with (source / '.playlite-steamautocrack.lock').open('a') as game_lock:
            try:
                fcntl.flock(game_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another operation is already modifying this game.') from None
            return _transfer_game(data, game, destination, restore, cancel, progress)


def _transfer_game(data, game, destination, restore=False, cancel=None, progress=lambda text: None):
    source = Path(game['ArchivePath'] if restore else game['InstallDirectory']).expanduser()
    target = Path(destination).expanduser()
    if not target.is_absolute() or target.is_symlink():
        raise ValueError('Select an absolute destination directory without a symlink.')
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Source and destination must be separate directories.')
    if target.exists():
        raise ValueError('Destination already exists; nothing was overwritten.')
    progress('Reading and hashing source files…')
    before = inventory(source, cancel)
    root_metadata = folder_metadata(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    required = sum(entry[1] for entry in before.values() if entry[0] == 'file')
    if shutil.disk_usage(target.parent).free < required:
        raise ValueError('Not enough free space at the destination.')
    token = uuid.uuid4().hex
    temporary = target.with_name(target.name + '.partial-' + token)
    quarantine = source.with_name(source.name + '.moving-' + token)
    journal = data / 'archive-transfers' / (token + '.json')
    journal.parent.mkdir(exist_ok=True)
    committed = False
    def copy_file(src, dst):
        progress('Copying ' + str(Path(src).relative_to(source)))
        with open(src, 'rb') as incoming, open(dst, 'xb') as outgoing:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                check_cancel(cancel)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        shutil.copystat(src, dst)
        return dst
    try:
        shutil.copytree(source, temporary, copy_function=copy_file, symlinks=True)
        progress('Verifying copied files and the source…')
        if inventory(source, cancel) != before or inventory(temporary, cancel) != before or folder_metadata(temporary) != root_metadata:
            raise ValueError('Source changed or verification failed. Source files were kept.')
        check_cancel(cancel)
        sync_tree(temporary)
        promote(temporary, target)
        sync_path(target.parent, directory=True)
        atomic_json(journal, dict(id=game['Id'], source=str(source), target=str(target),
                                 quarantine=str(quarantine), restore=restore))
        sync_path(journal)
        sync_path(journal.parent, directory=True)
        promote(source, quarantine)
        sync_path(source.parent, directory=True)
        if inventory(quarantine, cancel) != before or folder_metadata(quarantine) != root_metadata:
            raise ValueError('Source changed before removal. Original files were kept.')
        check_cancel(cancel)
        with library_lock(data):
            path = data / 'library.json'
            games = json.loads(path.read_text())
            saved = next((entry for entry in games if entry['Id'] == game['Id']), None)
            source_key = 'ArchivePath' if restore else 'InstallDirectory'
            if saved is None or saved.get(source_key) != game.get(source_key):
                raise ValueError(f'Game installation changed during transfer. Source kept; verified copy remains at {target}.')
            tags = saved.get('Tags') or []
            if restore:
                saved.pop('ArchivePath', None)
                saved.pop('ArchiveOriginalDirectory', None)
                saved.update(IsInstalled=True, InstallDirectory=str(target))
                saved['Tags'] = [tag for tag in tags if tag != 'Archived']
            else:
                saved.update(ArchivePath=str(target), ArchiveOriginalDirectory=str(source), IsInstalled=False)
                saved['Tags'] = list(dict.fromkeys(tags + ['Archived']))
            shutil.copy2(path, path.with_suffix('.json.bak'))
            atomic_json(path, games)
            sync_path(path)
            sync_path(path.parent, directory=True)
            committed = True
        progress('Removing the verified original copy…')
        try:
            shutil.rmtree(quarantine)
        except OSError:
            return games, f'Transfer finished. A redundant copy remains at {quarantine}.'
        journal.unlink()
        return games, 'Restored.' if restore else 'Archived.'
    except Exception:
        if not committed and quarantine.exists() and not source.exists():
            promote(quarantine, source)
        if not quarantine.exists():
            journal.unlink(missing_ok=True)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
