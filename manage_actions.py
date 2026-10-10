#!/usr/bin/env python3
"""
manage_actions.py -- interactive CLI for actions.json
=======================================================
Run this ON THE PI (needs direct USB access to the ACR122U, so it stops
the running nfc-acr daemon for the duration of any card read, and
restarts it afterwards -- which also makes the daemon pick up the
change immediately).

Everything here can also be done by hand (tail /tmp/nfc-acr.log for the
full UID after tapping an unrecognized card, then edit actions.json
directly) -- this just automates that loop and the lookup of which
scripts/actions already exist.

Usage:
  manage_actions.py list
  manage_actions.py add-admin
  manage_actions.py add-action
  manage_actions.py add-restricted
  manage_actions.py new-script NAME [--label LABEL]
  manage_actions.py remove UID
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

import usb1

INSTALL_DIR = "/recalbox/share/system/nfc-daemon"
ACTIONS_FILE = os.path.join(INSTALL_DIR, "actions.json")
ACTIONS_DIR = os.path.join(INSTALL_DIR, "actions")
ASSOC_FILE = os.path.join(INSTALL_DIR, "associations.json")
BACKUP_DIR = os.path.join(INSTALL_DIR, "config-backups")
SERVICE_SCRIPT = "/etc/init.d/S32nfc-acr"

ACR_VID = 0x072F
ACR_PID = 0x2200

SCRIPT_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+\.sh$')

DEFAULT_CFG = {"arm_timeout_s": 15, "admin_uids": [], "actions": {},
               "restricted_uids": []}


# ---------------------------------------------------------------- config ---

def load_actions():
    if not os.path.isfile(ACTIONS_FILE):
        return dict(DEFAULT_CFG)
    with open(ACTIONS_FILE) as f:
        cfg = json.load(f)
    cfg.setdefault("arm_timeout_s", 15)
    cfg.setdefault("admin_uids", [])
    cfg.setdefault("actions", {})
    cfg.setdefault("restricted_uids", [])
    return cfg


def save_actions(cfg):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if os.path.isfile(ACTIONS_FILE):
        ts = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(ACTIONS_FILE, os.path.join(BACKUP_DIR, f"actions.json.{ts}"))
    tmp = ACTIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, ACTIONS_FILE)
    print(f"[ok] saved {ACTIONS_FILE}")


def classify(cfg, full_uid):
    full_uid = full_uid.upper()
    if full_uid in {u.upper() for u in cfg["admin_uids"]}:
        return "admin"
    if full_uid in {k.upper() for k in cfg["actions"]}:
        return "action"
    if full_uid in {u.upper() for u in cfg["restricted_uids"]}:
        return "restricted"
    return None


def load_associations():
    try:
        with open(ASSOC_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


# ------------------------------------------------------------- USB/NFC ---

def connect():
    ctx = usb1.USBContext()
    for dev in ctx.getDeviceList():
        if dev.getVendorID() == ACR_VID and dev.getProductID() == ACR_PID:
            handle = dev.open()
            try:
                if handle.kernelDriverActive(0):
                    handle.detachKernelDriver(0)
            except Exception:
                pass
            handle.claimInterface(0)
            return ctx, handle
    return ctx, None


def send_apdu(handle, apdu):
    ccid = bytes([0x6F, len(apdu) & 0xFF, (len(apdu) >> 8) & 0xFF,
                  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00]) + bytes(apdu)
    handle.bulkWrite(0x02, ccid, timeout=2000)
    return list(handle.bulkRead(0x82, 256, timeout=2000))


def poll_once(handle):
    """Same protocol as nfc_acr_daemon.poll_card(). Returns (uid_int,
    uid_bytes) or (0, None) if nothing is present."""
    try:
        resp = send_apdu(handle, [0xFF, 0x00, 0x00, 0x00, 0x04,
                                   0xD4, 0x4A, 0x01, 0x00])
    except Exception:
        return 0, None
    if len(resp) > 10:
        payload = resp[10:]
        if (len(payload) >= 3 and payload[0] == 0xD5
                and payload[1] == 0x4B and payload[2] >= 1):
            nfcid_len = payload[7] if len(payload) > 7 else 0
            if nfcid_len > 0 and len(payload) > 8 + nfcid_len:
                uid_bytes = payload[8:8 + nfcid_len]
                uid = 0
                for i in range(min(4, len(uid_bytes))):
                    uid |= uid_bytes[i] << (i * 8)
                try:
                    send_apdu(handle, [0xFF, 0x00, 0x00, 0x00, 0x03,
                                        0xD4, 0x52, 0x00])
                except Exception:
                    pass
                return uid, uid_bytes
    try:
        send_apdu(handle, [0xFF, 0x00, 0x00, 0x00, 0x03, 0xD4, 0x52, 0x00])
    except Exception:
        pass
    return 0, None


def format_full_uid(uid_bytes):
    return ":".join(f"{b:02X}" for b in uid_bytes)


def wait_for_tap(handle, timeout=30):
    print(f"Karte jetzt auflegen (Timeout {timeout}s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        uid, uid_bytes = poll_once(handle)
        if uid > 0:
            return uid, format_full_uid(uid_bytes)
        time.sleep(0.3)
    print("[!] Timeout, keine Karte erkannt.")
    return None, None


# -------------------------------------------------------------- service ---

def service(cmd):
    subprocess.run([SERVICE_SCRIPT, cmd], check=False)


class DaemonPaused:
    """Stop the running daemon for exclusive USB access, always restart
    it afterwards (even on error/Ctrl-C) so the reader never ends up
    stuck without its daemon."""

    def __enter__(self):
        print("[..] Stoppe laufenden Daemon fuer exklusiven USB-Zugriff...")
        service("stop")
        time.sleep(1)
        self.ctx, self.handle = connect()
        if not self.handle:
            print("[!] ACR122U nicht gefunden (lsusb pruefen).")
        return self.handle

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.ctx:
                self.ctx.close()
        except Exception:
            pass
        print("[..] Starte Daemon wieder (laedt neue Konfiguration)...")
        service("start")
        return False


# --------------------------------------------------------------- catalog ---

def build_catalog(cfg):
    """script filename -> label, from actions already in actions.json
    plus any .sh file sitting in actions/ that isn't referenced yet."""
    catalog = {}
    for a in cfg["actions"].values():
        script = a.get("script", "")
        if script:
            catalog.setdefault(script, a.get("label", script))
    if os.path.isdir(ACTIONS_DIR):
        for fn in sorted(os.listdir(ACTIONS_DIR)):
            if SCRIPT_NAME_RE.match(fn) and fn not in catalog:
                catalog[fn] = fn[:-3].replace("_", " ").replace("-", " ").title()
    return catalog


def choose_action(cfg):
    catalog = build_catalog(cfg)
    items = sorted(catalog.items())
    print("\nVorhandene Actions:")
    for i, (script, label) in enumerate(items, 1):
        print(f"  {i}) {label}  ({script})")
    print(f"  n) Neue User-Action registrieren")
    print(f"  q) Abbrechen")
    choice = input("Auswahl: ").strip().lower()
    if choice == "q":
        return None
    if choice == "n":
        return new_user_action()
    try:
        idx = int(choice) - 1
        script, label = items[idx]
        return {"label": label, "script": script}
    except (ValueError, IndexError):
        print("[!] Ungueltige Auswahl.")
        return None


def new_user_action():
    label = input("Label (z.B. 'WLAN Hotspot aus'): ").strip()
    name = input("Skriptname ohne .sh (z.B. 'wifi_hotspot_off'): ").strip()
    if not name:
        print("[!] Kein Name angegeben.")
        return None
    script = name if name.endswith(".sh") else f"{name}.sh"
    if not SCRIPT_NAME_RE.match(script):
        print(f"[!] Ungueltiger Dateiname: {script!r} "
              f"(nur Buchstaben/Ziffern/_/- erlaubt)")
        return None
    path = os.path.join(ACTIONS_DIR, script)
    if not os.path.isfile(path):
        create_script(script, label)
    return {"label": label or script, "script": script}


def create_script(script, label):
    os.makedirs(ACTIONS_DIR, exist_ok=True)
    path = os.path.join(ACTIONS_DIR, script)
    template = f"""#!/bin/sh
# {label or script}: user action script, created by manage_actions.py.
# Safety rules (see ACTION_CARDS.md):
# - stop ES before touching recalbox.conf/es_input.cfg
# - sed exactly one key, never rewrite the whole file
# - timestamped backup before every config change
# - clean (non-forced) reboot/poweroff only
echo "[action] {script}: starting" >> /tmp/nfc-acr.log

# TODO: implement

echo "[action] {script}: done" >> /tmp/nfc-acr.log
"""
    with open(path, "w") as f:
        f.write(template)
    print(f"[ok] Skript-Geruest angelegt: {path}")
    print("     (Inhalt noch anpassen -- Ausfuehrbar ist es automatisch,")
    print("      der Daemon ruft es ueber '/bin/sh' auf, exFAT-chmod egal.)")


# ---------------------------------------------------------------- commands ---

def cmd_list(args):
    cfg = load_actions()
    assoc = load_associations()
    print(f"arm_timeout_s: {cfg['arm_timeout_s']}")
    print(f"\nadmin_uids ({len(cfg['admin_uids'])}):")
    for u in cfg["admin_uids"]:
        print(f"  {u}")
    print(f"\nactions ({len(cfg['actions'])}):")
    for u, a in cfg["actions"].items():
        print(f"  {u}  ->  {a.get('label')}  ({a.get('script')})")
    print(f"\nrestricted_uids ({len(cfg['restricted_uids'])}):")
    for u in cfg["restricted_uids"]:
        print(f"  {u}")


def _confirm_overwrite(cfg, full_uid):
    cls = classify(cfg, full_uid)
    if cls:
        ans = input(f"[!] UID {full_uid} ist bereits als '{cls}' registriert. "
                     f"Trotzdem weitermachen/ueberschreiben? [j/N] ").strip().lower()
        return ans in ("j", "y", "ja", "yes")
    return True


def cmd_add_admin(args):
    cfg = load_actions()
    with DaemonPaused() as handle:
        if not handle:
            return
        _, full_uid = wait_for_tap(handle)
        if not full_uid:
            return
        if not _confirm_overwrite(cfg, full_uid):
            return
        if full_uid not in [u.upper() for u in cfg["admin_uids"]]:
            cfg["admin_uids"].append(full_uid)
        save_actions(cfg)
        print(f"[ok] {full_uid} ist jetzt Admin-Karte.")


def cmd_add_action(args):
    cfg = load_actions()
    with DaemonPaused() as handle:
        if not handle:
            return
        _, full_uid = wait_for_tap(handle)
        if not full_uid:
            return
        if not _confirm_overwrite(cfg, full_uid):
            return
        chosen = choose_action(cfg)
        if not chosen:
            print("[!] Abgebrochen.")
            return
        cfg["actions"][full_uid] = chosen
        save_actions(cfg)
        print(f"[ok] {full_uid} -> {chosen['label']} ({chosen['script']})")


def cmd_add_restricted(args):
    cfg = load_actions()
    assoc = load_associations()
    with DaemonPaused() as handle:
        if not handle:
            return
        uid_int, full_uid = wait_for_tap(handle)
        if not full_uid:
            return
        if not _confirm_overwrite(cfg, full_uid):
            return
        name = assoc.get(str(uid_int), "")
        if name:
            print(f"[i] Karte ist aktuell mit '{name}' verknuepft.")
        else:
            print("[i] Karte hat noch keine Spiel-Verknuepfung in "
                  "associations.json -- bitte vorher in ES einmal zuordnen,"
                  " sonst fehlt spaeter der Anzeigename im Log.")
        if full_uid not in [u.upper() for u in cfg["restricted_uids"]]:
            cfg["restricted_uids"].append(full_uid)
        save_actions(cfg)
        print(f"[ok] {full_uid} ist jetzt admin-gated.")


def cmd_new_script(args):
    name = args.name if args.name.endswith(".sh") else f"{args.name}.sh"
    if not SCRIPT_NAME_RE.match(name):
        print(f"[!] Ungueltiger Dateiname: {name!r}")
        return
    path = os.path.join(ACTIONS_DIR, name)
    if os.path.isfile(path):
        print(f"[!] Existiert schon: {path}")
        return
    create_script(name, args.label or "")


def cmd_remove(args):
    cfg = load_actions()
    full_uid = args.uid.upper()
    removed = []
    if full_uid in [u.upper() for u in cfg["admin_uids"]]:
        cfg["admin_uids"] = [u for u in cfg["admin_uids"] if u.upper() != full_uid]
        removed.append("admin_uids")
    if full_uid in cfg["actions"]:
        del cfg["actions"][full_uid]
        removed.append("actions")
    if full_uid in [u.upper() for u in cfg["restricted_uids"]]:
        cfg["restricted_uids"] = [u for u in cfg["restricted_uids"] if u.upper() != full_uid]
        removed.append("restricted_uids")
    if not removed:
        print(f"[!] {full_uid} nicht gefunden.")
        return
    save_actions(cfg)
    print(f"[ok] {full_uid} entfernt aus: {', '.join(removed)}")
    print("[..] Starte Daemon neu, damit die Aenderung sofort greift...")
    service("restart")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("add-admin").set_defaults(func=cmd_add_admin)
    sub.add_parser("add-action").set_defaults(func=cmd_add_action)
    sub.add_parser("add-restricted").set_defaults(func=cmd_add_restricted)

    p_new = sub.add_parser("new-script")
    p_new.add_argument("name")
    p_new.add_argument("--label", default="")
    p_new.set_defaults(func=cmd_new_script)

    p_rm = sub.add_parser("remove")
    p_rm.add_argument("uid")
    p_rm.set_defaults(func=cmd_remove)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
