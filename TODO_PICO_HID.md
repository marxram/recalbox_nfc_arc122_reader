# TODO: Pi Pico as a USB-HID button panel for action cards

Status: **design phase** — no hardware, no code yet. Nothing in this
file has been built or tested; it's the agreed design to pick up once
the Pico and buttons/switch are physically available.

## Goal

A set of physical momentary buttons (+ one toggle switch) wired to a
Raspberry Pi Pico, which enumerates over USB as a plain HID keyboard.
Each button/switch transition sends one dedicated keycode. A new,
separate daemon on the Recalbox listens for those keycodes and runs
the *same* `actions/*.sh` scripts this project already uses for NFC
action cards — the Pico is just a second trigger path into
infrastructure that already exists, not a new action system.

Motivation: a party/multi-user setting wants physical reboot/shutdown/
pad-profile buttons without needing an NFC card on hand, and a
console-mode switch that reflects actual physical state instead of a
tap-to-toggle card.

## Why a separate daemon, not extending `nfc_acr_daemon.py`

Decided 2026-10-11. The NFC daemon's admin-window / restricted-card
policy exists because an NFC card can be tapped by anyone without
proving they have authorized physical access to the device's wiring.
A physical button wired inside the case already implies that level of
trust, so none of that policy is needed here — bolting it onto the NFC
daemon's state machine would be unnecessary complexity for no benefit.
A small, independent daemon (own `/etc/init.d/` service, own log) is
simpler to write and reason about.

## Agreed action table

| # | Input | Behavior | Script |
|---|---|---|---|
| 1 | Reboot/Reset | **long-press** (~2.5s hold) | `actions/reboot.sh` |
| 2 | Shutdown | **long-press** (~2.5s hold) | `actions/shutdown.sh` |
| 3 | Pad profile: N64 | short press | `actions/pads_only_n64.sh` |
| 4 | Pad profile: SNES | short press | `actions/pads_only_snes.sh` |
| 5 | Pad profile: All | short press | `actions/pads_all.sh` |
| 6 | Console mode | toggle switch, 2 states | ON edge -> `consolemode_on.sh`, OFF edge -> `consolemode_off.sh` |

Notes from the design discussion:
- Reboot and Shutdown deliberately require a long press (~2.5s,
  exact threshold tunable) to avoid an accidental tap triggering a
  power action — same rationale as a real case power button.
- Pad profiles got **one dedicated button per profile** (3 buttons),
  not a single button cycling through profiles — simpler, no state to
  track on either the Pico or the daemon side.
- The console-mode switch is a real latching toggle (not momentary):
  it must fire exactly once per edge (one keycode when switched ON,
  a different keycode when switched OFF), not repeatedly while held.

## Pico firmware (planned, not written)

CircuitPython + `adafruit_hid` (keyboard HID, not a gamepad — simplest
option, no custom USB descriptor work; see the chat history around
2026-10-11 for why keyboard was chosen over emulating a gamepad).

- 6 GPIOs, internal pull-ups, buttons/switch to GND.
- Software debounce on all 6 inputs.
- Buttons 3-6 (profiles + switch edges): fire their keycode immediately
  on the debounced transition.
- Buttons 1-2 (reboot/shutdown): only fire once held continuously past
  the long-press threshold - not on initial press, and not again on
  release.
- Use keycodes unlikely to collide with anything else on the system
  (e.g. F13-F18 range) since this device enumerates as a generic
  keyboard system-wide.
- 7 distinct keycodes needed in total (5 buttons + 2 switch edges).

## Recalbox-side daemon (planned, not written)

- New file, e.g. `pico_hid_daemon.py`, alongside `nfc_acr_daemon.py` in
  `/recalbox/share/system/nfc-daemon/` (same directory is fine - no
  reason to split install locations - but it's a separate process/
  service, not merged into the NFC daemon's code).
- Uses `python-evdev` to open the Pico's `/dev/input/eventN` (appears
  as a standard keyboard input device once plugged in) and read
  `EV_KEY` events.
- Static dict: keycode -> script path, one lookup, `/bin/sh <path>`
  (same exFAT-no-exec-bit workaround every other script in this repo
  uses - `/recalbox/share` is exFAT with `fmask=0133`).
- Own `/etc/init.d/S3Xpico-hid`-style service script (pick an unused
  sequence number), own log file under `/tmp` or similar, following
  the existing daemon's pattern of rebinding stdout/stderr at startup
  (BusyBox `start-stop-daemon --background` silently redirects forked
  fds to `/dev/null` otherwise - see `CHANGELOG.md` v3.0 for why).
- Needs to find the right `/dev/input/eventN` reliably across reboots/
  replugs - likely by matching on the Pico's USB vendor:product ID via
  `/sys/bus/usb/devices` (same `find_known_devices()`-style approach
  `configure_pads.py` already uses), not a hardcoded event number.

## Open items before implementation starts

- No hardware yet: Pico, 5 momentary buttons, 1 toggle switch, wiring.
- Exact long-press threshold (2.5s proposed, not fixed).
- Exact GPIO pin assignment on the Pico (cosmetic, decide when wiring).
- Exact keycode choice per input (just needs to be unused elsewhere).
- `manage_actions.py` is NFC-card-specific (UID-based) - the Pico
  daemon's keycode-to-script table is static config, not something
  that needs that tool's add/remove workflow. Worth a short section in
  `ACTION_CARDS.md` once built, but it's conceptually simpler than the
  NFC side, not an extension of it.
