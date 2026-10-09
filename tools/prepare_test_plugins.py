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
    pending = []
    for entry in json.loads((args.core / 'catalogue.json').read_text())['plugins']:
        checkout = root if entry['id'] == identity else Path(temporary) / entry['id']
        if checkout != root:
            subprocess.run(['git', 'clone', '--depth=1', 'https://github.com/' + entry['repository'] + '.git', str(checkout)], check=True)
        subprocess.run([sys.executable, 'tools/build_release.py'], cwd=checkout, check=True)
        manifest = json.loads((checkout / 'manifest.json').read_text())
        pending.append((entry, checkout, manifest))
    installed = set()
    while pending:
        ready = [item for item in pending if all(
            dependency['id'] in installed
            for dependency in item[2].get('plugin_dependencies', []))]
        if not ready:
            raise RuntimeError('Unresolved test fixture dependencies: ' + ', '.join(item[0]['id'] for item in pending))
        for entry, checkout, manifest in ready:
            install_archive(checkout / 'dist/plugin.zip', repository=entry['repository'])
            installed.add(entry['id'])
            pending.remove((entry, checkout, manifest))
