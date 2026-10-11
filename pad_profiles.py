#!/usr/bin/env python3
"""
Enable or disable whole classes of known USB gamepads by unbinding/
rebinding them from the kernel's usbhid driver - a reversible "soft
unplug" that needs no replugging and works even on hubs that don't
support per-port power switching (most cheap ones don't).

Only ever touches devices whose VID:PID is a known pad model in
configure_pads.py's DEVICE_TEMPLATES - never the NFC reader, Bluetooth
dongle, keyboard/mouse, or any unrecognized device. A profile is just
the set of VID:PIDs that should stay enabled; every other *recognized*
pad model gets disabled.

Disabling a pad this way is equivalent to unplugging it: EmulationStation/
RetroArch already handle that as a normal hotplug event, so no ES
restart is needed here (unlike configure_pads.py's own es_input.cfg
edits).
"""
import os
import sys

from configure_pads import DEVICE_TEMPLATES, find_known_devices

USBHID_DRIVER = "/sys/bus/usb/drivers/usbhid"

PROFILES = {
    "n64": {"0e6d:111d"},
    "snes": {"0810:e501", "081f:e401"},
    "all": set(DEVICE_TEMPLATES.keys()),
}


def interface_id(devpath):
    # Every template captured so far exposes a single interface, :1.0,
    # which is what usbhid actually binds/unbinds against.
    return f"{devpath}:1.0"


def is_bound(iface_id):
    return os.path.isdir(os.path.join(USBHID_DRIVER, iface_id))


def set_bound(iface_id, bound):
    action = "bind" if bound else "unbind"
    path = os.path.join(USBHID_DRIVER, action)
    try:
        with open(path, "w") as f:
            f.write(iface_id)
        return True
    except OSError as e:
        print(f"  {action} {iface_id} failed: {e}")
        return False


def apply_profile(name):
    if name not in PROFILES:
        print(f"Unknown profile {name!r}; known: {', '.join(PROFILES)}")
        return 1

    allowed = PROFILES[name]
    found_any = False
    for vid_pid, devpath in find_known_devices():
        found_any = True
        iface = interface_id(devpath)
        bound = is_bound(iface)
        should_be_bound = vid_pid in allowed
        label = DEVICE_TEMPLATES[vid_pid]["deviceName"]

        if should_be_bound and not bound:
            print(f"Enabling {label} ({vid_pid}) at {devpath}")
            set_bound(iface, True)
        elif not should_be_bound and bound:
            print(f"Disabling {label} ({vid_pid}) at {devpath}")
            set_bound(iface, False)
        else:
            state = "enabled" if bound else "disabled"
            print(f"{label} ({vid_pid}) at {devpath} already {state}")

    if not found_any:
        print("No known pad models currently connected - nothing to do.")
    return 0


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <profile>")
        print(f"Known profiles: {', '.join(PROFILES)}")
        return 1
    return apply_profile(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
