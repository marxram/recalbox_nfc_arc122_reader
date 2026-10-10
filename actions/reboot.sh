#!/bin/sh
# Clean reboot: stop ES the same way the init script does (SIGTERM, wait for
# exit) so it writes back settings/mappings first, then sync, then a real
# (non-forced) reboot so busybox still runs the normal init shutdown sequence.
echo "[action] reboot.sh starting at $(date -Iseconds)" >> /tmp/nfc-acr.log
/etc/init.d/S31emulationstation stop
sync
reboot
