#!/bin/sh
# Detect known pad models (currently: N64) wherever they're plugged in
# right now and (re)write their es_input.cfg inputConfig block via
# configure_pads.py - backup, stop ES, edit, sync, start ES again, same
# pattern as consolemode_off.sh. Safe to re-run any time pads are moved
# to different hub ports.
CFG=/recalbox/share/system/.emulationstation/es_input.cfg
BACKUP_DIR=/recalbox/share/system/nfc-daemon/config-backups
TS=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR"
cp "$CFG" "$BACKUP_DIR/es_input.cfg.$TS"
echo "[action] sync_pads.sh: backup -> $BACKUP_DIR/es_input.cfg.$TS" >> /tmp/nfc-acr.log

/etc/init.d/S31emulationstation stop
/usr/bin/python3 /recalbox/share/system/nfc-daemon/configure_pads.py >> /tmp/nfc-acr.log 2>&1
sync
/etc/init.d/S31emulationstation start

echo "[action] sync_pads.sh: done" >> /tmp/nfc-acr.log
