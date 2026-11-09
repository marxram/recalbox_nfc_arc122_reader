#!/usr/bin/env python3
"""
Automatic per-game controller assignment -- Stage 1.

Called from nfc_acr_daemon.py's _set_game_state(), before it simulates
the card tap, for both a real physical tap and an API-triggered launch
(both go through that one choke point). See
~/.claude/plans/wild-percolating-anchor.md for the full design and why
this lives here rather than as a Recalbox `[rungame](sync)` userscript.

Mechanism (v3.10): USB pads stay enabled all the time. ES is restarted
only when a wanted pad is connected but sits on the wrong player
(_restart_reason(): N64 <-> SNES switch, SF30s for N64 3-4 players,
M30 as Mega Drive player 1 -- the user's rule). The restart writes ES's
own persistent player-slot list in recalbox.conf (emulationstation.padN
/ padN.path) while ES is stopped. The earlier assumption that ES follows
SDL device-index order was WRONG (found live 2026-11-09, see
_write_pad_slots()): its InputMapper keeps remembered slots, which is
what put the M30 on P1 in Mario Kart 64. Priority per system:
pad_registry.SYSTEM_PRIORITY.

The decision is made from the real current state (remembered slots
reduced to connected pads + the N64 pad's es_input.cfg block), not from
a cache of the last launch, so a pad switched on or off between games
is noticed. Same-console game switches normally need no restart (~1s);
a restart costs ~10-15s, most of it ES's own shutdown.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pad_registry

PLAYER_COUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_player_counts.json")

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


def _desired_player_count(uid_int):
    entry = _load_player_counts().get(str(uid_int))
    if not entry:
        return None, 1
    return SYSTEM_KEY.get(entry["system"]), entry.get("players_max", 1)


def _priority_ordered_pads(system_key):
    """[(identity, kind), ...] -- identity is (vid_pid, devpath,
    physical_path) for USB or a BT MAC string for Bluetooth; kind is
    'usb' or 'bt'. Ordered by pad_registry.SYSTEM_PRIORITY's class list
    for this system; pads whose class isn't listed for it are left out.
    Within a class: USB in stable sysfs order, BT in BT_PADS order --
    deterministic for the same hardware state."""
    usb = pad_registry.usb_connected()
    bt_macs = set(pad_registry.bt_connected())
    priority = pad_registry.SYSTEM_PRIORITY.get(system_key, pad_registry.DEFAULT_PRIORITY)

    ordered = []
    for cls in priority:
        for vid_pid, devpath, phys in usb:
            if pad_registry.USB_PADS[vid_pid]["cls"] == cls:
                ordered.append(((vid_pid, devpath, phys), "usb"))
        for mac, info in pad_registry.BT_PADS.items():
            if info["cls"] == cls and mac in bt_macs:
                ordered.append((mac, "bt"))
    return ordered


RECALBOX_CONF = "/recalbox/share/system/recalbox.conf"
CONF_BACKUP_DIR = "/recalbox/share/system/nfc-daemon/config-backups"
CONF_BACKUPS_KEEP = 20
MAX_PAD_SLOTS = 10  # emulationstation.pad0 .. pad9
_PAD_LINE = re.compile(r"^emulationstation\.pad(\d+)(\.path)?=(.*)$")


def _slot_entry(item, kind):
    """(guid, name, path) exactly as ES stores it in recalbox.conf."""
    if kind == "bt":
        info = pad_registry.BT_PADS[item]
        return (info["es_guid"], info["es_name"], "")
    vid_pid, _devpath, phys = item
    info = pad_registry.USB_PADS[vid_pid]
    return (info["es_guid"], info["es_name"], phys)


def _read_pad_slots():
    """(non-slot lines, [(guid, name, path), ...] in slot order) from
    recalbox.conf. Raises OSError if unreadable. Read-only, safe while
    ES runs."""
    with open(RECALBOX_CONF, encoding="utf-8") as f:
        lines = f.read().splitlines()
    old = {}
    keep = []
    for line in lines:
        m = _PAD_LINE.match(line)
        if not m:
            keep.append(line)
            continue
        slot = int(m.group(1))
        ent = old.setdefault(slot, {"pad": None, "path": ""})
        if m.group(2):
            ent["path"] = m.group(3)
        else:
            ent["pad"] = m.group(3)
    entries = []
    for slot in sorted(old):
        pad = old[slot]["pad"]
        if pad and ":" in pad:
            guid, name = pad.split(":", 1)
            entries.append((guid, name, old[slot]["path"]))
    return keep, entries


def _write_pad_slots(wanted):
    """Found live 2026-11-09: EmulationStation does NOT assign players by
    SDL device-index order -- its InputMapper (es-core/src/input/
    InputMapper.cpp) persists a player-slot list in recalbox.conf
    (emulationstation.padN=GUID:Name, plus padN.path on 10.x for
    telling identical pads apart) and puts each connected pad into the
    slot it remembers. A stale "pad0=...M30..." made the Mega Drive pad
    player 1 in Mario Kart 64 although we had bound the N64 pads first.
    So: while ES is stopped (caller guarantees it), rewrite exactly
    these keys -- the wanted pads in slots 0.., then every previously
    remembered entry not claimed by them, in their old order, so ES's
    memory of currently-disconnected pads survives. Nothing else in the
    file is touched; timestamped backup first (project safety rule)."""
    try:
        keep, old_entries = _read_pad_slots()
    except OSError as e:
        print(f"[AssignControllers] cannot read recalbox.conf: {e}")
        return False

    remaining = list(old_entries)
    for ent in wanted:
        if ent in remaining:
            remaining.remove(ent)
        else:  # same pad, path not remembered yet (e.g. BT, or new port)
            for o in remaining:
                if o[:2] == ent[:2] and (not o[2] or not ent[2]):
                    remaining.remove(o)
                    break
    slots = (list(wanted) + remaining)[:MAX_PAD_SLOTS]
    if slots == old_entries[:MAX_PAD_SLOTS] and len(old_entries) <= MAX_PAD_SLOTS:
        return True  # already in the wanted order

    os.makedirs(CONF_BACKUP_DIR, exist_ok=True)
    backup = os.path.join(CONF_BACKUP_DIR, "recalbox.conf.bak-" + time.strftime("%Y%m%d-%H%M%S") + "-padslots")
    shutil.copy2(RECALBOX_CONF, backup)
    olds = sorted(f for f in os.listdir(CONF_BACKUP_DIR) if f.endswith("-padslots"))
    for f in olds[:-CONF_BACKUPS_KEEP]:
        try:
            os.remove(os.path.join(CONF_BACKUP_DIR, f))
        except OSError:
            pass

    while keep and keep[-1] == "":
        keep.pop()
    out = keep[:]
    for i, (guid, name, path) in enumerate(slots):
        out.append(f"emulationstation.pad{i}={guid}:{name}")
        out.append(f"emulationstation.pad{i}.path={path}")
    tmp = RECALBOX_CONF + ".tmp-padslots"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RECALBOX_CONF)
    print(f"[AssignControllers] pad slots written: {[s[1] + (' @' + s[2] if s[2] else '') for s in slots[:len(wanted)]]} (backup {backup})")
    return True


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


def _starter_pid():
    r = subprocess.run(["pgrep", "-f", r"^/bin/bash /usr/bin/emulationstation-starter"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.split()[0]


def _fix_pidfile():
    """The pidfile must name the emulationstation-STARTER (that's what
    start-stop-daemon -m writes and what `S31emulationstation stop`
    signals: the starter traps SIGTERM, forwards it to ES, waits, and
    does NOT respawn). v3.9/v3.10 wrongly wrote the ES binary's pid
    here; on 2026-11-09 that let a stop signal ES directly while it was
    still initialising after a boot-on-game session -- it took 30s to
    quit, and a second ES was started beside it."""
    pid = _starter_pid()
    if pid:
        try:
            with open(ES_PIDFILE, "w") as f:
                f.write(pid + "\n")
        except OSError:
            pass


ES_STOP_TIMEOUT_S = 60


def _es_stop():
    """Stop ES and wait until BOTH the starter and the ES binary are
    gone. Never returns True while either is alive -- the caller only
    writes recalbox.conf/es_input.cfg and starts a new ES on True.

    Found live 2026-11-09: right after a boot-on-game session ends, ES
    builds its whole UI (~12s) and only acts on SIGTERM afterwards; one
    stop took 30s. Normal stops at the menu take 7-10s. So: generous
    timeout, signal the starter (pidfile, then pgrep fallback for a
    stale pidfile), ES itself only as the last resort."""
    _fix_pidfile()
    subprocess.run([ES_INIT_SCRIPT, "stop"], check=False, env=_es_env())
    deadline = time.time() + ES_STOP_TIMEOUT_S
    nudged = False
    while time.time() < deadline:
        starter, es = _starter_pid(), _real_es_pid()
        if starter is None and es is None:
            return True
        if not nudged and time.time() > deadline - ES_STOP_TIMEOUT_S + 3:
            # init script found no/stale pidfile -> signal what's alive
            target = starter or es
            subprocess.run(["kill", "-TERM", target], check=False)
            nudged = True
        time.sleep(0.5)
    return False


ES_PIDFILE = "/var/run/emulationstation.pid"


def _es_env():
    """Found live 2026-11-09: ES (and every emulator it launches) inherits
    THIS process's environment. PulseAudio listens on
    $XDG_RUNTIME_DIR/pulse/native = /run/user/0/... -- the boot-time rc
    environment sets XDG_RUNTIME_DIR, an SSH shell does not. After the
    daemon had been restarted by hand over SSH, every ES restart from
    here came up without it and games had no sound at all. Always pass
    the boot value explicitly, independent of how the daemon started."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/0")
    return env


def _es_start_and_wait_ready():
    subprocess.run([ES_INIT_SCRIPT, "start"], check=False, env=_es_env())
    deadline = time.time() + ES_READY_TIMEOUT_S
    while time.time() < deadline:
        # v3.10: with the new uid written before the start (BootOnGame)
        # ES goes straight into the emulator and its API never answers
        # until the game ends -- that cost the full 15s timeout and a
        # late "done" beep. A running emulator means ES is up.
        if _game_running():
            _fix_pidfile()
            return True
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "1", "http://localhost:81/api/status"],
                capture_output=True, text=True, timeout=2,
            )
            if r.stdout.strip():
                _fix_pidfile()
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


ES_INPUT_CFG = "/recalbox/share/system/.emulationstation/es_input.cfg"


def _apply_n64_pad_profile(system_key):
    """Write the per-console N64-pad block (pad_registry.N64_PAD_PROFILES)
    for every connected N64 pad, keyed by its real devicePhysicalPath.
    Caller guarantees ES is stopped (it rewrites es_input.cfg on exit).
    Backup first, only if something actually changes."""
    from configure_pads import DEVICE_TEMPLATES, build_block, replace_block
    profile = pad_registry.n64_profile_for(system_key)
    paths = [phys for vid_pid, _d, phys in pad_registry.usb_connected()
             if vid_pid == pad_registry.N64_PAD_VIDPID]
    if not paths:
        return
    with open(ES_INPUT_CFG, encoding="utf-8") as f:
        old = f.read()
    template = dict(DEVICE_TEMPLATES[pad_registry.N64_PAD_VIDPID])
    template["inputs"] = pad_registry.N64_PAD_PROFILES[profile]
    new = old
    for phys in paths:
        new = replace_block(new, phys, build_block(template, phys))
    if new == old:
        return
    os.makedirs(CONF_BACKUP_DIR, exist_ok=True)
    backup = os.path.join(CONF_BACKUP_DIR, "es_input.cfg.bak-" + time.strftime("%Y%m%d-%H%M%S") + "-n64profile")
    shutil.copy2(ES_INPUT_CFG, backup)
    olds = sorted(f for f in os.listdir(CONF_BACKUP_DIR) if f.endswith("-n64profile"))
    for f in olds[:-CONF_BACKUPS_KEEP]:
        try:
            os.remove(os.path.join(CONF_BACKUP_DIR, f))
        except OSError:
            pass
    tmp = ES_INPUT_CFG + ".tmp-n64profile"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ES_INPUT_CFG)
    print(f"[AssignControllers] N64 pad profile '{profile}' written for {paths} (backup {backup})")


def _base_cls(cls):
    """'sf30_usb'/'sf30_bt' -> 'sf30' etc. Wired or Bluetooth doesn't
    matter for "is the right KIND of pad on this player"."""
    return cls.rsplit("_", 1)[0]


def _connected_entries():
    """[(slot_entry, base_cls), ...] for every known pad connected right
    now (USB in sysfs order, then BT)."""
    out = []
    for vid_pid, devpath, phys in pad_registry.usb_connected():
        item = (vid_pid, devpath, phys)
        out.append((_slot_entry(item, "usb"), _base_cls(pad_registry.USB_PADS[vid_pid]["cls"])))
    for mac in pad_registry.bt_connected():
        out.append((_slot_entry(mac, "bt"), _base_cls(pad_registry.BT_PADS[mac]["cls"])))
    return out


def _current_player_classes():
    """Best model of who is player 1, 2, ... in the RUNNING ES: its
    remembered slot list (recalbox.conf) reduced to the pads connected
    right now. Slot entries match a connected pad on GUID+name and path
    (an empty remembered path -- BT, or an old entry -- matches any).
    Connected pads ES has no slot for go last, like ES does with new
    pads. Returns [base_cls, ...] in player order."""
    try:
        _keep, slots = _read_pad_slots()
    except OSError:
        slots = []
    remaining = _connected_entries()
    order = []
    for guid, name, path in slots:
        for k, (ent, cls) in enumerate(remaining):
            if ent[0] == guid and ent[1] == name and (not path or not ent[2] or ent[2] == path):
                order.append(cls)
                del remaining[k]
                break
    order.extend(cls for _ent, cls in remaining)
    return order


_INPUTCONFIG_RE = r'\t<inputConfig[^>]*devicePhysicalPath="{}"[^>]*>.*?</inputConfig>'


def _n64_profile_matches(profile):
    """True if every connected N64 pad's es_input.cfg block already IS
    this profile (read-only, safe while ES runs). No N64 pad -> True."""
    from configure_pads import DEVICE_TEMPLATES, build_block
    paths = [phys for vid_pid, _d, phys in pad_registry.usb_connected()
             if vid_pid == pad_registry.N64_PAD_VIDPID]
    if not paths:
        return True
    try:
        with open(ES_INPUT_CFG, encoding="utf-8") as f:
            cfg = f.read()
    except OSError:
        return False
    template = dict(DEVICE_TEMPLATES[pad_registry.N64_PAD_VIDPID])
    template["inputs"] = pad_registry.N64_PAD_PROFILES[profile]
    for phys in paths:
        m = re.search(_INPUTCONFIG_RE.format(re.escape(phys)), cfg, re.DOTALL)
        if not m or m.group(0) != build_block(template, phys):
            return False
    return True


def _restart_reason(system_key, chosen):
    """None if the running ES already has the right pads in front (no
    restart), else a short reason string. The user's rule (2026-11-09):
    USB pads are always on; restart ES ONLY when a wanted pad is really
    there but on the wrong player --
      * N64 <-> SNES: the N64 pads resp. SNES pads must be players 1..n
        (n = players needed, as far as such pads are connected; for N64
        3-4 players that includes the SF30s, but only if connected) and
        the N64 pad's button profile must match the console.
      * Mega Drive: if an M30 (USB or BT) is connected and is not
        player 1; without an M30 only if player 1 is an N64 pad (then
        the SNES pads go in front). Otherwise keep the current order.
      * Every other system: never."""
    if system_key in ("n64", "snes"):
        want = [_chosen_cls(c) for c in chosen]
        have = _current_player_classes()[:len(want)]
        if have != want:
            return f"players {have} != wanted {want}"
        profile = pad_registry.n64_profile_for(system_key)
        if not _n64_profile_matches(profile):
            return f"N64 pad profile is not '{profile}'"
        return None
    if system_key == "megadrive":
        have = _current_player_classes()[:1]
        if chosen and _chosen_cls(chosen[0]) == "m30":
            if have != ["m30"]:
                return f"M30 connected but player 1 is {have}"
            return None
        # No M30 (user, 2026-11-09, option b): an N64 pad on player 1
        # (left over from an N64 game, with its N64 button profile) is
        # not acceptable -- restart so the SNES pads come first. Any
        # other player 1 is kept.
        if have == ["n64"] and chosen and _chosen_cls(chosen[0]) != "n64":
            return "no M30, but player 1 is an N64 pad"
        return None
    return None


def _chosen_cls(chosen_entry):
    item, kind = chosen_entry
    if kind == "usb":
        return _base_cls(pad_registry.USB_PADS[item[0]]["cls"])
    return _base_cls(pad_registry.BT_PADS[item]["cls"])


def apply_for_launch(uid_int, before_es_start=None):
    """Called right before the daemon simulates a tap for uid_int.
    Returns True if it restarted ES with a new pad order, False if the
    running ES was kept (fast path).

    Found live 2026-10-19: the project's pre-existing "direct API-to-API
    switch is unreliable" bug (documented in ACTION_CARDS.md) is caused
    by the old emulator simply never being told to exit -- the daemon's
    own "force removed edge" (association cleared, 0.3s sleep) is not
    enough on its own. Fix: always make sure no emulator is running
    before returning, regardless of whether the pad configuration
    itself needs to change.

    v3.10 (2026-11-09): USB pads are no longer unbound -- they stay on
    all the time -- and ES is restarted only per _restart_reason()
    (user's rule), checked against the real current state instead of a
    cache of what we did last time."""
    # Step timing (2026-11-09): each step logs its duration so the slow
    # one can be identified from /tmp/nfc-acr.log.
    t_start = time.time()
    t_last = [t_start]

    def _step(label):
        now = time.time()
        print(f"[AssignControllers] timing: {label} {now - t_last[0]:.2f}s "
              f"(total {now - t_start:.2f}s)")
        t_last[0] = now

    _stop_running_game_if_any()
    _step("stop running game")

    # USB pads always on: re-bind any pad an older version (or a
    # pads_only_* action card) had switched off. No-op if bound; with
    # ES running this is a normal hotplug.
    for vid_pid, devpath, _phys in pad_registry.usb_connected():
        pad_registry.usb_set_enabled(vid_pid, devpath, True)

    system_key, desired_count = _desired_player_count(uid_int)
    if system_key is None:
        print(f"[AssignControllers] UID {uid_int} not in game_player_counts.json, skipping")
        return False

    ordered = _priority_ordered_pads(system_key)
    chosen = ordered[:desired_count]
    reason = _restart_reason(system_key, chosen)
    if reason is None:
        _step(f"pads already right for {system_key}, no ES restart")
        return False

    print(f"[AssignControllers] system={system_key} desired={desired_count} "
          f"restart because {reason}; chosen={[(kind, item) for item, kind in chosen]}")

    es_stopped = _es_stop()
    _step("ES stop")
    if es_stopped:
        # Player order: chosen pads first, then EVERY known BT pad not
        # chosen (connected or asleep -- they may wake up after ES
        # started), in this system's priority order, pads not allowed
        # for it last. Then the rest of the system's priority order for
        # connected USB pads (always on now, so they need a defined
        # place too), then whatever ES remembered before.
        wanted = [_slot_entry(item, kind) for item, kind in chosen]
        chosen_macs = {item for item, kind in chosen if kind == "bt"}
        priority = pad_registry.SYSTEM_PRIORITY.get(system_key, pad_registry.DEFAULT_PRIORITY)
        rest = [m for m in pad_registry.BT_PADS if m not in chosen_macs]
        rank = lambda m: (priority.index(pad_registry.BT_PADS[m]["cls"])
                          if pad_registry.BT_PADS[m]["cls"] in priority else len(priority))
        for mac in sorted(rest, key=rank):
            wanted.append(_slot_entry(mac, "bt"))
        for item, kind in ordered[desired_count:]:
            if kind == "usb":
                wanted.append(_slot_entry(item, kind))
        try:
            _write_pad_slots(wanted)
        except Exception as e:
            print(f"[AssignControllers] WARNING: could not write pad slots: {e}")
        try:
            _apply_n64_pad_profile(system_key)
        except Exception as e:
            print(f"[AssignControllers] WARNING: could not write N64 pad profile: {e}")
        _step("write pad slots + N64 profile")
    else:
        # Never edit recalbox.conf while ES might still be alive (it
        # rewrites the file on exit), and never start a SECOND ES next
        # to it (2026-11-09: that happened once). Leave the running ES
        # alone; the daemon writes the card state after we return and
        # ES picks the game up through its normal card-reader poll.
        print("[AssignControllers] WARNING: ES did not stop within "
              f"{ES_STOP_TIMEOUT_S}s -- pad slots NOT written, no ES start")
        return False
    # Hook (v3.9): the daemon writes the NEW card state here, while ES
    # is still stopped, so ES boots straight into the game. Must never
    # break the launch -- on failure the daemon writes the state after
    # we return anyway (old path).
    if before_es_start is not None:
        try:
            before_es_start()
        except Exception as e:
            print(f"[AssignControllers] before_es_start hook failed (non-fatal): {e}")
    if not _es_start_and_wait_ready():
        print("[AssignControllers] WARNING: ES did not report ready in time")
    _step("ES start until API ready")
    return True
