#!/usr/bin/env python3
"""
Automatic per-game controller assignment -- Stage 1.

Called from nfc_acr_daemon.py's _set_game_state(), before it simulates
the card tap, for both a real physical tap and an API-triggered launch
(both go through that one choke point). See
~/.claude/plans/wild-percolating-anchor.md for the full design and why
this lives here rather than as a Recalbox `[rungame](sync)` userscript.

Mechanism (deliberately simple for Stage 1 -- no es_settings.cfg
writing): EmulationStation assigns player slots by SDL device-index
order established once at its own startup (confirmed by reading
EmulationStation's own source, es-core/src/InputManager.cpp), as long
as no INPUT P{n}NAME/GUID pin exists in es_settings.cfg (none do on
this device). Since this module always decides the exact set of pads
bound at the moment ES (re)starts, and binds them in the desired
priority order just before that restart, no explicit pinning is
needed -- ES's own natural fallback assignment does the rest. Confirm
this holds via the live verification steps in the plan before trusting
it blindly.

Caches the last-applied pad set/order (LAST_APPLIED_FILE) so consecutive
launches needing the identical configuration skip the whole stop/
reconfigure/start sequence entirely (~1s, confirmed live) -- this is
what keeps same-system, same-player-count game-to-game switching fast.
When the configuration DOES need to change, measured ~2s from a genuinely
idle state, longer (several seconds, bounded by the SIGTERM-then-SIGKILL
wait in _stop_running_game_if_any()) when an emulator has to be stopped
first -- unavoidable, switching the active game always takes some real
time no matter the mechanism.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pad_registry

PLAYER_COUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_player_counts.json")
LAST_APPLIED_FILE = "/tmp/assign_controllers_last.json"

# RetroKnowledge's system strings -> pad_registry's lowercase system keys.
SYSTEM_KEY = {"SNES": "snes", "Mega Drive": "megadrive", "N64": "n64", "Vectrex": "vectrex"}

ES_INIT_SCRIPT = "/etc/init.d/S31emulationstation"
ES_READY_TIMEOUT_S = 15


def _load_player_counts():
    try:
        with open(PLAYER_COUNTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_last_applied():
    try:
        with open(LAST_APPLIED_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_last_applied(applied):
    try:
        with open(LAST_APPLIED_FILE, "w", encoding="utf-8") as f:
            json.dump(applied, f)
    except OSError as e:
        print(f"[AssignControllers] could not persist last-applied state: {e}")


def _desired_player_count(uid_int):
    entry = _load_player_counts().get(str(uid_int))
    if not entry:
        return None, 1
    return SYSTEM_KEY.get(entry["system"]), entry.get("players_max", 1)


def _priority_ordered_pads(system_key):
    """[(identity, kind), ...] -- identity is (vid_pid, devpath,
    physical_path) for USB or a BT MAC string for Bluetooth; kind is
    'usb' or 'bt'. Native-for-this-system pads first, then fallbacks,
    each group in find_known_devices()'s stable sysfs-entry-name order
    (USB) / dict order (BT) -- deterministic, not re-sorted by anything
    launch-specific, so re-running with the same hardware state always
    produces the same order."""
    usb = pad_registry.usb_connected()
    bt_macs = set(pad_registry.bt_connected())

    native, fallback = [], []
    for vid_pid, devpath, phys in usb:
        cls = pad_registry.PAD_CLASS[vid_pid]
        item = ((vid_pid, devpath, phys), "usb")
        if cls["native"] == system_key:
            native.append(item)
        elif system_key in cls["fallback"]:
            fallback.append(item)

    for mac in pad_registry.SF30_BT_PADS:
        if mac not in bt_macs:
            continue
        cls = pad_registry.SF30_BT_PADS[mac]
        if system_key in cls["fallback"]:
            fallback.append((mac, "bt"))

    return native + fallback


EMULATOR_PATTERNS = ["emulatorlauncher.pyc", "retroarch", "mupen64plus"]


def _game_running():
    for pattern in EMULATOR_PATTERNS:
        if subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0:
            return True
    return False


def _stop_running_game_if_any():
    """Found live 2026-10-19: `S31emulationstation stop` does nothing
    useful while a game is actively running -- ES is blocked waiting on
    its own emulator child and never processes the signal in time, so
    the subsequent `start` collides with the still-alive old ES
    (duplicate-SDL-init crash, confirmed live). Must make sure no
    emulator is actually running before ever touching ES's own
    lifecycle. SIGTERM first (graceful, lets RetroArch/mupen64plus save
    state same as a normal card-removal stop), SIGKILL only if that
    didn't work in time."""
    if not _game_running():
        return
    subprocess.run(["pkill", "-TERM", "-f", "emulatorlauncher.pyc"], check=False)
    for _ in range(20):
        if not _game_running():
            return
        time.sleep(0.5)
    print("[AssignControllers] emulator didn't exit on SIGTERM, forcing")
    for pattern in EMULATOR_PATTERNS:
        subprocess.run(["pkill", "-KILL", "-f", pattern], check=False)
    time.sleep(1)


def _real_es_pid():
    r = subprocess.run(["pgrep", "-f", r"^/usr/bin/emulationstation$"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.split()[0]


def _es_stop():
    # No need to call _stop_running_game_if_any() here -- apply_for_launch()
    # already guarantees that unconditionally before it ever reaches this
    # point (see its docstring for why that has to be unconditional, not
    # just part of the pad-reconfiguration path).
    #
    # Found live 2026-10-19: `S31emulationstation stop` silently no-ops
    # ("The frontend is not started") whenever /var/run/emulationstation.pid
    # is stale -- already a known project gotcha (see ACTION_CARDS.md), but
    # repeated stop/start cycling from THIS feature triggers it far more
    # often than normal use ever did (confirmed: a stale pidfile from one
    # cycle made the next cycle's stop a no-op, so the poll below just
    # waited out its full 10s timeout watching an ES that was never
    # actually signaled). Don't trust the init script alone -- fall back
    # to signaling the real PID (found via pgrep, independent of the
    # pidfile) directly if ES is still alive partway through the wait.
    subprocess.run([ES_INIT_SCRIPT, "stop"], check=False)
    for i in range(20):
        pid = _real_es_pid()
        if pid is None:
            return True
        if i == 5:  # ~2.5s in -- give the init script a fair chance first
            subprocess.run(["kill", "-TERM", pid], check=False)
        time.sleep(0.5)
    return False


ES_PIDFILE = "/var/run/emulationstation.pid"


def _es_start_and_wait_ready():
    subprocess.run([ES_INIT_SCRIPT, "start"], check=False)
    deadline = time.time() + ES_READY_TIMEOUT_S
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "1", "http://localhost:81/api/status"],
                capture_output=True, text=True, timeout=2,
            )
            if r.stdout.strip():
                # Keep the pidfile pointed at the real binary, not
                # whichever wrapper PID the init script last wrote --
                # the launching wrapper process normally exits once ES
                # is up, leaving a stale pidfile for the *next* stop to
                # trip over (see _es_stop()'s comment). Harmless if some
                # other tool expects the wrapper's PID instead; this
                # only has to be good enough for our own next _es_stop().
                pid = _real_es_pid()
                if pid:
                    try:
                        with open(ES_PIDFILE, "w") as f:
                            f.write(pid + "\n")
                    except OSError:
                        pass
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _pad_key(item, kind):
    """Stable, JSON-able identity for one chosen pad -- (vid_pid,
    physical_path) for USB, the MAC string itself for BT. Used only to
    compare "is this the same config as last time", not for the actual
    bind/unbind calls (those need the full usb_connected() tuple)."""
    if kind == "bt":
        return item
    vid_pid, _devpath, phys = item
    return [vid_pid, phys]


def apply_for_launch(uid_int):
    """Called right before the daemon simulates a tap for uid_int.
    Returns True if it changed the pad configuration (ES was restarted),
    False if the existing configuration already matched (no-op, fast
    path).

    Found live 2026-10-19: the project's pre-existing "direct API-to-API
    switch is unreliable" bug (documented in ACTION_CARDS.md) is caused
    by the old emulator simply never being told to exit -- the daemon's
    own "force removed edge" (association cleared, 0.3s sleep) is not
    enough on its own. Confirmed by reproducing it: switching between
    two games needing the SAME pad config (so this module's cache made
    the rest of apply_for_launch() a no-op) silently kept the old game
    running. Fix: always make sure no emulator is running before
    returning, regardless of whether the pad configuration itself needs
    to change -- this subsumes the documented "stop-then-launch"
    workaround into every launch automatically."""
    _stop_running_game_if_any()

    system_key, desired_count = _desired_player_count(uid_int)
    if system_key is None:
        print(f"[AssignControllers] UID {uid_int} not in game_player_counts.json, skipping")
        return False

    ordered = _priority_ordered_pads(system_key)
    chosen = ordered[:desired_count]

    applied_key = {"system": system_key,
                    "chosen": [_pad_key(item, kind) for item, kind in chosen]}
    if _load_last_applied() == applied_key:
        return False

    print(f"[AssignControllers] system={system_key} desired={desired_count} "
          f"chosen={[(kind, item) for item, kind in chosen]}")

    # Disable every known USB pad not in the chosen set, then (re-)bind
    # the chosen ones in priority order -- a bind on an already-bound
    # device is a harmless no-op, so this loop's real job is making
    # sure freshly-(re)bound devices appear in OUR order, which is what
    # establishes SDL's device-index order when ES starts below.
    chosen_usb_physpaths = {item[2] for item, kind in chosen if kind == "usb"}
    for vid_pid, devpath, phys in pad_registry.usb_connected():
        if phys not in chosen_usb_physpaths:
            pad_registry.usb_set_enabled(vid_pid, devpath, False)
    for item, kind in chosen:
        if kind == "usb":
            vid_pid, devpath, phys = item
            pad_registry.usb_set_enabled(vid_pid, devpath, True)

    if not _es_stop():
        print("[AssignControllers] WARNING: ES did not stop cleanly in time")
    if not _es_start_and_wait_ready():
        print("[AssignControllers] WARNING: ES did not report ready in time")

    _save_last_applied(applied_key)
    return True
