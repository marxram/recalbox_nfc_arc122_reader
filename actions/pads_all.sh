#!/bin/sh
# Re-enable every recognized pad model (reset after pads_only_n64.sh /
# pads_only_snes.sh). No ES restart needed - see those scripts.
echo "[action] pads_all.sh starting at $(date -Iseconds)" >> /tmp/nfc-acr.log
/usr/bin/python3 /recalbox/share/system/nfc-daemon/pad_profiles.py all >> /tmp/nfc-acr.log 2>&1
echo "[action] pads_all.sh done" >> /tmp/nfc-acr.log
