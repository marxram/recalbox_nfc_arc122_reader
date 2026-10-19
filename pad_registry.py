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
Every USB VID:PID below must already exist in configure_pads.py's
DEVICE_TEMPLATES -- this module imports it rather than duplicating it.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from configure_pads import DEVICE_TEMPLATES, find_known_devices, devpath_to_physical_path
from pad_profiles import interface_id, is_bound, set_bound

# Which system each known pad is NATIVE for, and which systems it's a
# valid FALLBACK on (a cross-system substitute when the native pad(s)
# aren't enough/present). Keys must match configure_pads.py's
# DEVICE_TEMPLATES exactly.
PAD_CLASS = {
    # N64 pad via "SWITCH CO.,LTD." USB adapter.
    "0e6d:111d": {"native": "n64", "fallback": ("megadrive", "snes")},
    # SNES-style USB pads (2 different VID:PID, same button layout).
    "0810:e501": {"native": "snes", "fallback": ("megadrive",)},
    "081f:e401": {"native": "snes", "fallback": ("megadrive",)},
    # 8BitDo SF30 Pro over USB cable. VID:PID read directly from this
    # pad's own `bluetoothctl info` output (Modalias: usb:v2DC8p6101),
    # not guessed -- but NOT yet verified to actually launch/map
    # correctly wired (only tested wired wasn't done this session; BT
    # is the normal mode for these two pads). No es_input.cfg template
    # needed either way -- works via Recalbox's own stock mapping (see
    # TODO_USB_HUBS.md). Treat "fallback: any" loosely -- it's a
    # generic pad, no system-specific button legend to clash with.
    "2dc8:6101": {"native": None, "fallback": ("n64", "megadrive", "snes")},
    # Mega Drive pad: VID:PID NOT YET CAPTURED -- hardware not
    # connected to this project as of 2026-10-19. Do not guess it;
    # add the real entry (and its configure_pads.py template, if it
    # turns out to need one) once the pad is physically available,
    # the same way every other template here was captured.
}

# 8BitDo SF30 Pro pads over Bluetooth -- BT MAC -> info. Confirmed via
# `bluetoothctl devices` on 2026-10-19 (both already paired). No
# es_input.cfg template needed, same as the wired USB identity above.
SF30_BT_PADS = {
    "00:00:00:00:00:02": {"label": "SF30 Pro #1", "fallback": ("n64", "megadrive", "snes")},
    "00:00:00:00:00:03": {"label": "SF30 Pro #2", "fallback": ("n64", "megadrive", "snes")},
}

USBHID_DRIVER = "/sys/bus/usb/drivers/usbhid"


def usb_connected():
    """[(vid_pid, devpath, physical_path), ...] for every currently
    connected pad known to PAD_CLASS (a subset of configure_pads.py's
    full DEVICE_TEMPLATES -- e.g. the N64/SNES templates are also used
    standalone by pad_profiles.py's manual profiles)."""
    out = []
    for vid_pid, devpath in find_known_devices():
        if vid_pid in PAD_CLASS:
            out.append((vid_pid, devpath, devpath_to_physical_path(devpath)))
    return out


def bt_connected():
    """[mac, ...] for every SF30_BT_PADS entry bluetoothctl currently
    reports as Connected: yes. One `bluetoothctl info` call per known
    pad -- only 2 today, cheap enough not to need batching."""
    connected = []
    for mac in SF30_BT_PADS:
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
    bound = is_bound(iface)
    if enabled and not bound:
        set_bound(iface, True)
    elif not enabled and bound:
        set_bound(iface, False)
