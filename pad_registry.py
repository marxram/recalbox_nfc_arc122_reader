#!/usr/bin/env python3
"""
Registry of every known controller pad for the automatic per-game
controller-assignment feature (see assign_controllers.py), plus USB and
Bluetooth detection and the usbhid-based enable/disable mechanism.

Deliberately NOT a copy of configure_pads.py's DEVICE_TEMPLATES -- that
file owns the actual button-mapping templates (hand-captured, verified
on real hardware, never guessed). This module only adds the two things
configure_pads.py doesn't need: which system a pad is NATIVE for vs. a
valid FALLBACK on, and Bluetooth identity for the pads that have one.
The N64/SNES pads below also have configure_pads.py templates; the
8BitDo pads work with ES's own stored mapping and need none.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from configure_pads import SYSFS_USB, devpath_to_physical_path
from pad_profiles import interface_id, is_bound, set_bound

# Every known pad, keyed by USB VID:PID or Bluetooth MAC. "cls" is the
# pad class used by SYSTEM_PRIORITY below; es_name/es_guid are exactly
# what EmulationStation itself reports for the pad (copied from this
# device's own frontend.log "Added joystick" lines and recalbox.conf
# emulationstation.padN entries, 2026-11-09 -- not derived/computed).
# They are needed to write ES's persistent player-slot order (see
# assign_controllers._write_pad_slots()).
USB_PADS = {
    # N64 pad via "SWITCH CO.,LTD." USB adapter.
    "0e6d:111d": {"cls": "n64_usb", "es_name": "SWITCH CO.,LTD. Controller",
                  "es_guid": "03006a196d0e00001d11000011010000"},
    # SNES-style USB pads (2 different VID:PID, same button layout).
    "0810:e501": {"cls": "snes_usb", "es_name": "usb gamepad",
                  "es_guid": "0300493c1008000001e5000010010000"},
    "081f:e401": {"cls": "snes_usb", "es_name": "USB gamepad",
                  "es_guid": "03004d2a1f08000001e4000010010000"},
    # 8BitDo M30 (Mega Drive pad) wired -- captured 2026-10-24.
    "2dc8:5006": {"cls": "m30_usb", "es_name": "8BitDo 8BitDo M30 gamepad",
                  "es_guid": "03002e5bc82d00000650000011010000"},
    # 8BitDo SF30 Pro wired (recalbox.conf pad9 entry, GUID encodes
    # 2dc8:6001 -- the 2dc8:6101 used here before was the BT modalias).
    "2dc8:6001": {"cls": "sf30_usb", "es_name": "8BitDo 8Bitdo SF30 Pro",
                  "es_guid": "0300260cc82d00000160000011010000"},
}

BT_PADS = {
    "00:00:00:00:00:01": {"cls": "m30_bt", "label": "M30 (Mega Drive)",
                          "es_name": "8BitDo M30 gamepad",
                          "es_guid": "05009b42c82d00005106000000010000"},
    "00:00:00:00:00:02": {"cls": "sf30_bt", "label": "SF30 Pro #1",
                          "es_name": "8Bitdo SF30 Pro",
                          "es_guid": "05005f80c82d00000161000000010000"},
    "00:00:00:00:00:03": {"cls": "sf30_bt", "label": "SF30 Pro #2",
                          "es_name": "8Bitdo SF30 Pro",
                          "es_guid": "05005f80c82d00000161000000010000"},
}

# Per-system player priority, as pad classes in order (user's rules,
# 2026-11-09). A class missing from a system's list is never used for
# it (e.g. N64: only N64 pads and SF30s, never the M30 or SNES pads).
# Within one class: USB in sysfs order, BT in BT_PADS order.
SYSTEM_PRIORITY = {
    "n64":       ["n64_usb", "sf30_usb", "sf30_bt"],
    "megadrive": ["m30_usb", "m30_bt", "snes_usb", "n64_usb", "sf30_usb", "sf30_bt"],
    "snes":      ["snes_usb", "sf30_usb", "sf30_bt", "n64_usb", "m30_usb", "m30_bt"],
}
# Any other system (e.g. vectrex): every pad allowed, USB first.
DEFAULT_PRIORITY = ["snes_usb", "n64_usb", "m30_usb", "sf30_usb", "m30_bt", "sf30_bt"]

USBHID_DRIVER = "/sys/bus/usb/drivers/usbhid"


def usb_connected():
    """[(vid_pid, devpath, physical_path), ...] for every currently
    connected pad in USB_PADS, in stable sysfs-entry-name order. Own
    sysfs scan (not configure_pads.find_known_devices()), because the
    8BitDo pads need no es_input.cfg template and so aren't in
    DEVICE_TEMPLATES."""
    out = []
    if not os.path.isdir(SYSFS_USB):
        return out
    for entry in sorted(os.listdir(SYSFS_USB)):
        base = os.path.join(SYSFS_USB, entry)
        try:
            with open(os.path.join(base, "idVendor")) as f:
                vid = f.read().strip()
            with open(os.path.join(base, "idProduct")) as f:
                pid = f.read().strip()
        except OSError:
            continue
        vid_pid = f"{vid}:{pid}"
        if vid_pid in USB_PADS:
            out.append((vid_pid, entry, devpath_to_physical_path(entry)))
    return out


def bt_connected():
    """[mac, ...] for every BT_PADS entry bluetoothctl currently
    reports as Connected: yes. One `bluetoothctl info` call per known
    pad -- only 3 today, cheap enough not to need batching."""
    connected = []
    for mac in BT_PADS:
        try:
            out = subprocess.run(
                ["bluetoothctl", "info", mac],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except Exception:
            continue
        if any(line.strip() == "Connected: yes" for line in out.splitlines()):
            connected.append(mac)
    return connected


def usb_set_enabled(vid_pid, devpath, enabled):
    """Soft enable/disable one specific currently-connected USB pad via
    usbhid unbind/bind -- same mechanism as pad_profiles.py, but acting
    on one device at a time (the engine decides the full set)."""
    iface = interface_id(devpath)
    if not os.path.exists(os.path.join("/sys/bus/usb/devices", iface)):
        return
    bound = is_bound(iface)
    if enabled and not bound:
        set_bound(iface, True)
    elif not enabled and bound:
        set_bound(iface, False)


# ---------------------------------------------------------------------------
# Per-console button profiles for the N64 pad (0e6d:111d), 2026-11-09.
#
# es_input.cfg is per pad, not per console -- but assign_controllers
# restarts ES anyway whenever the console changes, so it swaps the N64
# pads' <inputConfig> block in that window. N64 games go through
# Recalbox's mupen64 input.xml table, every other console straight
# through RetroArch, so the two need different ES names on the buttons.
#
# Codes: ES id = evdev code - 304 on this pad. 305=A face, 306=B face
# -- PROBABLY WRONG: in Vigilante 8 under libretro mupen64plus_next the
# user found (2026-11-09) 306 = big N64 A, 305 = N64 B. If confirmed,
# the "retro" profile's b/y below are swapped too (SNES B should be the
# big A button). Re-check before relying on it; "n64" profile unaffected
# (it is a verbatim copy of the working live block).
# 313/307/304/312 = C up/down/left/right, 308 = L (user-confirmed: hotkey
# lives there today), 309 = R and 310 = Z (in N64 games r1/309 drives
# "R Trig" and l2/310 "Z Trig", and Mario Kart plays right), 316 = Start.
#
# "n64" = EXACTLY the block live on the device on 2026-11-09 (the v3.4
# mapping that matches the device's stock mupen64 input.xml; the repo's
# v3.5 template + input.xml override in configure_pads.py were never
# deployed). Hotkey+Select on L because the pad has no Select button.
#
# "retro" = for SNES / Mega Drive / everything else via RetroArch:
# L/R are real shoulders (F-Zero!), Hotkey+Select move to Z (rarely
# needed in SNES games, so holding it won't block gameplay input), the
# C buttons provide the missing SNES A/X. SNES B/Y = N64 A/B (the two
# big face buttons). On Mega Drive that gives A=N64 B, B=N64 A, C=C-down.
# ---------------------------------------------------------------------------
_N64_DPAD_STICK = [
    ("joystick1up",   "axis", "1", "-1", "1"),
    ("joystick1left", "axis", "0", "-1", "0"),
    ("left",  "hat", "0", "8", "-1"),
    ("down",  "hat", "0", "4", "-1"),
    ("right", "hat", "0", "2", "-1"),
    ("up",    "hat", "0", "1", "-1"),
]
N64_PAD_PROFILES = {
    "n64": [
        ("joystick2up",   "button", "9", "1", "313"),
        ("joystick2down", "button", "3", "1", "307"),
        ("joystick2left", "button", "0", "1", "304"),
        *_N64_DPAD_STICK,
        ("l2",     "button", "6",  "1", "310"),
        ("r1",     "button", "5",  "1", "309"),
        ("b",      "button", "2",  "1", "306"),
        ("a",      "button", "8",  "1", "312"),
        ("y",      "button", "1",  "1", "305"),
        ("hotkey", "button", "4",  "1", "308"),
        ("select", "button", "4",  "1", "308"),
        ("start",  "button", "12", "1", "316"),
    ],
    "retro": [
        *_N64_DPAD_STICK,
        ("b",      "button", "1",  "1", "305"),  # SNES B  <- N64 A
        ("y",      "button", "2",  "1", "306"),  # SNES Y  <- N64 B
        ("a",      "button", "3",  "1", "307"),  # SNES A  <- C-down
        ("x",      "button", "0",  "1", "304"),  # SNES X  <- C-left
        ("l1",     "button", "4",  "1", "308"),  # SNES L  <- L
        ("r1",     "button", "5",  "1", "309"),  # SNES R  <- R
        ("hotkey", "button", "6",  "1", "310"),  # Hotkey  <- Z
        ("select", "button", "6",  "1", "310"),  # Select  <- Z
        ("start",  "button", "12", "1", "316"),
    ],
}
N64_PAD_VIDPID = "0e6d:111d"


def n64_profile_for(system_key):
    return "n64" if system_key == "n64" else "retro"
