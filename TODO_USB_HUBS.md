# TODO: Multi-hub USB pad mapping (deferred)

Status: hubs are physically installed and port-to-kernel-path mapping is
**confirmed** (2026-10-11, see "Confirmed port mapping" below). Remaining
work is the actual `es_input.cfg` unification (step 5 of the plan).

## Hub layout as installed (2026-10-11)

- **Hub A** (4-port, ~0.5m cable) plugged into the Pi 5's lower USB3+
  port (`1-1` in kernel devpath notation).
- **Hub B** (4-port, identical model to Hub A, shorter cable) plugged
  into Hub A's port 1 (`1-1.1`).
- Both hubs: Genesys Logic `05e3:0610`.

**Port numbering convention used throughout this doc**: hub orientation
with the **LED facing up** and the **upstream cable exiting to the
left**. Port 1 is the leftmost downstream port in that orientation,
counting rightward. This has to be the fixed reference orientation for
the labels (A1-A4, B1-B4) to mean anything — if a hub gets physically
flipped or rotated, the labels must be reassigned, not just reread.

## Confirmed port mapping

Verified by direct unplug/replug (not just VID:PID inference) on
2026-10-11 — each row below was confirmed by actually removing that
device and observing which kernel devpath disappeared, except where
noted:

| Hub | Physical port | Kernel devpath | Device | Verified how |
|---|---|---|---|---|
| A | A1 | `1-1` port 1 (`1-1.1`) | Hub B | device identity (Hub B occupies it) |
| A | A2 | `1-1` port 2 | empty | by elimination |
| A | A3 | `1-1` port 4 | ACR122U (NFC reader) | user visually traced cable |
| A | A4 | `1-1` port 3 | BT dongle (TP-Link UB500) | disappeared on unplug |
| B | B1 | `1-1.1.1` | N64 pad | by elimination (B2/B3/B4 confirmed) |
| B | B2 | `1-1.1.2` | N64 pad | **disappeared on unplug** |
| B | B3 | `1-1.1.3` | SNES pad | by elimination |
| B | B4 | `1-1.1.4` | SNES pad | **disappeared on unplug** |

**Important finding — do not assume consistency, even between
identical-model hubs:** Hub A's ports 3/4 are *crossed* (physical A3 is
kernel port 4, physical A4 is kernel port 3), while Hub B's ports map
*straight* 1:1 (physical B*n* is kernel port *n*, directly confirmed for
n=2 and n=4). Both hubs are the same chip (`05e3:0610`) and same
product string, yet wired differently — this is apparently a per-unit
or per-board-revision trait, not a property of the hub model. **Every
individual hub must be verified port-by-port with an actual
unplug/replug test; never infer one hub's wiring from another, even an
apparently identical one.**

Good news for the actual goal: the N64 pads live on Hub B's straight
(non-crossed) pair, B1/B2, so `devicePhysicalPath` values
`0:1.1.1` (B1) and `0:1.1.2` (B2) can be trusted directly.

The Pi-port-dependency question (does the path change if a hub moves to
a different Pi USB port?) is still **not yet verified** — nothing has
been physically moved to test it.

## Goal

Map physically labeled USB hub ports to the internal identifiers
Recalbox/udev actually use (`devicePhysicalPath` in `es_input.cfg`,
`by-path` symlinks), so a specific labeled port always ends up as the
same player slot — and a pad can be swapped to a different labeled port
later and still be addressed/reassigned predictably by that label.

## Known requirements (from prior discussion)

- **Multiple hubs**, not just one — all of their ports need mapping, not
  only a single hub's.
- Every port gets a **physical label** (sticker/marker on the hub
  itself) chosen by the user. The mapping work translates those labels
  into the internal path identifiers — the label is the stable,
  human-facing anchor; the udev path is the implementation detail
  behind it.
- Different pad **models** may be plugged into different ports over
  time (not a fixed 1 pad ↔ 1 port assumption).
- The user intends to always plug a given hub into the **same Pi USB
  port**, to keep that hub's `by-path` chain stable — but flagged this
  might not matter much in practice, since a USB hub is (at least
  partly) identified on its own, independent of which Pi port it's
  connected to. **This needs to actually be verified against this
  specific Pi 5 + these specific hub(s)** — don't assume either way.
  Concretely: check whether `devicePhysicalPath`/by-path for a port
  changes only in the hub-chain segment (stable once that hub is on a
  given Pi port, as currently assumed) or also depends on upstream
  topology in a way that's less predictable than expected.

## Still open from the original brief (unrelated to the hub-count
question above, but needed for the same controller-setup goal)

- Confirm the N64 emulator core actually in use (launch a game, `ps
  aux` on the Pi while it's running).
- Test pad hot-plug slot-renumbering behavior (unplug/replug order
  effects on player slot assignment).
- Check whether `uhubctl` is available/useful once the hub is installed
  (e.g. for power-cycling individual ports during testing).

## Remaining steps

Steps 1-3 of the original plan (label every port, capture each label's
path, build the table) are done — see "Confirmed port mapping" above.
Step 5 (es_input.cfg) is also done, but automated rather than hand-done
once-off — see below.

**2026-10-11 update:** the N64 `es_input.cfg` block is unified, with
C-buttons fixed (all 4 directions, via the `joystick2` "second stick"
convention N64 libretro cores default to) and `hotkey`/`select` both on
the L shoulder (this pad has no Select button). Rather than hand-editing
`es_input.cfg` once for the then-current physical layout, this led to
building `configure_pads.py` (see `ACTION_CARDS.md`) — it detects known
pad models by VID:PID wherever they're currently plugged in and
(re)writes their `devicePhysicalPath`-tagged block automatically,
triggered via a new `sync_pads.sh` action card. Verified pad-model
templates exist for the N64 pad (`0e6d:111d`) and two SNES-style pad
VID:PIDs (`0810:e501`, `081f:e401`) that share one mapping.

Note: the physical layout has since changed from what this file
originally described — B2 now holds an 8BitDo SF30/SN30 pad (wired, not
actually via Bluetooth despite the model name) instead of a second N64
pad; that pad already works via Recalbox's own bundled stock mapping,
no template needed. Wherever the second N64 pad ends up, `sync_pads.sh`
will pick it up without further manual config, since the template
covers the VID:PID regardless of port.

Left to do:

1. Optionally verify the Pi-port-dependency question (plug Hub A into a
   different Pi USB port and see whether only the hub-local segment of
   the path changes) — not yet tested, low priority since the hub
   currently has no reason to move.
2. **In-game verification** of the written N64 mapping — launch an
   actual N64 game and physically confirm A/B, Z, L, and all four
   C-buttons do the right thing. This doubles as the still-open "confirm
   the actual N64 core in use" item below, since some forum reports
   suggest certain cores remap RetroPad buttons to N64 buttons
   non-trivially (e.g. RetroPad-B→N64-A) — unconfirmed for Recalbox's
   actual default core, verify live rather than trust that.
3. Register a card for the new `sync_pads.sh` action via
   `manage_actions.py` on the device (tap a new/blank card through its
   interactive flow) — not yet done, needs to happen on the device
   itself.
