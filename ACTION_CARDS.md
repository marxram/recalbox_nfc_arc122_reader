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
| Normal game card | green | beep if enabled (unchanged, upstream) |

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
| `reboot.sh` | Stops ES cleanly, `sync`, non-forced `reboot` |
| `shutdown.sh` | Stops ES cleanly, `sync`, non-forced `poweroff` |
| `consolemode_off.sh` | Backs up `recalbox.conf`, stops ES, `sed`s only the `cardreader.consolemode` key to `0`, `sync`, restarts ES |
| `consolemode_on.sh` | Same as above but sets `cardreader.consolemode` to `1` |
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
- **Reboot/shutdown are always the clean, non-forced command** (`reboot`,
  `poweroff`, not `-f`/`reboot -f`), preceded by `sync`, so BusyBox still
  runs its normal init shutdown sequence.
- Scripts are invoked as `/bin/sh <path>` by the daemon, never relied on
  for their own executable bit — `/recalbox/share` is exFAT
  (`fmask=0133`), so `chmod +x` there is a permanent no-op.
- WLAN keys/passwords present in `recalbox.conf` are never logged or
  touched by any script here.

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

## Known hardware/platform gotchas (don't re-introduce these)

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

## Update persistence model

- `/recalbox/share/system/nfc-daemon/*` (this repo's install target) —
  **survives** Recalbox updates (data partition).
- `/etc/init.d/S32nfc-acr` — **does not** survive updates (system
  partition); re-run `install_nfc.sh --update` after any Recalbox update.
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
