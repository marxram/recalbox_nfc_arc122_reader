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
Left to do:

1. Optionally verify the Pi-port-dependency question (plug Hub A into a
   different Pi USB port and see whether only the hub-local segment of
   the path changes) — not yet tested, low priority since the hub
   currently has no reason to move.
2. Unify the two broken N64 `es_input.cfg` blocks into one clean block,
   using `devicePhysicalPath` `0:1.1.1` (B1) for Player 1 and `0:1.1.2`
   (B2) for Player 2, fix the missing C-buttons, and set `hotkey=L`. BT
   8BitDo pads stay as fallback, unaffected by any of this.
