# Game Archiver for Playlite

Verified game-folder archive and restore operations.

This repository contains only this plugin. Playlite itself lives in
[swolfgang-dev/Playlite](https://github.com/swolfgang-dev/Playlite).
Requires Playlite 0.2.0 or later, plugin API 1.

## Installation

Install from the public GitHub release:

```sh
playlite-plugins install swolfgang-dev/playlite-plugin-game-archiver
```

Or choose Settings → Plugins → Installed → Install / update from GitHub.
Restart Playlite after installation or updating. Settings and game data remain in
Playlite's existing user-data folders. 

## Releases

Push a `v1.1.0`-style tag to run the release workflow. Each release
contains `plugin.zip` and `SHA256SUMS`. The archive has `manifest.json` and
`plugin.py` at its root; it never includes the base application.

Build locally with `python3 tools/build_release.py`.
The manifest declares any additional Python dependencies, installed into the
same environment as Playlite.

## Tests

Plugin integration tests live in `tests/`. Install Playlite and the optional
plugins required by a test, then run:

```sh
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -q
```

Tests requiring absent plugins are skipped. The release workflow checks Python
syntax and builds the standalone archive; integration tests run locally with
Playlite installed. Native executables are not bundled in the SteamAutoCrack
plugin; its separate tool installer downloads/builds them when requested.

## Distribution

Packages are published directly as GitHub releases in this source repository. Tag the manifest version (for example, `v1.1.2`) to build and publish `plugin.zip` and its checksums automatically.
