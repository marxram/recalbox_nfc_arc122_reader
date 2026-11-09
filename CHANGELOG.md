# Changelog

## v3.10 — ES restart only when really needed (2026-11-09)

### Changed
- **USB pads stay on.** `assign_controllers` no longer unbinds pads a
  game doesn't need; it re-binds every known USB pad on each launch.
- **ES is restarted only by the user's rule** (`_restart_reason()`):
  N64 ↔ SNES switch (N64 or SNES pads must be in front, N64 pad
  profile must match), SF30s for N64 3–4 players if connected, M30 as
  Mega Drive player 1 if connected. Otherwise, e.g. Mega Drive without
  M30 or Vectrex, the running ES is kept.
- The decision uses the real state (remembered slots in
  `recalbox.conf` reduced to connected pads, plus the N64 pad's
  `es_input.cfg` block) instead of the last-launch cache
  `/tmp/assign_controllers_last.json` (removed).

### Fixed
- **Late second beep** long after the game was already running: with
  BootOnGame, ES's API doesn't answer while the emulator runs, so the
  ready-wait ran into its 15 s timeout. It now also ends as soon as an
  emulator is running, and a physical tap gets no second beep at the
  end (the tap ack already beeped); API launches still do.
- **ES stop could take 30 s and a second ES was started beside it.**
  Right after a boot-on-game session ends, ES builds its UI (~12 s)
  and only reacts to SIGTERM afterwards. On top of that, v3.9 had
  rewritten the pidfile to the ES binary instead of the
  `emulationstation-starter` wrapper that `S31emulationstation stop`
  is meant to signal. Now: the pidfile always names the starter,
  `_es_stop()` waits up to 60 s for starter AND ES to be gone, and if
  that fails no second ES is started (the running one picks the card
  up through its normal poll).

### Device config (not in this repo)
- ES background music off: `audio.bgmusic=0`, `audio.music.volume=0`
  in `recalbox.conf` (was audible in console mode, where the menu
  option isn't reachable). Backup `config-backups/*-bgmusic`.
- Console-mode sound: `cardreader.consolemodesound=bios` → `recalbox`
  (the "bios" setting plays `/recalbox/system/resources/cardreader/
  Bios2.wav` regardless of `audio.bgmusic`; "recalbox" uses ES music,
  which is off). Backup `config-backups/*-consolesound`.

## v3.9 — Faster launches (2026-11-09)

### Changed
- `ack_tap()` runs in a background thread; `send_apdu()` is serialised
  with a lock. The launch no longer waits 2.4 s for the blink pattern.
- The new card state is written while ES is stopped
  (`apply_for_launch(..., before_es_start=...)`), so ES boots straight
  into the game.

### Added
- Step timing in `assign_controllers` (`timing: <step>` log lines) and
  an `HH:MM:SS.mmm` prefix on every daemon log line.

## v3.8 — Immediate tap feedback (2026-11-09)

### Added
- **`ack_tap()`**: a physical tap of a new game card now gets feedback
  right away. That's 1 beep + 2 quick green flashes, before the pad
  reassignment / ES restart, which can take ~10 s. Before this, the
  only feedback was the green+beep at the very end. Uses only the two
  proven `led_command()` states, no new APDU bytes. API launches and
  repeat taps of the already-running game are unchanged. A restricted
  game inside the admin window gets the 3-beep confirm burst followed
  by this ack.

## v3.7 — Correct player order, per-console N64 pad profile, sound fix (2026-11-09)

Details: `ACTION_CARDS.md`, sections "Player order: ES's remembered pad
slots" and "N64 pad: per-console button profile".

### Fixed
- **Wrong player 1 in Mario Kart 64** (M30 instead of an N64 pad). ES
  does not assign players by SDL device order, as previously assumed.
  It keeps a remembered slot list (`emulationstation.padN` /
  `padN.path`) in `recalbox.conf`. `assign_controllers` now rewrites
  exactly those keys while ES is stopped, with a timestamped backup
  (`config-backups/*-padslots`).
- **No sound in any game** after the daemon had been restarted over
  SSH. ES restarts now always get `XDG_RUNTIME_DIR=/run/user/0`
  (`_es_env()`).

### Changed
- `pad_registry.py`: `PAD_CLASS`/`SF30_BT_PADS` replaced by
  `USB_PADS`/`BT_PADS`, which include the M30 (USB `2dc8:5006` + BT)
  and the wired SF30 (`2dc8:6001`). Each entry carries the exact ES
  name and GUID. New `SYSTEM_PRIORITY` per system; N64 only allows N64
  pads + SF30s. USB pads are scanned directly via sysfs, so the 8BitDo
  pads no longer need a `configure_pads.py` template.

### Added
- **Per-console N64 pad profile**: N64 games keep the existing mapping
  (byte-identical; Hotkey/Select on L). SNES/Mega Drive/others get a
  RetroArch-style mapping: real L/R shoulders, Hotkey/Select on Z,
  C-buttons as the missing SNES A/X. Swapped in `es_input.cfg` while
  ES is stopped, with backup (`config-backups/*-n64profile`).

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
