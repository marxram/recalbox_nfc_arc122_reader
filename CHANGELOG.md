# Changelog

## v3.3 — Pad-type profiles (enable/disable pad classes by card tap)

### Added
- `pad_profiles.py` — enables/disables whole classes of known pad
  models by unbinding/rebinding them from the kernel's `usbhid` driver,
  a reversible "soft unplug" needing no replug and no ES restart.
  Scoped to only devices with a verified template in
  `configure_pads.py` — never touches the NFC reader, Bluetooth dongle,
  or anything unrecognized. Ships `n64`, `snes`, and `all` (reset)
  profiles.
- `actions/pads_only_n64.sh`, `actions/pads_only_snes.sh`,
  `actions/pads_all.sh` — new action card wrappers around it.

## v3.2 — Automated controller-port sync for multi-pad hub setups

### Added
- `configure_pads.py` — detects known pad models by USB VID:PID
  wherever they're currently plugged in (across nested USB hubs) and
  writes/updates their `es_input.cfg` `<inputConfig>` block with an
  already human-verified button mapping, keyed by `devicePhysicalPath`.
  Replaces re-running EmulationStation's per-device input wizard for
  every pad on every port it might occupy. Ships with verified
  templates for the project's N64 pad adapter (`0e6d:111d`) and two
  SNES-style pad VID:PIDs (`0810:e501`, `081f:e401`) that turned out to
  share an identical button mapping. Supports `--dry-run`.
- `actions/sync_pads.sh` — new NFC action card wrapper around
  `configure_pads.py`, following the same backup/stop-ES/edit/sync/
  restart-ES pattern as every other action script here.

## v3.1 — Fix shutdown/reboot action cards actually hanging the device

### Fixed
- **`shutdown.sh`/`reboot.sh` called raw `poweroff`/`reboot`**, which
  silently no-ops when run from this daemon's non-interactive subprocess
  context (no `/dev/initctl`, no utmp on this image — BusyBox's `halt`
  can't determine the runlevel and falls back to a no-op "soft
  poweroff"). Both scripts now use `shutdown -h now` / `shutdown -r now`,
  matching the convention Recalbox's own power-button scripts use. See
  `ACTION_CARDS.md` gotchas section.
- Diagnosed (but not part of this repo — fixed directly on the device)
  a third-party `/recalbox/share/system/custom.sh` bug that ignored the
  `stop` argument and relaunched an infinite GPIO-polling loop on every
  shutdown attempt, hanging `rcK` before it could stop any other
  service. This was blocking the physical power button and ES's own
  shutdown menu too, not just NFC cards. See `ACTION_CARDS.md`.

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
