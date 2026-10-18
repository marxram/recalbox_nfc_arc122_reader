# Changelog

## v3.6 — Secure remote-control API + play-session log

### Added
- **`api_server.py`**: stdlib-only (no new pip dependency to survive a
  Recalbox update) HTTP API running as a background thread inside
  `nfc_acr_daemon.py` — no second init.d service. Exposes an implicit
  allowlist of everything already in `actions.json`/`associations.json`
  (run an action script, launch/stop a game) behind a bearer token, plus
  a self-documenting `GET /` page. Does not duplicate the native
  Recalbox Manager v3 API (`:81/api/*`). See `api-docs/README.md` for
  the integration reference and `ACTION_CARDS.md`'s "Remote-control
  API" section for the implementation writeup.
- **Three independent feature flags** in `config.json`: `nfc_games`,
  `nfc_actions`, `api.enabled` — any combination works, including
  API-only with no physical reader claimed at all.
- **`virtual_card` tracking** in the daemon so a card tapped physically
  and a game launched via the API can coexist: removing the physical
  card never stops an API-launched game, but a genuine new physical
  tap always takes over immediately.
- **Play-session log** (`play_log.jsonl`, `GET /playlog`): start/end/
  duration/source per session, written from the same `_set_game_state`/
  `_clear_game_state` choke point every game-state transition already
  goes through. Nothing native tracks play *duration* at all.
- **`manage_actions.py config`** subcommand to toggle the three flags
  and regenerate the API token without hand-editing `config.json`.

### Fixed
- **Console mode ON/OFF cards were swapped** on the live device — the
  scripts themselves were correct, the bug was the two physical cards'
  UID-to-script assignment in `actions.json` being backwards. Fixed by
  swapping the two entries' `{label, script}` pairs.
- **Idle screensaver blocked API game launches**: `emulationstation.
  screensaver.type=demo` only exits on a real controller button press
  (confirmed via Recalbox's docs), which an API call has no way to
  send. Disabled entirely via `emulationstation.screensaver.time=0`
  (new key, had to be appended rather than `sed`-replaced) — verified
  clean for 10 minutes live, past the old 5-minute default.
- **A found-live bug in the API itself**: action labels containing
  spaces (e.g. "Console mode ON") weren't percent-encoded on the docs
  page or decoded by the server, so 3 of the 4 live actions couldn't
  actually be triggered through the API as shipped. Fixed with
  `urllib.parse.quote`/`unquote`.

### Known issue, not yet resolved
- Switching directly from one already-running game to another via a
  single `POST /games/<uid>/launch` call is unreliable (worked once in
  live testing, failed on two immediate retries after a related fix).
  Workaround: always `POST /games/stop` then a separate `launch` call
  — both independently confirmed reliable. See `ACTION_CARDS.md`.

## v3.5 — Fix N64 pad properly: override Recalbox's input.xml table

### Changed
- **Superseded v3.4's workaround** (repointing `a`/`y` to different
  physical buttons) with the mechanism Recalbox's own wiki documents
  for exactly this situation: a persistent per-device copy of
  `/recalbox/share/system/configs/mupen64/input.xml`. Shipped at
  `configs/mupen64/input.xml` in this repo and deployed by the new
  `ensure_mupen64_input_override()` in `configure_pads.py` (runs on
  every `sync_pads.sh` tap). It changes exactly one line from stock —
  `a` now routes to `B Button` instead of the default `C Button R` —
  which resolves the collision at its actual source instead of working
  around it, and lets the N64 template's `es_input.cfg` entries go back
  to the pad's natural buttons (`a` = real A-face button, `b` = real
  B/C-face button, `joystick2up/down/left/right` = the real C-cluster).

### Fixed (same underlying bugs as v3.4, now fixed at the real root)
- **Stock Recalbox routes both `a` and `joystick2right` to "C Button
  R"** for any N64 pad using the right-analog-stick C-button
  convention — confirmed by Recalbox's own wiki, which says only the
  two official `Nintendo Switch N64 Controller`/`N64 Controller`-named
  pads get a hardcoded bypass for this. Two ES names targeting one
  mupen64 setting get merged into a space-separated value by Recalbox's
  own generator code, and that merged value doesn't work for a plain
  button target on this device — C-Right was completely dead in-game.
- **N64's "B Button" (Smart Bomb) was silently stuck on a stale value**
  from an older config generation, because nothing fed "B Button" and
  Recalbox's ini writer never clears keys that go missing between runs.
  That stale value collided with C-Down's button index, firing a bomb
  on C-Down instead of braking. The `a` -> `B Button` override line
  fixes this as a side effect.
- See "N64 pad: the `a`/`C Button R` collision, and the `input.xml`
  override" in `ACTION_CARDS.md` for the full mechanism — this was
  never an offset bug, it's a Recalbox-side translation table gap for
  third-party pads.
- Button scheme verified end-to-end in Lylat Wars/Star Fox 64: A=laser,
  B=Smart Bomb, C-Up=camera, C-Down=brake, C-Left=boost (+ stick-down =
  the flip-turn move). C-Right still unconfirmed in-game as of this
  writing.

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
