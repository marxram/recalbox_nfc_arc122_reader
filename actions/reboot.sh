#!/bin/sh
# Clean reboot: stop ES the same way the init script does (SIGTERM, wait for
# exit) so it writes back settings/mappings first, then sync, then
# shutdown -r now. Raw `reboot` has the same non-interactive-subprocess
# runlevel-detection problem as raw `poweroff` - see shutdown.sh.
echo "[action] reboot.sh starting at $(date -Iseconds)" >> /tmp/nfc-acr.log
/etc/init.d/S31emulationstation stop
sync
shutdown -r now
