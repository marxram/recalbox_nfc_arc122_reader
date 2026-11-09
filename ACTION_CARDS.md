# Two-Stage Action Cards, Admin Window & Restricted Games

This document covers everything added on top of the upstream
`nfc_acr_daemon.py` for this specific device: a Raspberry Pi 5 whose case
no longer has physical reset/power buttons, so reboot/shutdown/console-mode
are now done entirely through NFC cards — plus an admin-gated mechanism to
keep certain (e.g. 18+) games from launching with a casual tap.

Upstream's own card-reader mechanism (plug a game card → launch) is
**completely unchanged** for ordinary game cards. Everything below is an
additional classification step that runs *before* a card is ever treated
as a game card.

## Why a two-stage mechanism at all

A single NFC tap is too easy to trigger by accident (a card left resting
near the reader, a kid grabbing the wrong card, etc.). Anything
destructive (reboot, shutdown, changing `recalbox.conf`) or
age-gated (restricted games) needs a second, deliberate confirmation step
— but without ever blocking normal game-card polling, since the reader
has no other job 99% of the time.

## Terminology: truncated vs. full UID

- **Truncated UID** (`uid_int`, decimal, used for the ES-visible `uuid`
  file and as the key in `associations.json`): only the first 4 bytes of
  the card's UID, little-endian. This is upstream's original behavior,
  unchanged, because ES/the sysfs interface only ever sees this value.
- **Full UID** (`full_uid`, e.g. `04:5C:F0:30:10:02:89`): all bytes
  returned by the reader (7 for NTAG213/215/216). Used **only** inside the
  daemon to classify admin/action/restricted cards, so they can never
  collide with a truncated game-card UID by chance.

A card only gets its full UID logged the first time it's tapped and not
yet recognized — see "Onboarding a new token" below.

## Classification precedence

On every new card presence, `classify(full_uid)` checks, in order:

1. `admin_uids` → `admin`
2. `actions` (keys) → `action`
3. `restricted_uids` → `restricted`
4. anything else → `game`

Only the `game` branch ever sets `self.plugged = True` and writes to
`/tmp/fake-card-reader/`, i.e. only `game` cards are ever visible to ES.
**A card listed in `actions.json` can never be shown to ES as "unknown,
please assign a game"**, no matter how many times it's tapped — the
classification happens strictly before the generic game-card code path
runs.

## State machine (current: "Admin Window")

```
                    tap admin card
    IDLE ───────────────────────────────▶ WINDOW_OPEN
      ▲                                    │   │
      │          timeout (15s default)     │   │ tap admin card again
      └────────────────────────────────────┘   │ (just resets the 15s timer)
      ▲                                         ▼
      │   tap action/restricted card    WINDOW_OPEN (extended)
      │   → fast blink+beep burst,
      └── run script / forward launch,
          window closes immediately

  (tap action/restricted card OUTSIDE a window → ignored, logged, red blink)
  (tap a normal game card, anytime → launches immediately, unaffected)
```

This is intentionally the **reverse** of the first version: admin
authorizes *first*, opening a 15-second window; the window accepts
exactly **one** action/restricted card, then closes. This matters in
practice for two reasons:

- It reads more naturally for a physical workflow: "I am the admin, I'm
  allowing the next thing" rather than "something wants to happen, now
  prove you're the admin".
- There's no action pending in memory while unauthenticated — nothing
  resembling a pending reboot/shutdown exists until the admin has already
  acted.

LED/buzzer feedback:

| Event | LED | Buzzer |
|---|---|---|
| Window open / extended, waiting | slow alternating red/green (~0.7s) | — |
| Action/restricted card recognized inside window | fast red/green burst (3×, ~0.15s) | beep ×3 if enabled |
| Window closed (timeout) | solid red | — |
| Action/restricted card tapped **outside** a window | single red | — |
| Normal game card, tap recognised (physical only, since v3.8) | 2× quick green flash (~0.15s), ends green | 1 beep if enabled |
| Normal game card, launch handed to ES (after pad reassignment / ES restart, up to ~10s later) | green | beep if enabled (upstream) |

Only the two ACR122U LED/buzzer APDU calls already proven to work by the
original daemon (`green`, `red`) are ever used, including for all
blink patterns above — they're just alternated/timed from Python. **Do
not** add new guessed APDU byte patterns for LED control: an earlier
attempt at a "continuous hardware blink" with invented ctrl-byte/repeat
values left the reader's LED stuck solid and unresponsive to new card
polls on real hardware, recoverable only by physically unplugging the
ACR122U. There is no datasheet for this behavior; only reuse byte
sequences that have been verified against the real reader.

## `actions.json` schema

See `actions.json.example` for a template (the real file with this
device's actual card UIDs is intentionally **not** committed — see
`.gitignore` — copy the example and fill in real UIDs captured per the
workflow below).

```json
{
  "arm_timeout_s": 15,
  "admin_uids": ["<full UID>", "..."],
  "actions": {
    "<full UID>": {"label": "Reboot", "script": "reboot.sh"}
  },
  "restricted_uids": ["<full UID>", "..."]
}
```

- `arm_timeout_s` — window duration in seconds.
- `admin_uids` — full UIDs of the admin card(s). Any number is allowed;
  this device uses 2 (so either of two trusted people can authorize).
- `actions` — full UID → `{label, script}`. `script` must be a bare
  filename matching `^[A-Za-z0-9_-]+\.sh$` and must exist inside
  `actions/` — this is enforced by `SCRIPT_NAME_RE` in the daemon; no
  path or argument from `actions.json` or the card itself is ever passed
  to the shell.
- `restricted_uids` — full UIDs of game cards that must go through the
  admin window instead of launching directly. On confirm, the daemon
  forwards the launch to ES exactly like a normal game card would be
  (same `plugged`/`uuid`/`write_state()` path) — it's gated, not
  special-cased beyond that.

Missing `actions.json` entirely disables all of this — every card then
behaves exactly like stock upstream.

## Action scripts (`actions/`)

| Script | Does |
|---|---|
| `reboot.sh` | Stops ES cleanly, `sync`, `shutdown -r now` |
| `shutdown.sh` | Stops ES cleanly, `sync`, `shutdown -h now` |
| `consolemode_off.sh` | Backs up `recalbox.conf`, stops ES, `sed`s only the `cardreader.consolemode` key to `0`, `sync`, restarts ES |
| `consolemode_on.sh` | Same as above but sets `cardreader.consolemode` to `1` |
| `sync_pads.sh` | Backs up `es_input.cfg`, stops ES, runs `configure_pads.py` to (re)write known pad models' `inputConfig` blocks for whichever physical hub port they're on right now, `sync`, restarts ES — see "Controller port sync" below |
| `pads_only_n64.sh` | Disables every recognized pad model except N64 pads (reversible `usbhid` unbind, no ES restart) — see "Pad profiles" below |
| `pads_only_snes.sh` | Same, but keeps only SNES-style pads enabled |
| `pads_all.sh` | Re-enables every recognized pad model (reset) |
| `test_action.sh` | No-op script for exercising the action-card path without side effects |

Safety rules these scripts follow (non-negotiable — see incident notes
below for why):

- **ES is always stopped before touching `recalbox.conf` or
  `es_input.cfg`.** ES rewrites these files on exit; editing them while
  ES is still running, or killing ES uncleanly, has previously destroyed
  real controller-mapping data on this device.
- **Config edits are always a targeted `sed` on one exact key**, never a
  full-file rewrite, and always preceded by a timestamped backup under
  `config-backups/`.
- **Reboot/shutdown always go through `shutdown -r now` / `shutdown -h
  now`** (never raw `reboot`/`poweroff`, never a `-f` variant), preceded
  by `sync`. Raw `poweroff`/`reboot` silently no-op when run from this
  daemon's subprocess context — see the gotcha below.
- Scripts are invoked as `/bin/sh <path>` by the daemon, never relied on
  for their own executable bit — `/recalbox/share` is exFAT
  (`fmask=0133`), so `chmod +x` there is a permanent no-op.
- WLAN keys/passwords present in `recalbox.conf` are never logged or
  touched by any script here.

## Controller port sync (`configure_pads.py`)

With multiple identical pads (two N64 pads, several SNES-style pads)
spread across the hub ports documented in `TODO_USB_HUBS.md`, relying on
EmulationStation's own per-device input wizard for every single pad on
every port it might ever occupy doesn't scale, and the wizard can't be
driven from here anyway (it needs a human physically pressing buttons).
`configure_pads.py` instead replays an **already-verified** button
mapping for a known pad model onto whichever physical port it's
currently plugged into:

1. A pad model's mapping is captured **once**, by hand: run ES's input
   wizard for it, then cross-check with `evtest` on the real
   `/dev/input/eventN` device for anything the wizard's fixed prompt set
   doesn't cover (e.g. this project's N64 pad has 4 C-buttons but the
   wizard only prompts for 2 "second stick" directions — see the git
   history around 2026-10-11 for how the other two were found).
2. That verified mapping is added to `DEVICE_TEMPLATES` in
   `configure_pads.py`, keyed by USB `vendor:product` (e.g. `0e6d:111d`
   for the N64 pad's adapter).
3. Running the script (normally via the `sync_pads.sh` action card) scans
   `/sys/bus/usb/devices` for anything matching a known template, works
   out its `devicePhysicalPath` from its live sysfs devpath, and
   writes/replaces that exact port's `<inputConfig>` block in
   `es_input.cfg` — never touching blocks for other devices or other
   ports.

**This script never guesses a hardware mapping** — the same rule as the
LED APDU bytes above. A new pad model needs step 1 done by hand first;
skipping straight to writing a template from assumptions is exactly the
kind of unverified-hardware-value guess this project avoids.

`devicePhysicalPath` format: ES/`by-path` writes this as
`platform-xhci-hcd.0-usb-0:<port-chain>:1.0`, where `<port-chain>` is the
device's sysfs devpath (e.g. `1-1.1.1`, as seen in
`/sys/bus/usb/devices`) with the leading `<bus>-` stripped (e.g. `1.1.1`)
— confirmed empirically against this device's own by-path symlinks, see
`TODO_USB_HUBS.md`, not assumed from documentation.

Supports `--dry-run` to show what would change without touching the
file — always check this after adding a new template or moving pads to
new ports before running it for real.

### N64 pad: the `a`/`C Button R` collision, and the `input.xml` override

Recalbox's N64 config generator (`mupenControllers.py` on the device)
doesn't read `es_input.cfg`'s raw `id`/`code` values for C-buttons/A/B
directly — it looks up the **ES-generic name** (`a`, `b`, `x`, `y`,
`joystick2up`, ...) in a static table Recalbox ships at
`/recalbox/share_init/system/configs/mupen64/input.xml` (this is also
documented on Recalbox's own wiki, under N64 controller configuration):

```
a -> C Button R      b -> A Button
x -> C Button U      y -> B Button
joystick2up -> C Button U     joystick2down  -> C Button D
joystick2left -> C Button L   joystick2right -> C Button R
```

Stock, this means `a` and `joystick2right` both target "C Button R" for
any pad using the right-analog-stick convention for C-buttons (like
this project's N64 template). When two different ES names target the
same mupen64 setting, Recalbox's own generator code merges them into
one space-separated value (e.g. `C Button R = button(8) button(1)`)
instead of picking one — and on this device that merged value does not
work for a plain button target: verified in-game, C-Right was
completely dead with both `a` and `joystick2right` defined normally.
Recalbox's wiki confirms only the two official
`Nintendo Switch N64 Controller`/`N64 Controller`-named pads get a
hardcoded bypass for this; every other third-party N64 pad using the
stick convention hits the same collision.

Also, before this was fixed, `y` was never defined by this pad at all,
so "B Button" in the generated `mupen64plus.cfg` was never written and
sat on a stale value left over from an older config generation
(Recalbox's ini writer only adds/updates keys it's given, it never
clears ones that go missing) — which happened to collide with C-Down's
button index and fired a bomb on C-Down instead of braking.

**The fix ships a persistent override** of that table at
`configs/mupen64/input.xml` in this repo, deployed to
`/recalbox/share/system/configs/mupen64/input.xml` by
`ensure_mupen64_input_override()` in `configure_pads.py` (runs on every
`sync_pads.sh` tap, independent of which pads are currently plugged
in). It changes exactly one line: `a` now routes to `B Button` instead
of `C Button R`. That gives the pad's real A-face button a job (N64's
Smart Bomb) and leaves `joystick2right` as the sole, uncontested source
of "C Button R" — so the N64 template's `es_input.cfg` entries stay
natural (`a` = real A-face button, `b` = real B/C-face button,
`joystick2up/down/left/right` = the real C-cluster), no repurposing
needed. Recalbox re-reads `input.xml` at every game launch, and
`/recalbox/share/...` survives updates, so this is a one-time deploy.

**Don't add `x` to the N64 template** without a matching override line
for it too, for the same reason: stock, `x` and `joystick2up` both
target "C Button U" and would reproduce the exact same breakage.

Button scheme verified end-to-end in Lylat Wars/Star Fox 64
2026-10-11: A=laser, B=Smart Bomb, C-Up=camera, C-Down=brake,
C-Left=boost (+ stick-down = the flip-turn move). C-Right's expected
effect (replaying radio messages, not a flight maneuver, per community
guides) is still unconfirmed — nobody's reliably pressed that exact
physical button in testing yet, see the git history around this date.

## Pad profiles (`pad_profiles.py`)

With several pads of different types connected permanently (so nobody
has to hunt for "the right one"), it helps to be able to say "only N64
pads should work right now" — more deterministic controller assignment
when launching a specific system, and it means a player grabbing the
wrong pad type just gets nothing instead of a pad that's mapped for a
different system entirely.

`pad_profiles.py <profile>` enables/disables whole classes of pads by
unbinding/rebinding them from the kernel's `usbhid` driver — a
reversible "soft unplug" that needs no physical replugging and works
even on hubs with no per-port power switching (most cheap ones don't
support that). A disabled pad disappears from `/dev/input` exactly as
if unplugged; EmulationStation/RetroArch already handle that as a
normal hotplug event, so **no ES restart is needed** for this one,
unlike every other action script here. (Corollary: disabling a pad
someone is actively using mid-game will drop their input, same as
yanking the cable — that's the intended behavior, not a bug.)

It only ever touches devices with a verified entry in
`configure_pads.py`'s `DEVICE_TEMPLATES` — never the NFC reader,
Bluetooth dongle, keyboard/mouse, or any pad model without a template
(e.g. the 8BitDo SF30/SN30 pad, which needs no custom template since
Recalbox's own stock mapping already handles it — it's currently
untouched by any profile).

Profiles are just a set of VID:PIDs that stay enabled; defined in
`PROFILES` in `pad_profiles.py`:
- `n64` — only `0e6d:111d`
- `snes` — `0810:e501` and `081f:e401`
- `all` — every known template (reset)

## Automatic per-game controller assignment (`pad_registry.py` / `assign_controllers.py`)

**Status (2026-10-19): Stage 1 built, deployed, correctness verified
live and repeatedly. Latency on configuration-change launches is not
yet fully tuned — see "Known open gap" below. User is trial-using it
and will decide whether to keep it, tune it further, or move to a
powered/per-port-switched USB hub instead (the decision logic below —
which pads, what priority, what player slot — stays reusable either
way; only the enable/disable mechanism would change).** Rollback if
needed: the pre-feature daemon is backed up on the device at
`/recalbox/share/system/nfc-daemon/code-backups/20261019-213628/
nfc_acr_daemon.py` — restore it and `/etc/init.d/S32nfc-acr restart`;
`pad_registry.py`/`assign_controllers.py` can be left in place unused.

### Why, beyond `pad_profiles.py`

The target setup has up to 7 pads permanently connected: 2x N64
(USB-only), 2x SNES (USB-only), 1x Mega Drive pad (BT or USB), 2x
8BitDo SF30 Pro (BT or USB). `pad_profiles.py` above only offers two
fixed, manually-triggered profiles and doesn't know how many players a
given *game* actually needs or which pad should land on which player
slot. This feature decides that automatically, per launch: only the
pads a game can use get enabled, in a fixed priority order (that
system's native pad(s) first, then cross-system fallback pads), so
e.g. the Mega Drive pad is always player 1 on Mega Drive games with the
SNES pad filling in as player 2 if needed, and an N64 game never sees a
stray SNES pad registered as an extra, unused controller.

### Files

- `pad_registry.py` — pad identities (extends, does not duplicate,
  `configure_pads.py`'s `DEVICE_TEMPLATES`: which system each pad is
  *native* for vs. a valid cross-system *fallback*), USB detection (the
  same sysfs scan as above) and Bluetooth detection (`bluetoothctl info
  <mac>`, parses `Connected: yes/no`), and per-pad enable/disable
  (reuses `pad_profiles.py`'s `usbhid` bind/unbind functions directly).
  The Mega Drive pad's VID:PID is **not yet captured** (hardware wasn't
  connected to this project yet as of 2026-10-19) — don't guess it,
  capture it the same hands-on way every other template here was.
- `game_player_counts.json` (gitignored, real card UIDs; format in
  `game_player_counts.json.example`) — `{uid: {slug, title, system,
  players_max}}` for all 59 registered UIDs, derived from
  RetroKnowledge's already-researched `players.max` (no new research
  needed) joined against the live device's `associations.json` by
  title (11 UIDs needed an explicit manual title-variant override,
  verified by hand, not guessed — see the Dev-machine `game_steckbriefe`
  project's `RetroKnowledge/overview.json` for the source of truth if
  this ever needs regenerating).
- `bt_pads.json` (gitignored, real BT MACs; format in
  `bt_pads.json.example`) — the Bluetooth pads `pad_registry.BT_PADS`
  loads at import, in priority order. Missing file = no BT pads.
- `assign_controllers.py` — `apply_for_launch(uid_int, before_es_start)`:
  looks up the system and player count, builds the priority-ordered
  pad list and decides from the **real current state** whether ES must
  be restarted at all (`_restart_reason()`, see "When ES is restarted"
  below). Only then: stop ES, write the player slots + N64 pad profile,
  call the daemon's `before_es_start` hook, start ES. Before v3.10 it
  also unbound every pad not needed and cached the last configuration;
  both are gone.

### Integration point: the NFC daemon itself, not a Recalbox hook

Recalbox has a built-in `[rungame](sync)` userscript mechanism
(`/recalbox/share/userscripts/`) that would seem like the natural place
for this, but it runs as a *child process of EmulationStation itself*,
blocked waiting for it to exit — and the mechanism below needs an
actual ES restart to take effect (`Settings` — which backs
`es_settings.cfg` — loads its file exactly once, at construction,
confirmed by reading EmulationStation's own source). Having that hook
restart its own parent is self-referential and risky. Instead,
`assign_controllers.apply_for_launch()` is called from
`nfc_acr_daemon.py`'s `_set_game_state()`, which already owns exactly
when a tap gets simulated for *both* a real physical tap and an
API-triggered launch — one change covers both paths.

**Placement inside `_set_game_state()` matters**: the call sits *after*
the existing "force removed edge" block (which clears the fake-card-
reader files to "nothing plugged" when switching from one game to
another), not before it. Restarting ES while the OLD game's uid was
still written there made the freshly-started ES immediately re-launch
the game being switched *away from* — caught live, see the comment at
the call site.

### Player order: ES's remembered pad slots in `recalbox.conf` (2026-11-09)

`es_settings.cfg`'s `INPUT P{n}NAME`/`INPUT P{n}GUID` pins can only pin
a pad *model*, not a port, so they can't tell 2 identical N64 pads
apart. They're not used on this device.

**Superseded assumption:** this section used to say ES assigns players
by SDL device-index order, so binding pads in priority order before the
ES restart was enough. That was **wrong**. It was found live when the
M30 (Mega Drive pad) became P1 in Mario Kart 64 although the N64 pads
had been bound first. ES 10.x's `InputMapper` (es-core/src/input/
InputMapper.cpp) stores a player-slot list in `recalbox.conf`:

```
emulationstation.pad0=<GUID>:<Name>
emulationstation.pad0.path=<devicePhysicalPath>   (empty for BT)
...
emulationstation.pad9=...
```

and puts every connected pad back into the slot it remembers.

Current mechanism (`assign_controllers._write_pad_slots()`), run only
while ES is stopped:
1. Chosen pads in priority order go into slots 0…
2. Then every known BT pad that wasn't chosen, in this system's priority
   order. BT pads can't be unbound like USB ones and may wake up after
   ES has started, so they have to sit *behind* the chosen ones. For
   example, the M30 ends up behind both SF30s in a 4-player N64 game.
3. Then all other previously remembered entries in their old order, so
   ES's memory of currently unplugged pads survives.

Only the `emulationstation.padN*` lines are rewritten. Everything else
in `recalbox.conf` stays byte-identical, and a timestamped backup goes
to `nfc-daemon/config-backups/recalbox.conf.bak-*-padslots` (last 20
kept). If ES didn't stop cleanly in time, the slot write is skipped for
that launch (never edit `recalbox.conf` under a live ES).

Per-system priority now lives in `pad_registry.SYSTEM_PRIORITY`, as
pad *classes* in order. A class missing from a system's list is never
used for it:

| System | Priority (classes) |
|---|---|
| `n64` | N64 USB → SF30 USB → SF30 BT (never M30/SNES pads) |
| `megadrive` | M30 USB → M30 BT → SNES USB → N64 USB → SF30 USB → SF30 BT |
| `snes` | SNES USB → SF30 USB → SF30 BT → N64 USB → M30 USB → M30 BT |
| anything else | `DEFAULT_PRIORITY`: all pads, USB first |

`pad_registry.USB_PADS` / `BT_PADS` replace the old `PAD_CLASS` /
`SF30_BT_PADS`. Each entry carries the exact `es_name`/`es_guid` ES
reports for that pad, copied from this device's `frontend.log`
"Added joystick" lines and existing `padN` entries, never computed.
Note that the wired SF30 is `2dc8:6001`; the `2dc8:6101` used earlier
was its BT modalias.

### N64 pad: per-console button profile (2026-11-09)

`es_input.cfg` holds one block per pad, not per console, but the N64 pad
needs different button names on N64 than elsewhere:
- N64 games go through Recalbox's mupen64 `input.xml` translation table.
- Every other system goes straight through RetroArch.

Since `assign_controllers` restarts ES on every console change anyway,
`_apply_n64_pad_profile()` swaps the N64 pads' `<inputConfig>` block in
that window. It is written for each connected N64 pad by its real
`devicePhysicalPath`, only if something actually changes, and backed up
to `config-backups/es_input.cfg.bak-*-n64profile`.

Profiles (`pad_registry.N64_PAD_PROFILES`, chosen by `n64_profile_for()`):
- **`n64`**: byte-for-byte the block that was live on the device on
  2026-11-09, verified with a diff before deploying. It's the v3.4
  mapping, matching the stock mupen64 `input.xml`. Hotkey + Select sit
  on **L** because the pad has no Select button, and Z+Start / L-combos
  exit the game. Note that the repo's v3.5 template and `input.xml`
  override (see "N64 pad" section above) were **never deployed**; the
  device runs the stock `input.xml`.
- **`retro`** (SNES, Mega Drive, everything else):
  - L/R are real shoulders, which F-Zero needs.
  - Hotkey + Select move to **Z**.
  - N64 A/B = SNES B/Y.
  - C-down / C-left = SNES A / X.
  - On Mega Drive: A = N64 B, B = N64 A, C = C-down.

Not yet tested live: N64 pad in an actual SNES game (it only gets picked
when no SNES pad/SF30 is available).

### Two real bugs found live, fixed (not just theorized)

1. **Switching between two actively-running games crashed ES**
   (duplicate SDL2 init, confirmed via `frontend.log`'s `[SDL2] Fatal
   error`), because `S31emulationstation stop` does nothing useful
   while a game is running (ES is blocked on its own emulator child).
   Fixed: `_stop_running_game_if_any()` SIGTERMs (then SIGKILLs if
   needed) the actual `retroarch`/`mupen64plus`/`emulatorlauncher.pyc`
   process tree before ever touching ES's own lifecycle — called
   unconditionally on every launch (see #2 for why that has to be
   unconditional).
2. **Reproduced and fixed this project's pre-existing, previously-
   unsolved "direct API-to-API switch is unreliable" bug** (see the
   Remote-control API section above) — root cause confirmed live: the
   old emulator is simply never told to exit; the daemon's "force
   removed edge" alone (association cleared, 0.3s sleep) isn't enough.
   The first version of this fix only ran the emulator-kill step when
   the pad *configuration* needed to change, so switching between two
   games needing the *same* configuration (common — e.g. two different
   1-player N64 games) still silently kept the old game running. Fixed
   by making the emulator-kill step unconditional, independent of
   whether pad reconfiguration follows — this now closes the documented
   switch-reliability gap automatically on every launch, not just a
   client-side workaround anymore.
3. **Bonus**: this feature's own repeated ES stop/start cycling exposes
   the project's already-known stale-`/var/run/emulationstation.pid`
   gotcha (see "Known hardware/platform gotchas" below) far more often
   than normal use ever did — `S31emulationstation stop` silently
   no-ops on a stale pidfile. `_es_stop()` no longer trusts the init
   script alone (falls back to SIGTERM on the real PID found via
   `pgrep`); a successful start now also rewrites the pidfile to the
   real PID, keeping the *next* cycle sane.

### Final USB pad layout (verified 2026-11-09)

Fixed setup, left to right, each pad marked with a colour. Verified
live: one button press per pad, read from its `/dev/input/event*`
node and that node's USB port (not assumed from the plug order).

| Colour | Pad | USB port (`devicePhysicalPath`) | VID:PID | Player |
|---|---|---|---|---|
| Green  | N64  | `1-1.4.1` (`platform-xhci-hcd.0-usb-0:1.4.1:1.0`) | `0e6d:111d` | N64 P1 |
| Yellow | N64  | `1-1.4.2` | `0e6d:111d` | N64 P2 |
| Pink   | SNES | `1-1.4.3` | `081f:e401` ("USB gamepad") | SNES P1 |
| Orange | SNES | `1-1.4.4` | `0810:e501` ("usb gamepad") | SNES P2 |

Left to right matches the hub port order, so the existing rule
"within one class, USB pads in sysfs order" already gives green before
yellow and pink before orange. The two N64 pads are identical models;
only the port tells them apart, so they must stay on these ports.

### When ES is restarted (v3.10, 2026-11-09)

User's rule: USB pads are always on. Restarting ES (~10–15 s) is only
worth it when a wanted pad is **really connected** but sits on the
wrong player. `_restart_reason()` implements exactly that:

| Game system | ES restart only if … |
|---|---|
| N64 | players 1..n are not the N64 pads (then the SF30s, if connected — that's the 3/4-player case), or the N64 pad's `es_input.cfg` block isn't the `n64` profile |
| SNES | players 1..n are not the SNES pads (then SF30s, …), or the N64 pad block isn't the `retro` profile |
| Mega Drive | an M30 (USB or BT) is connected and is not player 1. No M30: only if player 1 is an N64 pad (then the SNES pads go in front), otherwise keep the current order |
| anything else | never |

n = the game's `players_max`. Only connected pads count: an N64
4-player game with the SF30s switched off compares only 2 players.
"Same kind" is compared, not the exact pad (`sf30_usb` = `sf30_bt`,
either SNES pad model), so the two identical N64 pads swapping places
never causes a restart.

**"Who is player n right now"** is modelled as ES's remembered slot
list (`emulationstation.padN` in `recalbox.conf`) reduced to the pads
connected right now (`_current_player_classes()`), plus connected pads
without a slot at the end. This replaced the old last-launch cache
(`/tmp/assign_controllers_last.json`), which didn't notice pads being
switched on/off between games. Effect: switching N64 → Mega Drive (no
M30) → N64 now never restarts ES; N64 ↔ SNES does.

`apply_for_launch()` re-binds every known USB pad on every launch, so a
pad switched off by an older version or a `pads_only_*` action card
comes back by itself.

### Launch latency (measured 2026-11-09, v3.9)

Every step is logged as `[AssignControllers] timing: <step> Xs (total
Ys)`, and every daemon log line has an `HH:MM:SS.mmm` prefix
(`_TimestampWriter`). On a launch with an ES restart, total 12–15 s:

- stopping the running game: 0–3 s
- **ES stop: 7–10 s**. ES's own shutdown: webserver, an unexplained
  ~4 s gap, then saving gamelists + `recalbox.conf`. Not shortened on
  purpose: killing ES hard risks losing those writes.
- writing slots/profile: < 0.1 s
- ES start until `:81/api/status` answers: ~3 s

v3.9 removed what could be removed without touching ES itself:
1. The tap ack (green blink + beep, `ack_tap()`) runs in its own thread
   instead of blocking the launch for 2.4 s. `send_apdu()` is
   serialised with a lock, because the ACR122U only answers the next
   APDU after a blink finished.
2. The daemon writes the new card state **before** ES starts
   (`before_es_start` hook), so ES boots directly into the game
   (BootOnGame) instead of starting to the menu and then getting a
   simulated tap.

v3.10 then removes most restarts entirely (above).

**Slow stop right after a game (accepted, 2026-11-09):** when a card is
removed, ES in console mode plays the intro video
`cardreader.consolemodevideo` (`Bios2.mp4`, exactly 24 s; `Bios1.mp4`
is 20 s) and only acts on SIGTERM once it has finished. A restart
within ~25 s of a game ending therefore takes ~30 s instead of 7–10 s
(measured twice: SIGTERM → "Quit requested" 26 s). ES has no "no
video" option, a shorter own video would have to live in the system
partition that updates wipe, and SIGKILL risks the gamelist/config
writes. User decided to leave it. Switches without a restart are not
affected.

### Not yet built

- Stage 2: when a game wants more players than are currently available
  (e.g. a 4-player N64 game with only 2 pads live), show an on-screen
  image/message and wait a few seconds before launching anyway — not
  started, deferred until the SNES/Mega-Drive pads are physically
  connected so it can be tested meaningfully.
- Whether Recalbox's snes9x core here can actually route 4 physical
  controllers to a SNES game via software multitap — `super_bomberman`
  genuinely has `players_max: 4` in the data (confirmed, not assumed),
  so the *game* side is real; the *emulator* side is unverified.

## Restricted (admin-gated) games

Currently configured on this device (names only — see the live
`actions.json` on the Pi, or your own `actions.json`, for the actual
UIDs): Mortal Kombat, Mortal Kombat 4, Street Fighter II Turbo,
Command & Conquer, Duke Nukem 64.

## Onboarding a new token or restricted game (capture workflow)

A card's full UID is only known once it's been tapped while **not yet**
in `actions.json` — at that point it logs as `class=game` (harmless; it
doesn't get written to ES unless left as a real game card, and even then
only after being lifted and the normal game flow proceeds).

1. Tap the new card once.
2. Read the full UID from the log:
   ```
   ssh recalbox "tail -n 5 /tmp/nfc-acr.log"
   ```
   Look for `full=XX:XX:XX:XX:XX:XX:XX`.
3. Add that full UID to the right list in `actions.json` (`admin_uids`,
   `actions`, or `restricted_uids`).
4. Deploy and restart:
   ```
   scp actions.json recalbox:/recalbox/share/system/nfc-daemon/actions.json
   ssh recalbox "/etc/init.d/S32nfc-acr restart"
   ```
5. Tap again to confirm it now classifies correctly (log line should show
   the new class, not `game`).

For a **restricted game** specifically, the card must already have a
normal game association in `associations.json` *before* this, since
`run_action_now()` looks up the association by the truncated UID to
build the "Launch (restricted): ..." log label — it doesn't need one to
function, but the label falls back to `UID <n>` without it.

## Remote-control API (`api_server.py`)

Optional HTTP layer in the **same process** as `nfc_acr_daemon.py`
(started as a background thread from `ACRDaemon.start()`, not a
separate service/init.d entry — keeps install/update to one path, see
"Update persistence model" below). Built so a future client (planned:
the user's PiPhone assistant "Andi" — not yet integrated, see "Still
deferred") can trigger anything already defined in `actions.json`/
`associations.json` over the network instead of only via a physical
card tap. Does **not** duplicate the native Recalbox Manager v3 API
(`:81/api/status`, `/versions`, `/systems`, `/systems/<id>/roms`) —
those stay the right tool for read-only status/system/ROM-list lookups.

### Config (`config.json`)

```json
{
  "buzzer": false,
  "nfc_games": true,
  "nfc_actions": true,
  "api": {"enabled": false, "port": 8080, "token": "<random, generated by install_nfc.sh>"}
}
```

Three independent flags, all combinations supported:
- `nfc_games` — plain game-card taps launch a game. Off = the cartridge
  slot ignores unregistered/game cards entirely.
- `nfc_actions` — admin/action/restricted-card classification (the
  whole admin-window mechanism above). Off = every card is just a
  `game` card as far as `classify()` is concerned.
- `api.enabled`/`port`/`token` — the HTTP layer below. The hardware
  poll thread only starts at all if `nfc_games or nfc_actions` is true,
  so an API-only setup (no reader attached) doesn't try to claim the
  USB device.

Manage from the Pi: `python3 manage_actions.py config [--nfc-games
on|off] [--nfc-actions on|off] [--api on|off] [--api-port N]
[--regen-token]` (prints the current config either way; any change
restarts the service so it takes effect immediately).

### Endpoints

Stdlib-only (`http.server.ThreadingHTTPServer` — deliberately no
Flask/FastAPI, see "Update persistence model": anything pip-installed
would need the same update-survival treatment as `/etc/init.d`, and
plain stdlib avoids that entirely).

| Endpoint | Auth | Does |
|---|---|---|
| `GET /` | none | Self-documenting HTML page, generated from the live `actions.json`/`associations.json` every request |
| `GET /actions` | token | List actions as JSON (same shape as `actions.json`'s `actions` key) |
| `POST /actions/<label-or-uid>/trigger` | token | Run one whitelisted script — same `run_whitelisted_script()`/`SCRIPT_NAME_RE` path a real admin-window tap uses |
| `GET /games` | token | List registered game UIDs -> association strings |
| `POST /games/<uid>/launch` | token | Make ES launch that game, as if the card had been tapped |
| `POST /games/stop` | token | Make ES return to the menu, as if the card had been lifted |
| `GET /playlog` | token | Recent play sessions (see below), newest first |

Auth: `Authorization: Bearer <token>` (constant-time compare against
`config.json`'s `api.token`), required on every route except the docs
page. Path segments are percent-decoded before matching (labels like
"Console mode ON" contain spaces).

### Play-session log (`play_log.jsonl`)

`_set_game_state()`/`_clear_game_state()` in the daemon are the single
choke point for every game-state transition now (real tap, API launch,
API stop, admin-gated restricted game), so they also append one JSON
line per finished session to `play_log.jsonl` on the persistent share:

```json
{"start": "2026-10-18T21:42:06", "end": "2026-10-18T21:42:26", "duration_s": 19.4, "uid": "1038113728", "game": "F-Zero (E).zip", "source": "api"}
```

`source` is `"physical"` or `"api"`. Nothing in native Recalbox/ES
tracks play *duration* (only `playcount`/`lastplayed` in `gamelist.xml`,
and even those stay empty for NFC-triggered launches on this setup) —
this log is the only source for "what was played, when, for how long".

### `virtual_card` — letting the reader and the API coexist

A card tapped physically and a game launched via the API both end up
setting the same `plugged`/`uuid` state ES reads, so the daemon tracks
whether the *current* one came from a real tap (`virtual_card=False`)
or the API (`virtual_card=True`):
- Removing the physical card never stops an API-launched game (the
  poll loop's "no card seen for 3 polls" auto-clear explicitly skips
  while `virtual_card` is true).
- A genuine new physical tap always takes over immediately regardless
  of what was running before (real or API), and clears `virtual_card`.
- `_set_game_state()` forces a brief fake "removed" edge (uuid *and*
  association cleared, `plugged=false`, ~0.3s, then the new state) when
  switching from one already-active uuid to another, since a real
  physical swap always has that gap for free and ES/RetroArch may rely
  on seeing it.

**Known unreliable case, confirmed live 2026-10-18:** a single
`POST /games/<uid>/launch` call while a *different* game is already
running (physical or API) worked once in testing but failed on two
immediate retries after it, even after the edge-forcing fix above —
RetroArch/ES didn't always treat the forced edge as a real card swap.
Root cause not fully understood. **Until this is debugged further,
treat direct in-place switching as best-effort, not guaranteed** — the
reliable pattern for a client is always `POST /games/stop` followed by
a separate `POST /games/<uid>/launch`, both independently confirmed
reliable across repeated live tests. Launching from a genuinely idle
state, and stopping, are both solid; only the direct switch-while-
already-running path is in question.

## Known hardware/platform gotchas (don't re-introduce these)

- **No sound after restarting things over SSH (found live 2026-11-09)**:
  ES and every emulator inherit the environment of whoever (re)starts
  ES. PulseAudio listens on `$XDG_RUNTIME_DIR/pulse/native` =
  `/run/user/0/pulse/native`. The boot rc environment sets
  `XDG_RUNTIME_DIR`, a plain SSH shell does not. After the daemon had
  been restarted by hand over SSH, every ES restart it did came up
  without sound. Fixed in code with `assign_controllers._es_env()`,
  which always passes `XDG_RUNTIME_DIR=/run/user/0` to
  `S31emulationstation stop/start`. By hand, always use
  `export XDG_RUNTIME_DIR=/run/user/0; /etc/init.d/S32nfc-acr restart`
  (same for `pactl` over SSH). Also note: restarting the daemon while a
  card lies on the reader re-launches that game.
- **Stale RetroArch auto-savestates crash cores**: RetroArch here runs
  with `savestate_auto_load = true`. A leftover `<rom>.state.auto` in
  `/recalbox/share/saves/<system>/` is loaded silently on every start of
  any libretro core for that ROM. If it doesn't fit the core, the result
  is an immediate segfault (exit -11 / ES "exit code 245") or a broken
  game. First suspect when a libretro core "doesn't start" for one
  game only. See `../system-tools/EMULATOR_TWEAKS.md` (Vigilante 8).

- **BusyBox `start-stop-daemon --background`** redirects the forked
  child's fd 0/1/2 to `/dev/null` itself, *after* the init script's own
  `>> LOGFILE` redirect — silently winning over it. The daemon therefore
  opens its own log file and rebinds `sys.stdout`/`sys.stderr` at import
  time instead of relying on shell redirection.
- **exFAT exec bit**: `/recalbox/share` is exFAT (`fmask=0133`); `chmod
  +x` is a no-op there and `os.access(path, os.X_OK)` will always return
  `False`. Never gate script execution on the executable bit on this
  filesystem — invoke via `/bin/sh <path>` instead.
- **LED APDU bytes**: only `green`/`red` as implemented in
  `led_command()` are proven against the real ACR122U. Everything else
  is a guess and guesses have bricked the LED state on this hardware
  before (see State machine section above).
- **Raw `poweroff`/`reboot` silently no-op from a daemon subprocess**:
  this image has no `/dev/initctl` and no `/var/run/utmp`, so BusyBox's
  `halt` (what `poweroff`/`reboot` symlink to) can't determine the
  runlevel when invoked without a controlling TTY, logs `WARNING: could
  not determine runlevel - doing soft poweroff`, and that fallback does
  nothing — no error, no crash, it just never powers off. Always use
  `shutdown -h now` / `shutdown -r now` instead (same convention
  Recalbox's own `powerswitch.sh`/`rpi-pin*-power.py` use).
- **`/recalbox/share/system/custom.sh` (third-party "SuperPi4 case
  button" hook, not part of this repo) hangs every shutdown/reboot on
  this device**: `/etc/init.d/S99custom` passes `$1` (start/stop) through
  to it, but `custom.sh` ignores `$1` entirely and always runs
  `powerswitch.sh start` — an infinite GPIO-polling loop that never
  returns. Since `rcK` stops services in reverse order and `S99custom` is
  the *first* one it stops, this hung `rcK` immediately, before it ever
  got to `S50dropbear`/`S91smb`/etc. — meaning every shutdown path
  (physical power button, ES's own "Shutdown System" menu, and our NFC
  card) was hanging, not just this project's script. Fixed on the live
  device by making `custom.sh` only call `powerswitch.sh start` when
  `$1` is not `stop`; backup at
  `/recalbox/share/system/custom.sh.20261011-105428.bak` on the Pi. This
  file is **not** tracked by this repo (it belongs to the case vendor's
  own install) and isn't update-persistent the way `/recalbox/share` is
  guaranteed to be, so re-check it after any case-software reinstall.
- **Shutdown/reboot cards appear stuck if ES is in attract-mode/demo
  loop** when tapped: `S31emulationstation stop`'s `SIGTERM` is sent
  immediately, but ES doesn't seem to act on it until its main loop next
  gets a controller input event — observed as "nothing happens until you
  press a button on a pad, then it shuts down within a couple seconds."
  The card tap and the daemon side are working correctly; this is ES's
  own demo-mode loop not noticing the pending stop promptly. Not fixed
  (nothing to fix on our side) — just press any pad button after tapping
  if the device happens to be in attract mode.

## Update persistence model

- `/recalbox/share/system/nfc-daemon/*` (this repo's install target,
  now including `api_server.py` alongside `nfc_acr_daemon.py` — both
  copied by the same loop in `install_nfc.sh`) — **survives** Recalbox
  updates (data partition).
- `/etc/init.d/S32nfc-acr` — **does not** survive updates (system
  partition); re-run `install_nfc.sh --update` after any Recalbox update.
  There is deliberately only this one init.d entry even with the API
  layer added — it runs as a thread inside the same daemon process
  rather than a second service, specifically so a Recalbox update only
  ever needs this one `--update` call, never a second one for the API.
- `recalbox.conf` / `es_input.cfg` changes made at launch time (pad
  slots, N64 profile) live on the share partition, so they **survive**
  updates. They are rewritten on the next launch anyway.
- Per-game emulator overrides (`/recalbox/share/roms/<sys>/<rom>.core.cfg`
  / `.recalbox.conf`) **survive** updates. They aren't part of this
  repo; see `../system-tools/EMULATOR_TWEAKS.md`.
- Anything touched directly on `/recalbox/system/...` (e.g. a custom
  console-mode video under `/recalbox/system/resources/cardreader/`) is
  **not** update-persistent and isn't covered by this installer at all.

## Deliberately not implemented

- **On-screen ("do you really want to reboot?") confirmation on the TV**
  for the admin window — would require patching EmulationStation's C++
  source (no external hook exists for triggering `GuiMsgBox`/
  `GuiInfoPopup` from outside the process); the LED/buzzer feedback was
  judged sufficient instead.
- **Custom console-mode idle video** — technically straightforward
  (replace the `Bios1.mp4`/`Bios2.mp4` resource file and point
  `cardreader.consolemodevideo` at it), just not yet done — pending the
  actual video file.

## Still deferred

Controller/hub setup (unifying the two N64 pad `es_input.cfg` blocks,
fixing the hotkey/C-buttons, labeling and mapping multiple new USB hub
ports to `devicePhysicalPath`) is intentionally **not** part of this
repo's scope yet — it's waiting on the new USB hub(s) being physically
installed and labeled.

**PiPhone integration** — deliberately out of scope for this repo; this
repo only builds the API, PiPhone-side wiring happens later, separately.
There is **no service discovery** of any kind (no mDNS, no registration
anywhere) — a future client needs three things handed to it out of
band, there's no way to derive them from the API itself:
1. The Recalbox's host/IP — PiPhone's `recalbox_status.py` already has
   this in its own `config.yaml` (`recalbox_host`), so likely just
   reused as-is.
2. The API port (`config.json`'s `api.port`, `8080` by default).
3. The bearer token (`config.json`'s `api.token`) — a credential, so it
   has to be copied into PiPhone's config deliberately, never guessed
   or auto-fetched.

`GET /` (no auth) only answers "what can I call" (actions/games/URLs)
once a client already has 1-3 above — it's not a discovery mechanism
for finding the server itself. When PiPhone integration actually
happens, document the chosen approach (env var? PiPhone config.yaml
keys? a one-time manual copy?) here or in PiPhone's own repo — not
decided yet.

**Remote-API test cases:**

- [x] **Physically remove a card while an API-launched game is
  running — confirmed survives, 2026-10-19, live on real hardware.**
  Sequence actually run: physical SimCity card in reader (`virtual_card=
  False`) -> `POST /games/1038113728/launch` (F-Zero) while SimCity
  card still physically present -> confirmed F-Zero running (native
  `:81/api/status`) and SimCity's session correctly closed/logged
  (`Play session logged: Sim City (G).zip (13.6s, physical)`) -> **the
  still-present SimCity card physically removed** -> F-Zero kept
  running undisturbed: `plugged=true`/UID unchanged in daemon status,
  native status still F-Zero, the real `retroarch`/`snes9x` process for
  F-Zero still alive (`ps aux`), no "Card removed" line ever appeared
  in the log. The `virtual_card` skip-logic (poll loop ignoring "no
  card seen" while `virtual_card=True`) works as designed on real
  hardware, not just in the daemon's own state logic.
- [x] **Bonus, same session: a genuine new physical tap immediately
  takes over from an API-launched game — confirmed.** With F-Zero still
  running (API-launched), a third physical card (Mario Kart 64) was
  tapped: log shows `Card detected... class=game` immediately followed
  by `Play session logged: F-Zero (E).zip (69.1s, api)` (clean
  close+log of the API session, `source: api`), native status switched
  to Mario Kart 64. No special handling needed, no stop-then-launch
  required for the *physical-tap-overrides-API* direction specifically
  (only the API-to-API direct-switch direction below is the known
  unreliable one).
- [ ] Debug the direct-switch reliability issue noted above (API launch
  while a *different API-launched* game is already running — works
  sometimes, not others) rather than just routing around it with
  stop-then-launch. Note: the physical-tap-overrides-API path tested
  above was reliable every time; the unreliable case is specifically
  API-launch-over-API-launch, not yet retested this session.
- [ ] Exercise `nfc_games`/`nfc_actions`/`api.enabled` toggled
  individually and in combination (e.g. confirm no USB claim when both
  NFC flags are off, port not listening when `api.enabled` is false).
- [ ] Simulate a Recalbox update (`rm /etc/init.d/S32nfc-acr`, run
  `install_nfc.sh --update`) and confirm the daemon **and** the API
  both come back with the preserved config, no second manual step.
