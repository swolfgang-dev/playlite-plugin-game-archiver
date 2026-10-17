"""Build a standalone plugin release without the base application."""
import hashlib
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED
root = Path(__file__).resolve().parent.parent
out = root / 'dist'
out.mkdir(exist_ok=True)
archive = out / 'plugin.zip'
with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    for name in sorted(filter(None, tracked)):
        relative = Path(name)
        path = root / relative
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()) and not any(part in ('.git', '.github', 'dist', '__pycache__', 'tests', 'tools', '.venv') for part in relative.parts):
            bundle.write(path, str(relative))
(out / 'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  plugin.zip\n')
print(archive)
