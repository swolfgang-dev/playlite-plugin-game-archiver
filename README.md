# Game Archiver for Playlite

Verified folder archive and restore operations, including selections of games.
Requires Playlite 0.2.23 or later, plugin API 1.

Install through Settings → Plugins → Available. Configure one or more archive
locations in Settings → Plugins → General → Game Archiver. The optional game
library root preserves relative paths; games outside it use their folder names.
No compression is used. Existing archive records remain supported.

Select games with Ctrl-click or Shift-click in either list or grid view, then
right-click to archive or restore them. Mixed selections offer separate actions
for installed and archived entries. A batch processes games sequentially and
reports individual failures. Cancelling keeps completed transfers and stops the
current transfer safely before skipping the remaining games.

Each transfer copies the entire installation directory into a temporary folder
at the destination, flushes files to disk, and verifies hashes, sizes, timestamps,
permissions, empty folders, and symlink targets. Links are preserved without
following them. Special files are rejected. Existing destinations are never
replaced. The original folder is quarantined and verified again; only after the
library record is committed is that verified original removed. Interrupted
uncommitted quarantines are recovered before a later transfer. Recovery copies
are retained if cleanup fails.

Playing an archived game offers to restore it to its original location, then
launches it after restoration succeeds. Executable and Wine prefix paths stay
unchanged. Archive information and an archive icon appear in Playlite's game
view. The editor can record an existing archive manually; editing those fields
does not move files.

## Development

Build with `python3 tools/build_release.py`. CI installs Playlite and plugin
fixtures into isolated data folders, runs all plugin tests, then publishes
`plugin.zip` and `SHA256SUMS` when a version tag is pushed.
