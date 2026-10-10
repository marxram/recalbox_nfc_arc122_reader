#!/bin/sh
# Clean shutdown: same ES-stop + sync as reboot.sh, then a real (non-forced)
# poweroff so busybox still runs the normal init shutdown sequence.
echo "[action] shutdown.sh starting at $(date -Iseconds)" >> /tmp/nfc-acr.log
/etc/init.d/S31emulationstation stop
sync
poweroff
