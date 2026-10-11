#!/bin/sh
# Disable every recognized pad model except N64 pads, via a reversible
# usbhid unbind (no ES restart needed - this is equivalent to a soft
# unplug, which ES/RetroArch already handle as a normal hotplug event).
echo "[action] pads_only_n64.sh starting at $(date -Iseconds)" >> /tmp/nfc-acr.log
/usr/bin/python3 /recalbox/share/system/nfc-daemon/pad_profiles.py n64 >> /tmp/nfc-acr.log 2>&1
echo "[action] pads_only_n64.sh done" >> /tmp/nfc-acr.log
