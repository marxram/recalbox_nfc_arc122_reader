#!/usr/bin/env python3
"""
Detect known pad models (by USB VID:PID) wherever they're currently
plugged in, and (re)write their <inputConfig> block in es_input.cfg with
a verified-good button mapping, keyed by devicePhysicalPath so several
identical pads on different ports each get their own correct entry.

This never guesses a hardware mapping: every template in DEVICE_TEMPLATES
was captured once, by hand, via EmulationStation's own input wizard plus
evtest on real hardware (see ACTION_CARDS.md / TODO_USB_HUBS.md for how
the N64 template below was derived). To support a new pad model, capture
its mapping the same way first, then add a template - don't invent one.

Must be run with EmulationStation already stopped (the caller - normally
actions/sync_pads.sh - owns that lifecycle and the backup, matching every
other action script in this repo). Safe to re-run any time pads are
moved to different ports: existing blocks for a given devicePhysicalPath
are replaced, not duplicated.
"""
import os
import re
import sys

SYSFS_USB = "/sys/bus/usb/devices"
ES_INPUT_CFG = "/recalbox/share/system/.emulationstation/es_input.cfg"

# Each template was captured by hand on this project's real hardware -
# see ACTION_CARDS.md for the device and the date it was verified.
DEVICE_TEMPLATES = {
    # N64 pad via "SWITCH CO.,LTD." USB adapter - verified 2026-10-11 on
    # the pad plugged into Hub B port 1 (see TODO_USB_HUBS.md).
    "0e6d:111d": {
        "deviceName": "SWITCH CO.,LTD. Controller",
        "deviceGUID": "03006a196d0e00001d11000011010000",
        "deviceNbAxes": "4",
        "deviceNbHats": "1",
        "deviceNbButtons": "13",
        "gamepadtype": "8bitdo",
        # C-buttons as joystick2 (right-stick convention), matching what
        # N64 libretro cores default to - see ACTION_CARDS.md.
        "inputs": [
            ("joystick2up",    "button", "9",  "1",  "313"),
            ("joystick2down",  "button", "8",  "1",  "312"),
            ("joystick2left",  "button", "0",  "1",  "304"),
            ("joystick2right", "button", "3",  "1",  "307"),
            ("joystick1up",    "axis",   "1",  "-1", "1"),
            ("joystick1left",  "axis",   "0",  "-1", "0"),
            ("left",  "hat", "0", "8", "-1"),
            ("down",  "hat", "0", "4", "-1"),
            ("right", "hat", "0", "2", "-1"),
            ("up",    "hat", "0", "1", "-1"),
            # "r1" is bound to the pad's real Z trigger (code 309); "l2"
            # is bound to its real L shoulder (code 310) - named after
            # whichever ES wizard prompt the real button was pressed
            # for, not the pad's own silkscreen labels.
            ("l2", "button", "6",  "1", "310"),
            ("r1", "button", "5",  "1", "309"),
            ("b",  "button", "2",  "1", "306"),
            ("a",  "button", "1",  "1", "305"),
            # Deliberately both on the L shoulder: this pad has no
            # Select button and L is otherwise unused in N64 games.
            ("hotkey", "button", "4", "1", "308"),
            ("select", "button", "4", "1", "308"),
            ("start",  "button", "12", "1", "316"),
        ],
    },
}

# SNES-style pads: two different VID:PIDs (sold under different brands,
# "GeekPi" among them) that turned out to capture byte-for-byte identical
# button codes, so they share one input list - verified 2026-10-11 with
# both 0810:e501 pads simultaneously connected (B3 and B4), which made ES
# auto-stamp devicePhysicalPath on its own for the one just configured
# (B4) - see TODO_USB_HUBS.md.
_SNES_INPUTS = [
    ("left",  "axis", "0", "-1", "0"),
    ("down",  "axis", "1", "1",  "1"),
    ("right", "axis", "0", "1",  "0"),
    ("up",    "axis", "1", "-1", "1"),
    ("r1", "button", "5", "1", "293"),
    ("l1", "button", "4", "1", "292"),
    ("y",  "button", "3", "1", "291"),
    ("x",  "button", "0", "1", "288"),
    ("b",  "button", "2", "1", "290"),
    ("a",  "button", "1", "1", "289"),
    ("hotkey", "button", "8", "1", "296"),
    ("select", "button", "8", "1", "296"),
    ("start",  "button", "9", "1", "297"),
]
DEVICE_TEMPLATES["0810:e501"] = {
    "deviceName": "usb gamepad",
    "deviceGUID": "0300493c1008000001e5000010010000",
    "deviceNbAxes": "2",
    "deviceNbHats": "0",
    "deviceNbButtons": "10",
    "gamepadtype": "8bitdo",
    "inputs": _SNES_INPUTS,
}
DEVICE_TEMPLATES["081f:e401"] = {
    "deviceName": "USB gamepad",
    "deviceGUID": "03004d2a1f08000001e4000010010000",
    "deviceNbAxes": "2",
    "deviceNbHats": "0",
    "deviceNbButtons": "10",
    "gamepadtype": "8bitdo",
    "inputs": _SNES_INPUTS,
}


def find_known_devices():
    """Yield (vid_pid, devpath) for every currently connected device that
    matches a known template, e.g. ("0e6d:111d", "1-1.1.1")."""
    if not os.path.isdir(SYSFS_USB):
        return
    for entry in sorted(os.listdir(SYSFS_USB)):
        base = os.path.join(SYSFS_USB, entry)
        vid_path = os.path.join(base, "idVendor")
        pid_path = os.path.join(base, "idProduct")
        if not (os.path.isfile(vid_path) and os.path.isfile(pid_path)):
            continue
        with open(vid_path) as f:
            vid = f.read().strip()
        with open(pid_path) as f:
            pid = f.read().strip()
        vid_pid = f"{vid}:{pid}"
        if vid_pid in DEVICE_TEMPLATES:
            yield vid_pid, entry


def devpath_to_physical_path(devpath):
    """'1-1.1.1' (sysfs devpath) -> 'platform-xhci-hcd.0-usb-0:1.1.1:1.0'
    (the devicePhysicalPath/by-path format ES uses). Confirmed empirically
    against this device's own /dev/input/by-path symlinks - see
    TODO_USB_HUBS.md - not derived from documentation."""
    if "-" not in devpath:
        raise ValueError(f"unexpected devpath format: {devpath!r}")
    port_chain = devpath.split("-", 1)[1]
    return f"platform-xhci-hcd.0-usb-0:{port_chain}:1.0"


def build_block(template, physical_path):
    attrs = [
        f'deviceName="{template["deviceName"]}"',
        f'deviceGUID="{template["deviceGUID"]}"',
        f'deviceNbAxes="{template["deviceNbAxes"]}"',
        f'deviceNbHats="{template["deviceNbHats"]}"',
        f'deviceNbButtons="{template["deviceNbButtons"]}"',
    ]
    if template.get("gamepadtype"):
        attrs.append(f'gamepadtype="{template["gamepadtype"]}"')
    attrs.append('version="2"')
    attrs.append(f'devicePhysicalPath="{physical_path}"')
    lines = [f'\t<inputConfig type="joystick" {" ".join(attrs)}>']
    for name, itype, iid, value, code in template["inputs"]:
        lines.append(
            f'\t\t<input name="{name}" type="{itype}" id="{iid}" '
            f'value="{value}" code="{code}" />'
        )
    lines.append("\t</inputConfig>")
    return "\n".join(lines)


def replace_block(content, physical_path, new_block):
    """Remove any existing <inputConfig> with this exact
    devicePhysicalPath, then insert new_block before </inputList>."""
    pattern = re.compile(
        r"\t<inputConfig[^>]*devicePhysicalPath=\""
        + re.escape(physical_path)
        + r"\"[^>]*>.*?</inputConfig>\n?",
        re.DOTALL,
    )
    content, n = pattern.subn("", content)
    if n:
        print(f"  replaced existing block for {physical_path}")
    else:
        print(f"  no existing block for {physical_path}, adding new")
    marker = "</inputList>"
    idx = content.rindex(marker)
    content = content[:idx] + new_block + "\n" + content[idx:]
    return content


def main():
    dry_run = "--dry-run" in sys.argv

    if not os.path.isfile(ES_INPUT_CFG):
        print(f"ERROR: {ES_INPUT_CFG} not found", file=sys.stderr)
        return 1

    found = list(find_known_devices())
    if not found:
        print("No known pad models currently connected - nothing to do.")
        return 0

    with open(ES_INPUT_CFG) as f:
        content = f.read()

    changed = False
    for vid_pid, devpath in found:
        physical_path = devpath_to_physical_path(devpath)
        template = DEVICE_TEMPLATES[vid_pid]
        print(f"Found {vid_pid} ({template['deviceName']}) at {devpath} "
              f"-> {physical_path}")
        block = build_block(template, physical_path)
        content = replace_block(content, physical_path, block)
        changed = True

    if not changed:
        return 0

    if dry_run:
        print("--dry-run: not writing changes.")
        return 0

    with open(ES_INPUT_CFG, "w") as f:
        f.write(content)
    print(f"Wrote {ES_INPUT_CFG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
