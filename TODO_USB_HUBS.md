# TODO: Multi-hub USB pad mapping (deferred)

Status: **blocked** until the new USB hub(s) are physically installed.
Not started — this file only captures the requirements discussed so far
so the next session can pick it up directly.

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

## Plan once the hub(s) are physically installed

1. User labels every port on every hub.
2. For each labeled port: plug in a known pad, run `ls -la
   /dev/input/by-path/` (or equivalent — confirm exact approach on this
   Recalbox version) and capture which `by-path`/`devicePhysicalPath`
   value corresponds to that label.
3. Build a label → path table.
4. Verify the Pi-port-dependency question above using that table (plug
   the same hub into a different Pi port, if feasible, and see whether
   only the hub-local segment of the path changes).
5. Only then: unify the two broken N64 `es_input.cfg` blocks into one
   clean block (no `devicePhysicalPath` baked in until the table from
   step 3 exists), fix missing C-buttons, set `hotkey=L`, and bind
   Player 1/2 to the labeled ports from the table. BT 8BitDo pads stay
   as fallback, unaffected by any of this.
