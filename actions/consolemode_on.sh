#!/bin/sh
# Re-enter NFC-only kiosk mode. Mirror of consolemode_off.sh.
CONF=/recalbox/share/system/recalbox.conf
BACKUP_DIR=/recalbox/share/system/nfc-daemon/config-backups
TS=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR"
cp "$CONF" "$BACKUP_DIR/recalbox.conf.$TS"
echo "[action] consolemode_on.sh: backup -> $BACKUP_DIR/recalbox.conf.$TS" >> /tmp/nfc-acr.log

/etc/init.d/S31emulationstation stop
sed -i 's/^cardreader\.consolemode=.*/cardreader.consolemode=1/' "$CONF"
sync
/etc/init.d/S31emulationstation start

echo "[action] consolemode_on.sh: done, consolemode=1" >> /tmp/nfc-acr.log
