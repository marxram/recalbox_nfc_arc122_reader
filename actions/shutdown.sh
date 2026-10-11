#!/bin/sh
# Clean shutdown: same ES-stop + sync as reboot.sh, then shutdown -h now.
# Raw `poweroff` silently no-ops when run from a non-interactive daemon
# subprocess on this image (no /dev/initctl, no utmp - it can't determine
# the runlevel and falls back to a "soft poweroff" that does nothing).
# `shutdown -h now` is what Recalbox's own power-button scripts
# (powerswitch.sh, rpi-pin*-power.py) use, and it's the one that actually
# drives /etc/init.d/rcK through to a real halt.
echo "[action] shutdown.sh starting at $(date -Iseconds)" >> /tmp/nfc-acr.log
/etc/init.d/S31emulationstation stop
sync
touch /tmp/poweroff.please
shutdown -h now
