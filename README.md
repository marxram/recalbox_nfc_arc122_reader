# NFC ACR122U Card Reader for Recalbox

## What is this?
This system allows you to use a standard **ACR122U USB NFC reader** with
Recalbox's EmulationStation Card Reader feature. Use any NFC card
(NTAG, Mifare, etc.) to associate and launch your retro games.

### Credit

This is a fork of [triplea-trax/recalbox_nfc_arc122_reader](https://github.com/triplea-trax/recalbox_nfc_arc122_reader),
which this project is built on and was inspired by — the core daemon,
the `mount --bind` sysfs trick, and the basic card↔game association flow
are all theirs. This fork adds a case-button-replacement layer (action
cards for reboot/shutdown/console-mode), an admin-gated confirmation
step, admin-gated restricted games, and several reliability fixes found
and fixed while running this on a real, daily-use family device. See
[**What this fork adds**](#what-this-fork-adds) below for the full list.

## How it works
```
ACR122U USB reader
       ↓ (libusb)
Python daemon reads NFC cards
       ↓ (writes files)
/tmp/fake-card-reader/plugged, uuid, etc.
       ↓ (mount --bind)
/sys/kernel/recalbox-card-reader/
       ↓ (reads files)
EmulationStation → detects card → launches game
```

The `mount --bind` command overlays our files onto the sysfs path that
EmulationStation reads. ES sees no difference from a hardware card reader.

## Requirements
- Raspberry Pi 5 with Recalbox
- ACR122U USB NFC reader
- NFC cards (NTAG213/215/216, Mifare Classic, etc.)

## Prerequisite: enable the card-reader device-tree overlay

Everything here relies on `mount --bind` onto
`/sys/kernel/recalbox-card-reader/`. That path only exists if the
`recalbox-card-reader` device-tree overlay is enabled — it is **not**
on by default. Without it, the install still "succeeds" but ES will
never see any card state at all (the init script prints "(WARNING:
sysfs missing)" and simply skips the bind mount).

Add this line to `/boot/recalbox-user-config.txt` (create the file if
it doesn't exist):

```
dtoverlay=recalbox-card-reader
```

Then **reboot** — this is a boot-time device-tree overlay, not
something you can `modprobe`/hot-load afterwards. You can confirm it
took effect after reboot:

```bash
ls /sys/kernel/recalbox-card-reader/   # must exist
dmesg | grep -i card.reader            # "cardreader_probe: ... Card Reader is ready."
```

## Installation
```bash
# SSH into your Pi
ssh root@<pi-ip>

# Download and run the installer
mount -o remount,rw /
mkdir -p /recalbox/share/system/nfc-daemon
curl -sL https://raw.githubusercontent.com/marxram/recalbox_nfc_arc122_reader/main/install_nfc.sh -o /recalbox/share/system/nfc-daemon/install_nfc.sh
sh /recalbox/share/system/nfc-daemon/install_nfc.sh

# Reboot
reboot
```

## Usage
1. Plug the ACR122U into a USB port on the Pi
2. The daemon starts automatically on boot
3. **Red LED** = reader ready, waiting for card
4. Place an NFC card → **Green LED** = card detected
5. In ES, navigate to a game and choose "Set this game for the current card"
6. Next time you place that card, the game launches automatically!

## Commands
```bash
/etc/init.d/S32nfc-acr start    # Start daemon + mount bind
/etc/init.d/S32nfc-acr stop     # Stop daemon + remove mount bind
/etc/init.d/S32nfc-acr restart  # Restart
/etc/init.d/S32nfc-acr status   # Check status
```

## Buzzer Configuration
Edit `/recalbox/share/system/nfc-daemon/config.json`:

```json
{"buzzer": false}   ← silent (default)
{"buzzer": true}    ← beep on card detection
```

Then restart: `/etc/init.d/S32nfc-acr restart`

## LED Behavior
| State | LED | Buzzer |
|-------|-----|--------|
| Waiting | Red | - |
| Card detected | Green | Beep (if enabled) |
| Card removed | Red | - |

## After a Recalbox Update
Recalbox updates **overwrite the system partition**, which deletes the init
script `/etc/init.d/S32nfc-acr`. The daemon files in
`/recalbox/share/system/nfc-daemon/` are **not affected** (data partition).

To restore after an update:
```bash
ssh root@<pi-ip>
mount -o remount,rw /
sh /recalbox/share/system/nfc-daemon/install_nfc.sh --update
reboot
```

If files are missing, `--update` will automatically fall back to a
full installation.

## Installer Options
| Command | Behavior |
|---------|----------|
| `sh install_nfc.sh` | Full install. Skips if already installed. |
| `sh install_nfc.sh --update` | Recreates init script. Full install if files missing. |
| `sh install_nfc.sh --force` | Full reinstall (overwrites everything except card associations). |

## Installed Files

### Data partition (survives updates)
- `/recalbox/share/system/nfc-daemon/nfc_acr_daemon.py` — Main daemon
- `/recalbox/share/system/nfc-daemon/config.json` — Configuration (buzzer)
- `/recalbox/share/system/nfc-daemon/associations.json` — Card↔Game mappings
- `/recalbox/share/system/nfc-daemon/install_nfc.sh` — Installer (for updates)
- `/recalbox/share/system/nfc-daemon/manage_actions.py` — Interactive CLI for admin/action/restricted cards (see `ACTION_CARDS.md`)
- `/recalbox/share/system/nfc-daemon/configure_pads.py` — Controller port-sync tool (see `ACTION_CARDS.md`)
- `/recalbox/share/system/nfc-daemon/actions/` — Action scripts triggered by action cards (see `ACTION_CARDS.md`)

### System partition (overwritten by updates)
- `/etc/init.d/S32nfc-acr` — Auto-start service

### Temporary files
- `/tmp/fake-card-reader/` — Virtual sysfs files
- `/tmp/nfc-acr.log` — Daemon log

## Troubleshooting
| Problem | Solution |
|---------|----------|
| ACR122U not detected | Check `lsusb \| grep ACR` |
| LIBUSB_ERROR_BUSY | Old NFC daemon running: `pkill -f nfc_daemon` |
| ES says "not installed" / no mount bind | `recalbox-card-reader` overlay not enabled — see Prerequisite above (`ls /sys/kernel/recalbox-card-reader/`, add `dtoverlay=recalbox-card-reader` to `/boot/recalbox-user-config.txt`, reboot) |
| Card not detected | Try a different NFC card type |
| LED not lighting up | Check logs: `cat /tmp/nfc-acr.log` |
| Not working after update | Run: `sh /recalbox/share/system/nfc-daemon/install_nfc.sh --update` |

## What this fork adds

None of the following changes how ordinary game cards behave — upstream's
tap-a-card-to-launch flow is untouched. Full details, the `actions.json`
schema, safety rules for the action scripts, and known hardware/platform
gotchas: see [`ACTION_CARDS.md`](ACTION_CARDS.md) and
[`CHANGELOG.md`](CHANGELOG.md).

### Features

- **Action cards** — NFC-triggered reboot, shutdown, and console-mode
  on/off, for devices whose case no longer has physical buttons.
- **Admin window** — tap an admin card to open a 15-second window;
  exactly one action card (or restricted game) tapped inside it is
  consumed immediately, with distinct LED/buzzer feedback for "window
  open" vs. "recognized, running now". Everyday game cards never pass
  through this window at all.
- **Restricted (admin-gated) games** — specific game cards (e.g. 18+
  titles) require the admin window instead of launching directly, so a
  casual tap can't start them.
- **`manage_actions.py`** — an interactive CLI on the Pi to add/remove
  admin/action/restricted cards and scaffold new action scripts, instead
  of hand-editing `actions.json` and tailing logs.

### Fixes

- **Association-file corruption bug** (inherited from upstream, fixed
  here): EmulationStation writes the `association` sysfs file in
  `O_APPEND` mode, so re-associating an already-associated card used to
  concatenate the new entry onto the old one, corrupting
  `associations.json` and breaking all later scans. The daemon now
  tracks its own writes and keeps the file clean. `clean_associations.py`
  one-shot-repairs a file already corrupted by this.
- **Daemon logging silently went nowhere** when launched the normal way
  (`start-stop-daemon --background`): BusyBox redirects the forked
  process's stdout/stderr to `/dev/null` *after* the init script's own
  log redirect, silently winning the race. The daemon now rebinds
  `sys.stdout`/`sys.stderr` to its log file itself at startup.
- **Action scripts wouldn't run on exFAT** — `/recalbox/share` is exFAT
  (`fmask=0133`), so `chmod +x` is a permanent no-op there and gating
  execution on the executable bit always fails. Scripts are invoked via
  `/bin/sh <path>` instead.
- **Shutdown/reboot cards hung the device indefinitely**: raw
  `poweroff`/`reboot` silently no-op when run from this daemon's
  non-interactive subprocess context (no `/dev/initctl`, no utmp on this
  image — BusyBox's `halt` can't determine the runlevel and gives up
  without erroring). Switched to `shutdown -h now` / `shutdown -r now`,
  the same convention Recalbox's own power-button scripts use.
- **Documented** (though it lives outside this repo, in a third-party
  case-vendor script) a `custom.sh` bug that hung *every* shutdown path
  on affected devices — physical power button and ES's own menu
  included, not just these cards — see `ACTION_CARDS.md` for the
  diagnosis and fix.

## Technical Details

### Why mount --bind?
EmulationStation uses `std::ifstream` (C++) to read sysfs files.
The internal glibc call chain goes through hidden symbols
(`__open_nocancel`) that cannot be intercepted by LD_PRELOAD.

`mount --bind` solves this at the filesystem level: **all** accesses are
redirected regardless of the mechanism used by ES.

### Card Reader sysfs files
| File | Permissions | Description |
|------|:-----------:|-------------|
| available | 0444 | "true" if reader is present |
| plugged | 0444 | "true" if a card is placed |
| uuid | 0444 | Numeric card UID (decimal) |
| association | 0644 | System/ROM path for the card |
| reset_card | 0644 | Write to disassociate a card |
| firmware_version | 0444 | Firmware version (80 = up to date) |
| module_version | 0444 | Kernel module version |

### NFC Card UIDs
NTAG cards have a 7-byte UID. The ACR122U reads the first 4 bytes in
little-endian to generate a 32-bit UID.
