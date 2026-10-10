# Changelog

## v3.0 — Action cards, admin window, restricted games (this fork)

### Added
- Action cards: whitelisted shell scripts (`actions/`) triggered by
  dedicated NFC cards, classified by full UID so they're never exposed
  to EmulationStation as assignable game cards. See `ACTION_CARDS.md`.
- Admin window: an admin card opens a 15s window; the next action or
  restricted-game card tapped inside it is consumed immediately (fast
  blink+beep confirmation), then the window closes. Normal game cards
  are completely unaffected.
- Restricted games: specific game UIDs (`restricted_uids` in
  `actions.json`) require the admin window instead of launching
  directly — used here for 18+-rated titles.
- Daemon now rebinds `sys.stdout`/`sys.stderr` to its own log file at
  startup, working around BusyBox `start-stop-daemon --background`
  silently redirecting the forked process's fds to `/dev/null` (this bug
  predates this fork's changes — logging never actually reached
  `/tmp/nfc-acr.log` even on stock upstream when launched this way).

### Fixed
- Action-script execution no longer gated on the executable bit, which
  is a permanent no-op on the exFAT-mounted `/recalbox/share`
  (`fmask=0133`) — scripts are invoked as `/bin/sh <path>` instead.

## v2.0 — Association bug fix

### Fixed
- **Critical bug in association persistence**: previously, associating a
  card that was already associated with another game would concatenate
  the new entry to the old one in `associations.json`, corrupting the
  file and breaking all subsequent scans. Root cause: EmulationStation
  opens the `association` sysfs-like file in `O_APPEND` mode, so each
  write was added to the existing content instead of replacing it. The
  daemon now tracks its own writes and extracts only the new portion
  appended by ES, then rewrites the file clean to prevent future
  accumulation.

### Added
- `clean_associations.py` — one-shot script to repair `associations.json`
  files corrupted by the v1.x bug. Creates a `.corrupted.bak` backup
  before modifying. Run once after upgrading to v2.x.

### Migration from v1.x
If you were using a previous version:
```bash
git pull
./install_nfc.sh --update
/etc/init.d/S32nfc-acr stop
python3 clean_associations.py
/etc/init.d/S32nfc-acr start
```
