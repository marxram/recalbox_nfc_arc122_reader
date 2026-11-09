#!/bin/bash
#================================================================
# NFC ACR122U Card Reader for Recalbox
# Automatic installation / update
#
# Usage:
#   sh install_nfc.sh              First installation
#   sh install_nfc.sh --update     After a Recalbox update
#   sh install_nfc.sh --force      Full reinstall
#================================================================

REPO_URL="https://raw.githubusercontent.com/marxram/recalbox_nfc_arc122_reader/main"
INSTALL_DIR="/recalbox/share/system/nfc-daemon"
SERVICE_SCRIPT="/etc/init.d/S32nfc-acr"
CUSTOM_SH="/recalbox/share/system/custom.sh"
FAKE_DIR="/tmp/fake-card-reader"
SYSFS_DIR="/sys/kernel/recalbox-card-reader"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!!]${NC} $1"; }
error() { echo -e "${RED}[ERR]${NC} $1"; }

MODE="install"
for arg in "$@"; do
    case "$arg" in
        --update|-u)  MODE="update" ;;
        --force|-f)   MODE="force" ;;
    esac
done

echo ""
echo "========================================"
case "$MODE" in
    install) echo " NFC ACR122U - Installation" ;;
    update)  echo " NFC ACR122U - Restore after update" ;;
    force)   echo " NFC ACR122U - Full reinstall" ;;
esac
echo "========================================"
echo ""

mount -o remount,rw / 2>/dev/null || true

#================================================================
# CHECK: already installed? (only for "install" mode)
#================================================================
if [ "$MODE" = "install" ]; then
    if [ -f "$INSTALL_DIR/nfc_acr_daemon.py" ] && [ -f "$SERVICE_SCRIPT" ]; then
        info "Already installed."
        echo ""
        echo " Use --force to reinstall"
        echo " Use --update after a Recalbox update"
        echo ""
        "$SERVICE_SCRIPT" status
        exit 0
    fi
fi

#================================================================
# UPDATE MODE: verify files, fallback to full install if missing
#================================================================
NEED_FULL_INSTALL=0

if [ "$MODE" = "update" ]; then
    MISSING=0
    if [ ! -f "$INSTALL_DIR/nfc_acr_daemon.py" ]; then
        warn "Daemon missing: $INSTALL_DIR/nfc_acr_daemon.py"
        MISSING=1
    fi
    if [ ! -f "$INSTALL_DIR/api_server.py" ]; then
        warn "API module missing: $INSTALL_DIR/api_server.py"
        MISSING=1
    fi
    for f in pad_registry.py assign_controllers.py configure_pads.py pad_profiles.py; do
        if [ ! -f "$INSTALL_DIR/$f" ]; then
            warn "Controller-assignment file missing: $INSTALL_DIR/$f"
            MISSING=1
        fi
    done
    if [ ! -f "$INSTALL_DIR/config.json" ]; then
        warn "Config missing: $INSTALL_DIR/config.json"
        MISSING=1
    fi
    if [ "$MISSING" -eq 1 ]; then
        warn "Missing files detected -> running full installation"
        echo ""
        NEED_FULL_INSTALL=1
    else
        info "Daemon files present"
    fi
fi

#================================================================
# FULL INSTALL (install, force, or update with missing files)
#================================================================
if [ "$MODE" = "install" ] || [ "$MODE" = "force" ] || [ "$NEED_FULL_INSTALL" -eq 1 ]; then

    mkdir -p "$INSTALL_DIR"

    # Download or copy the daemon and every module it imports (all land
    # in the same persistent dir, all need to survive a Recalbox update
    # the same way)
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

    # pad_registry.py/assign_controllers.py (automatic per-game controller
    # assignment) are imported by nfc_acr_daemon.py itself now, and those
    # in turn import configure_pads.py/pad_profiles.py -- all 5 are hard
    # daemon dependencies, not optional action-script helpers, so they
    # need the same survive-updates treatment as the daemon itself.
    # Device-specific data (real card UIDs, BT pad MACs) is gitignored and
    # never downloaded -- copied only if the installer finds your own
    # copy next to it. See game_player_counts.json.example and
    # bt_pads.json.example for the format.
    for f in game_player_counts.json bt_pads.json; do
        if [ -f "$SCRIPT_DIR/$f" ]; then
            cp -f "$SCRIPT_DIR/$f" "$INSTALL_DIR/"
            echo "Installing $f (local data)... OK"
        elif [ ! -f "$INSTALL_DIR/$f" ]; then
            warn "$f not found -- see $f.example (controller assignment works without it, but does nothing)"
        fi
    done

    for f in nfc_acr_daemon.py api_server.py pad_registry.py assign_controllers.py \
             configure_pads.py pad_profiles.py; do
        echo -n "Installing $f... "
        INSTALLED=0

        # Try local file first (tarball install)
        if [ -f "$SCRIPT_DIR/$f" ]; then
            cp -f "$SCRIPT_DIR/$f" "$INSTALL_DIR/"
            INSTALLED=1
        fi

        # Try download if local not found
        if [ "$INSTALLED" -eq 0 ]; then
            if command -v curl >/dev/null 2>&1; then
                curl -sL "$REPO_URL/$f" -o "$INSTALL_DIR/$f"
                [ -s "$INSTALL_DIR/$f" ] && INSTALLED=1
            elif command -v wget >/dev/null 2>&1; then
                wget -q "$REPO_URL/$f" -O "$INSTALL_DIR/$f"
                [ -s "$INSTALL_DIR/$f" ] && INSTALLED=1
            fi
        fi

        if [ "$INSTALLED" -eq 0 ]; then
            error "Could not install $f"
            exit 1
        fi

        # Verify it's not a 404 page
        if head -1 "$INSTALL_DIR/$f" | grep -q "404"; then
            error "Download failed (404) for $f. Check that the repository is public."
            rm -f "$INSTALL_DIR/$f"
            exit 1
        fi

        chmod +x "$INSTALL_DIR/$f"
        info "$f installed"
    done

    # Copy install script to survive updates
    if [ -f "$SCRIPT_DIR/install_nfc.sh" ] && [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
        cp -f "$SCRIPT_DIR/install_nfc.sh" "$INSTALL_DIR/"
        info "Installer copied to $INSTALL_DIR"
    fi

    # Config (preserve existing unless force)
    if [ "$MODE" = "force" ] || [ ! -f "$INSTALL_DIR/config.json" ]; then
        API_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(24))" 2>/dev/null)"
        cat > "$INSTALL_DIR/config.json" << CFGEOF
{
  "buzzer": false,
  "nfc_games": true,
  "nfc_actions": true,
  "api": {"enabled": false, "port": 8080, "token": "$API_TOKEN"}
}
CFGEOF
        info "Config created (buzzer: OFF, nfc_games/nfc_actions: ON, api: OFF)"
        echo " Generated API token (needed once you set api.enabled=true):"
        echo "   $API_TOKEN"
    else
        info "Existing config preserved"
    fi

    # Disable old NFC daemon in custom.sh
    if [ -f "$CUSTOM_SH" ]; then
        if grep -v "^#" "$CUSTOM_SH" 2>/dev/null | grep -q "nfc_reader\|nfc_daemon"; then
            sed -i 's|^sh .*/nfc_reader/.*|# \0 (disabled by nfc installer)|' "$CUSTOM_SH"
            sed -i 's|^python.*nfc_daemon.*|# \0 (disabled by nfc installer)|' "$CUSTOM_SH"
            info "Old NFC daemon disabled in custom.sh"
        fi
    fi

fi

#================================================================
# SERVICE INIT.D (always recreated)
#================================================================
echo -n "Creating init service... "

cat > "$SERVICE_SCRIPT" << 'INITEOF'
#!/bin/bash
DAEMON="/recalbox/share/system/nfc-daemon/nfc_acr_daemon.py"
FAKE_DIR="/tmp/fake-card-reader"
SYSFS_DIR="/sys/kernel/recalbox-card-reader"
PIDFILE="/var/run/nfc-acr.pid"
LOGFILE="/tmp/nfc-acr.log"

start() {
    echo -n "Starting NFC ACR daemon: "
    pkill -f "nfc_daemon.py" 2>/dev/null || true
    pkill -f "nfc_reader" 2>/dev/null || true
    sleep 1
    modprobe recalbox_card_reader 2>/dev/null || true
    modprobe i2c-dev 2>/dev/null || true
    sleep 1
    mkdir -p "$FAKE_DIR"
    echo -n "true"  > "$FAKE_DIR/available"
    echo -n "false" > "$FAKE_DIR/plugged"
    echo -n "0"     > "$FAKE_DIR/uuid"
    echo -n "80"    > "$FAKE_DIR/firmware_version"
    echo -n "80"    > "$FAKE_DIR/module_version"
    echo -n ""      > "$FAKE_DIR/association"
    echo -n ""      > "$FAKE_DIR/reset_card"
    echo -n "false" > "$FAKE_DIR/reversed"
    echo -n "false" > "$FAKE_DIR/specific"
    echo -n "0"     > "$FAKE_DIR/season"
    echo -n "0"     > "$FAKE_DIR/id"
    if [ -d "$SYSFS_DIR" ]; then
        if ! mount | grep -q "on $SYSFS_DIR "; then
            mount --bind "$FAKE_DIR" "$SYSFS_DIR"
        fi
    else
        echo -n "(WARNING: sysfs missing) "
    fi
    start-stop-daemon --start --background --make-pidfile \
        --pidfile "$PIDFILE" \
        --startas /usr/bin/python3 -- "$DAEMON" \
        >> "$LOGFILE" 2>&1
    echo "OK"
}

stop() {
    echo -n "Stopping NFC ACR daemon: "
    start-stop-daemon --stop --pidfile "$PIDFILE" 2>/dev/null || true
    rm -f "$PIDFILE"
    umount "$SYSFS_DIR" 2>/dev/null || true
    echo "OK"
}

restart() { stop; sleep 2; start; }

status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "NFC ACR daemon is running (PID $(cat "$PIDFILE"))"
    else
        echo "NFC ACR daemon is NOT running"
    fi
    if mount | grep -q "on $SYSFS_DIR "; then
        echo "Mount bind: ACTIVE"
    else
        echo "Mount bind: INACTIVE"
    fi
    if [ -f "$FAKE_DIR/plugged" ]; then
        echo "Card plugged: $(cat "$FAKE_DIR/plugged")"
        echo "Card UUID: $(cat "$FAKE_DIR/uuid")"
    fi
}

case "$1" in
    start) start ;; stop) stop ;; restart) restart ;; status) status ;;
    *) echo "Usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
INITEOF

chmod +x "$SERVICE_SCRIPT"
info "Service created: $SERVICE_SCRIPT"

echo ""
echo "========================================"
echo -e " ${GREEN}Done!${NC}"
echo "========================================"
echo ""
echo " Please reboot your Recalbox to start the daemon:"
echo ""
echo "   reboot"
echo ""
echo " Or start manually without reboot:"
echo "   /etc/init.d/S32nfc-acr restart"
echo "   /etc/init.d/S32nfc-acr status"
echo ""
echo " Config: edit $INSTALL_DIR/config.json (or use manage_actions.py)"
echo '   "buzzer"      -> beep on card detection (default: off)'
echo '   "nfc_games"   -> cartridge slot launches games (default: on)'
echo '   "nfc_actions" -> admin/action/restricted cards (default: on)'
echo '   "api.enabled" -> HTTP remote-control API, see api_server.py (default: off)'
echo " Restart the service after any config.json change:"
echo "   /etc/init.d/S32nfc-acr restart"
echo ""
echo " After a Recalbox update:"
echo "   mount -o remount,rw /"
echo "   sh $INSTALL_DIR/install_nfc.sh --update"
echo ""
