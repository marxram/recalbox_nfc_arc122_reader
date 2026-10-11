#!/bin/sh
# Disable every recognized pad model except SNES-style pads, via a
# reversible usbhid unbind (no ES restart needed - this is equivalent
# to a soft unplug, which ES/RetroArch already handle as a normal
# hotplug event).
echo "[action] pads_only_snes.sh starting at $(date -Iseconds)" >> /tmp/nfc-acr.log
/usr/bin/python3 /recalbox/share/system/nfc-daemon/pad_profiles.py snes >> /tmp/nfc-acr.log 2>&1
echo "[action] pads_only_snes.sh done" >> /tmp/nfc-acr.log
