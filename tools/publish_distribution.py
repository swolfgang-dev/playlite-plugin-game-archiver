"""Publish a built plugin to its independently switchable distribution repository."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('tag', help='Release tag, matching manifest version')
args = parser.parse_args()
manifest = json.loads((root / 'manifest.json').read_text())
if args.tag != 'v' + manifest['version']:
    parser.error('Tag must match the manifest version.')
repository = manifest['distribution_repository']
subprocess.run(['python3', str(root / 'tools/build_release.py')], check=True)
# Never change repository visibility. A private distribution remains private.
subprocess.run(['gh', 'release', 'create', args.tag, str(root / 'dist/plugin.zip'),
                str(root / 'dist/SHA256SUMS'), '--repo', repository, '--title',
                manifest['name'] + ' ' + args.tag, '--notes', 'Standalone Playlite plugin package. Checksums included.'], check=True)
with tempfile.TemporaryDirectory() as temporary:
    destination = Path(temporary) / 'repository'
    subprocess.run(['gh', 'repo', 'clone', repository, str(destination), '--', '--config', 'credential.helper=!gh auth git-credential'], check=True)
    public = {key: manifest[key] for key in ('id', 'name', 'version', 'description', 'minimum_playlite_version') if key in manifest}
    public['repository'] = repository
    (destination / 'plugin.json').write_text(json.dumps(public, indent=2) + '\n')
    def git(*arguments):
        subprocess.run(['git', *arguments], cwd=destination, check=True)
    git('config', 'user.name', 'Playlite release automation')
    git('config', 'user.email', 'release@users.noreply.github.com')
    git('add', 'plugin.json')
    diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=destination)
    if diff.returncode:
        git('commit', '-m', 'Update plugin metadata for ' + args.tag)
        git('push')
print('Published ' + repository + ' ' + args.tag)
