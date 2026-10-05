"""Install source test fixtures into an isolated plugin directory."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from playlite.plugin_manager import install_archive

parser = argparse.ArgumentParser()
parser.add_argument('core', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
identity = json.loads((root / 'manifest.json').read_text())['id']
with __import__('tempfile').TemporaryDirectory() as temporary:
    for entry in json.loads((args.core / 'catalogue.json').read_text())['plugins']:
        checkout = root if entry['id'] == identity else Path(temporary) / entry['id']
        if checkout != root:
            subprocess.run(['git', 'clone', '--depth=1', 'https://github.com/' + entry['repository'] + '.git', str(checkout)], check=True)
        subprocess.run([sys.executable, 'tools/build_release.py'], cwd=checkout, check=True)
        install_archive(checkout / 'dist/plugin.zip', repository=entry['repository'])
