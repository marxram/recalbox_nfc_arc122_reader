#!/bin/sh
# Leave NFC-only kiosk mode: backup recalbox.conf, stop ES, flip only the
# cardreader.consolemode line (sed on the exact key, never rewrite the whole
# file), sync, start ES again so it picks up the new setting.
CONF=/recalbox/share/system/recalbox.conf
BACKUP_DIR=/recalbox/share/system/nfc-daemon/config-backups
TS=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR"
cp "$CONF" "$BACKUP_DIR/recalbox.conf.$TS"
echo "[action] consolemode_off.sh: backup -> $BACKUP_DIR/recalbox.conf.$TS" >> /tmp/nfc-acr.log

/etc/init.d/S31emulationstation stop
sed -i 's/^cardreader\.consolemode=.*/cardreader.consolemode=0/' "$CONF"
sync
/etc/init.d/S31emulationstation start

echo "[action] consolemode_off.sh: done, consolemode=0" >> /tmp/nfc-acr.log
