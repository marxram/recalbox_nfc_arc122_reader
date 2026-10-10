# NFC ACR122U Card Reader for Recalbox

## What is this?
This system allows you to use a standard **ACR122U USB NFC reader** with
Recalbox's EmulationStation Card Reader feature. Use any NFC card
(NTAG, Mifare, etc.) to associate and launch your retro games.

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

## Installation
```bash
# SSH into your Pi
ssh root@<pi-ip>

# Download and run the installer
mount -o remount,rw /
mkdir -p /recalbox/share/system/nfc-daemon
curl -sL https://raw.githubusercontent.com/triplea-trax/recalbox_nfc_arc122_reader/main/install_nfc.sh -o /recalbox/share/system/nfc-daemon/install_nfc.sh
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
| ES says "not installed" | Kernel module missing: `modprobe recalbox_card_reader` |
| Card not detected | Try a different NFC card type |
| LED not lighting up | Check logs: `cat /tmp/nfc-acr.log` |
| Not working after update | Run: `sh /recalbox/share/system/nfc-daemon/install_nfc.sh --update` |

## Custom additions on this fork

This fork adds, on top of everything above:

- **Action cards** — NFC-triggered reboot/shutdown/console-mode scripts,
  for devices whose case has no physical buttons anymore.
- **Admin window** — an admin card opens a 15s window during which
  exactly one action card (or restricted game) is accepted, with distinct
  LED/buzzer feedback for "window open" vs. "recognized, running now".
- **Restricted (admin-gated) games** — specific game cards (e.g. 18+
  titles) require the admin window instead of launching directly.

None of this touches how ordinary game cards behave. Full details,
`actions.json` schema, safety rules for the action scripts, and known
hardware gotchas: see [`ACTION_CARDS.md`](ACTION_CARDS.md).

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
