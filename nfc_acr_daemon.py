#!/usr/bin/env python3
"""
NFC ACR122U Card Reader Daemon for Recalbox
============================================
Reads NFC cards from ACR122U USB reader and writes card state
to /tmp/fake-card-reader/ files. Combined with mount --bind,
EmulationStation sees these as the real sysfs card-reader files.

Architecture:
  ACR122U USB -> daemon Python -> /tmp/fake-card-reader/ -> mount --bind -> ES

BUGFIX (v2): EmulationStation writes to the 'association' file in APPEND
mode (O_APPEND). Each new association made by the user was concatenated
to the previous value instead of replacing it, corrupting the file and
associations.json. The daemon now tracks what it wrote itself and
extracts only the NEW part that ES appended, even if ES appended
multiple entries in rapid succession.

ADMIN WINDOW (v4): cards listed in actions.json ("actions"/"restricted_uids"/
"admin_uids") are never forwarded to ES directly. Presenting the admin card
opens a window for arm_timeout_s (slow red/green blink = "window open,
waiting"); presenting a second admin card while it's open just resets the
timer ("extended"). The FIRST action or restricted-game card presented
inside that window is consumed immediately: a fast blink+beep burst signals
recognition (important since reboot/shutdown can cut power within a
second), then the script runs (or the restricted game is forwarded to ES
exactly like a normal game card), and the window closes -- no further
action/restricted card is accepted until the next admin tap. A timeout with
nothing presented just closes the window. Normal game cards are completely
unaffected by any of this and always launch immediately, window or not.
Game-card UID handling (4-byte little-endian int, used as the
associations.json key) is unchanged for backward compatibility;
action/admin/restricted classification uses the FULL UID (all bytes from
the D4 4A response) so cards don't collide.
"""

import os
import re
import sys
import time
import json
import signal
import subprocess
import threading
import usb1

# The init script starts this daemon via BusyBox's
# `start-stop-daemon -b/--background`, which dup2()s the forked child's
# fd 0/1/2 to /dev/null itself -- that happens AFTER the shell's own
# `>> LOGFILE` redirect takes effect, so it silently wins. print() has
# therefore always gone to /dev/null when run through the init script,
# never to /tmp/nfc-acr.log, however the daemon is launched. Open the log
# file ourselves and rebind sys.stdout/stderr to it (line-buffered) so
# logging works regardless of how/whether the launcher redirects fds.
LOG_FILE = "/tmp/nfc-acr.log"
try:
    _log_fh = open(LOG_FILE, "a", buffering=1)
    sys.stdout = _log_fh
    sys.stderr = _log_fh
except Exception:
    pass

FAKE_DIR = "/tmp/fake-card-reader"
ASSOC_FILE = "/recalbox/share/system/nfc-daemon/associations.json"
CONFIG_FILE = "/recalbox/share/system/nfc-daemon/config.json"
ACTIONS_FILE = "/recalbox/share/system/nfc-daemon/actions.json"
ACTIONS_DIR = "/recalbox/share/system/nfc-daemon/actions"
PLAYLOG_FILE = "/recalbox/share/system/nfc-daemon/play_log.jsonl"
ACR_VID = 0x072f
ACR_PID = 0x2200

# Regex pour détecter un UUID format 8-4-4-4-12 hex suivi de ||
UUID_HEADER_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\|\|'
)

# Whitelist: only a bare filename matching this pattern, existing inside
# ACTIONS_DIR, may ever be executed. No paths or arguments come from
# actions.json or from the card itself.
SCRIPT_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+\.sh$')


class ACRDaemon:
    def __init__(self):
        self.running = True
        self.plugged = False
        self.uuid = 0
        self.associations = {}
        self.handle = None
        self.ctx = None
        self.buzzer = False
        self.nfc_games = True
        self.nfc_actions = True
        self.api_enabled = False
        self.api_port = 8080
        self.api_token = ""
        self._api_httpd = None

        # Guards every mutation of the fake-card-reader files and every
        # whitelisted-script launch -- the hardware poll loop and the
        # optional HTTP API thread can both call into these now.
        self._lock = threading.Lock()

        # True when the current "card plugged" state was set by the API,
        # not by a real tap. Lets an API-launched game survive the poll
        # loop's own "no real card seen" auto-clear (see run()), while a
        # genuine physical tap still always takes over immediately.
        self.virtual_card = False

        # Play-log session tracking (see _session_open/_session_close).
        # None = no session currently open.
        self._session_start = None
        self._session_uid = None
        self._session_source = None

        # BUGFIX: mémoire de la dernière valeur écrite dans "association".
        # Sert à distinguer ce que le daemon a écrit lui-même de ce que ES
        # a appendé derrière en mode O_APPEND.
        self._last_written_assoc = ""

        # Full-UID presence tracking, independent of self.plugged/self.uuid
        # (which stay reserved for the ES-visible game-card state).
        self._cur_full_uid = None
        self._miss_count = 0

        # Admin-window state machine (IDLE / WINDOW_OPEN). See module
        # docstring ("ADMIN WINDOW (v4)") for the full flow.
        self.admin_uids = set()
        self.actions = {}
        self.arm_timeout_s = 15.0
        self.window_open = False
        self.window_deadline = 0.0
        self._window_tick_time = 0.0
        self._window_tick_phase = False

        os.makedirs(FAKE_DIR, exist_ok=True)
        self.load_associations()
        self.load_config()
        self.load_actions()
        self.init_files()

        signal.signal(signal.SIGTERM, self._sig)
        signal.signal(signal.SIGINT, self._sig)

    def _sig(self, sig, frame):
        print(f"[ACR] Signal {sig}, stopping...")
        self.running = False

    def load_associations(self):
        try:
            with open(ASSOC_FILE, 'r') as f:
                self.associations = json.load(f)
            print(f"[ACR] Loaded {len(self.associations)} associations")
        except:
            self.associations = {}

    def save_associations(self):
        try:
            with open(ASSOC_FILE, 'w') as f:
                json.dump(self.associations, f, indent=2)
        except Exception as e:
            print(f"[ACR] Save error: {e}")

    def load_config(self):
        """Missing file or any read error = every flag keeps its default
        (buzzer off, NFC games+actions on, API off) -- same fallback
        behaviour as before this file gained more keys."""
        try:
            with open(CONFIG_FILE, 'r') as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        self.buzzer = cfg.get("buzzer", False)
        self.nfc_games = cfg.get("nfc_games", True)
        self.nfc_actions = cfg.get("nfc_actions", True)
        api_cfg = cfg.get("api") or {}
        self.api_enabled = bool(api_cfg.get("enabled", False))
        self.api_port = int(api_cfg.get("port", 8080))
        self.api_token = api_cfg.get("token", "") or ""
        print(f"[ACR] Config: buzzer={self.buzzer} nfc_games={self.nfc_games} "
              f"nfc_actions={self.nfc_actions} api_enabled={self.api_enabled}")

    def load_actions(self):
        """Load actions.json (optional). Missing file = feature disabled,
        every card behaves exactly like before."""
        self.admin_uids = set()
        self.actions = {}
        self.restricted_uids = set()
        self.arm_timeout_s = 15.0
        try:
            with open(ACTIONS_FILE, 'r') as f:
                cfg = json.load(f)
            self.arm_timeout_s = float(cfg.get("arm_timeout_s", 15))
            self.admin_uids = {u.upper() for u in cfg.get("admin_uids", [])}
            self.actions = {k.upper(): v for k, v in cfg.get("actions", {}).items()}
            self.restricted_uids = {u.upper() for u in cfg.get("restricted_uids", [])}
            print(f"[ACR] Actions config: {len(self.actions)} action(s), "
                  f"{len(self.admin_uids)} admin UID(s), "
                  f"{len(self.restricted_uids)} restricted game UID(s), "
                  f"timeout={self.arm_timeout_s}s")
        except FileNotFoundError:
            print("[ACR] No actions.json, two-stage action cards disabled")
        except Exception as e:
            print(f"[ACR] actions.json load error: {e}")

    def init_files(self):
        """Create all sysfs-like files with initial values"""
        files = {
            "available": "true",
            "plugged": "false",
            "uuid": "0",
            "firmware_version": "80",
            "module_version": "80",
            "reversed": "false",
            "specific": "false",
            "season": "0",
            "id": "0",
            "association": "",
            "reset_card": "",
        }
        for name, value in files.items():
            with open(os.path.join(FAKE_DIR, name), 'w') as f:
                f.write(value)

    def write_state(self):
        uid_str = str(self.uuid)
        assoc = self.associations.get(uid_str, "")
        writes = {
            "plugged": "true" if self.plugged else "false",
            "uuid": uid_str if self.plugged else "0",
            "id": uid_str if self.plugged else "0",
            "association": assoc,
        }
        for name, value in writes.items():
            with open(os.path.join(FAKE_DIR, name), 'w') as f:
                f.write(value)
        # BUGFIX: mémoriser ce qu'on vient d'écrire dans "association"
        self._last_written_assoc = assoc

    @staticmethod
    def _extract_last_assoc(text):
        """Si text contient plusieurs entrées 'UUID||path' concaténées,
        retourne la dernière. Sinon retourne text tel quel."""
        matches = list(UUID_HEADER_RE.finditer(text))
        if len(matches) > 1:
            return text[matches[-1].start():]
        return text

    def check_association_write(self):
        """Check if ES wrote a new association.

        ES ouvre le fichier 'association' en mode O_APPEND, donc chaque
        écriture est AJOUTÉE au contenu existant au lieu de le remplacer.
        On compare ce qu'on lit à ce qu'on a écrit soi-même pour isoler
        uniquement la partie ajoutée par ES.
        """
        try:
            path = os.path.join(FAKE_DIR, "association")
            with open(path, 'r') as f:
                raw = f.read()

            if not self.plugged:
                return

            # Déterminer ce qu'ES a ajouté par-dessus ce que le daemon a écrit
            prefix = self._last_written_assoc
            if raw.startswith(prefix) and len(raw) > len(prefix):
                new_part = raw[len(prefix):].strip()
            elif raw == prefix:
                # Rien de nouveau, juste ce qu'on a écrit
                return
            else:
                # Contenu inattendu (race ou état incohérent)
                # → fallback : extraire la dernière entrée UUID du tout
                new_part = self._extract_last_assoc(raw).strip()

            if not new_part:
                return

            # Si la nouvelle partie contient elle-même plusieurs entrées
            # (ES a fait plusieurs append rapidement), garder la dernière
            new_assoc = self._extract_last_assoc(new_part).strip()
            if not new_assoc:
                return

            uid_str = str(self.uuid)
            if self.associations.get(uid_str) != new_assoc:
                with self._lock:
                    self.associations[uid_str] = new_assoc
                    self.save_associations()
                print(f"[ACR] Association: UID={uid_str} -> {new_assoc}")

                # Nettoyer le fichier : garder uniquement la nouvelle assoc
                # → empêche toute concaténation future de s'amplifier
                with open(path, 'w') as f:
                    f.write(new_assoc)
                self._last_written_assoc = new_assoc
        except Exception as e:
            print(f"[ACR] assoc check error: {e}")

        # === Reset card (inchangé) ===
        try:
            path = os.path.join(FAKE_DIR, "reset_card")
            with open(path, 'r') as f:
                content = f.read().strip()
            if content and content not in ("0", ""):
                uid_str = str(self.uuid)
                if uid_str in self.associations:
                    with self._lock:
                        del self.associations[uid_str]
                        self.save_associations()
                    print(f"[ACR] Reset association for UID={uid_str}")
                with open(path, 'w') as f:
                    f.write("")
                # Après reset, invalider le prefix mémorisé
                self._last_written_assoc = ""
        except:
            pass

    def led_command(self, state, buzz=False):
        """LED/buzzer: 'green' = card detected, 'red' = no card/idle.

        Only these two calls are used anywhere, including for the ARMED
        blink -- they are the ones proven to work against the real reader.
        A first attempt added extra ctrl-byte/repeat-count values guessed
        without a datasheet for a continuous hardware blink; on real
        hardware that left the reader stuck showing both LEDs solid and,
        worse, no longer responding to new card taps until the process
        was restarted. ARMED feedback is now just these two calls,
        alternated from the main loop (see run()), so nothing here is
        unverified.
        """
        try:
            if state == 'green':
                self.send_apdu([0xFF, 0x00, 0x40, 0x0E, 0x04,
                                0x02, 0x01, 0x01, 0x01 if buzz else 0x00])
            elif state == 'red':
                self.send_apdu([0xFF, 0x00, 0x40, 0x0D, 0x04,
                                0x00, 0x00, 0x00, 0x00])
        except:
            pass

    def connect_acr(self):
        try:
            if self.ctx:
                try:
                    self.ctx.close()
                except:
                    pass
            self.ctx = usb1.USBContext()
            for dev in self.ctx.getDeviceList():
                if dev.getVendorID() == ACR_VID and dev.getProductID() == ACR_PID:
                    self.handle = dev.open()
                    try:
                        if self.handle.kernelDriverActive(0):
                            self.handle.detachKernelDriver(0)
                    except:
                        pass
                    self.handle.claimInterface(0)
                    print("[ACR] ACR122U connected!")
                    self.led_command('red')
                    return True
        except Exception as e:
            print(f"[ACR] Connect error: {e}")
            self.handle = None
        return False

    def send_apdu(self, apdu):
        ccid = bytes([0x6F, len(apdu) & 0xFF, (len(apdu) >> 8) & 0xFF,
                      0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00]) + bytes(apdu)
        self.handle.bulkWrite(0x02, ccid, timeout=2000)
        return list(self.handle.bulkRead(0x82, 256, timeout=2000))

    def poll_card(self):
        """Poll for NFC card. Returns (uid_int, uid_bytes) with uid_int the
        existing 4-byte little-endian value (unchanged, used for ES/
        associations.json), uid_bytes the FULL NFCID from the reader
        (used only for action/admin classification). (0, None) if no card,
        (-1, None) on USB error."""
        try:
            resp = self.send_apdu([0xFF, 0x00, 0x00, 0x00, 0x04,
                                   0xD4, 0x4A, 0x01, 0x00])
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
                        self.send_apdu([0xFF, 0x00, 0x00, 0x00, 0x03,
                                        0xD4, 0x52, 0x00])
                        return uid, uid_bytes
            try:
                self.send_apdu([0xFF, 0x00, 0x00, 0x00, 0x03,
                                0xD4, 0x52, 0x00])
            except:
                pass
            return 0, None
        except usb1.USBErrorTimeout:
            return 0, None
        except Exception:
            return -1, None

    @staticmethod
    def format_full_uid(uid_bytes):
        return ":".join(f"{b:02X}" for b in uid_bytes)

    def classify(self, full_uid):
        if not self.nfc_actions:
            # Admin/action/restricted cards disabled by config -- every
            # card is just a (possibly unknown) game card.
            return 'game'
        if full_uid in self.admin_uids:
            return 'admin'
        if full_uid in self.actions:
            return 'action'
        if full_uid in self.restricted_uids:
            return 'restricted'
        return 'game'

    def open_window(self):
        """Admin card presented: open (or, if already open, just extend)
        the window during which exactly one action/restricted card will
        be accepted."""
        extending = self.window_open
        self.window_open = True
        self.window_deadline = time.time() + self.arm_timeout_s
        # Driven from run(): alternate plain red/green every ~0.7s while
        # the window is open, instead of trusting an opaque hardware
        # repeat count (see led_command docstring).
        self._window_tick_time = 0.0
        self._window_tick_phase = False
        print(f"[ACR] ADMIN WINDOW {'extended' if extending else 'opened'} "
              f"({self.arm_timeout_s}s)")
        self.led_command('green', buzz=self.buzzer)

    def close_window(self, reason):
        if not self.window_open:
            return
        print(f"[ACR] ADMIN WINDOW closed ({reason})")
        self.window_open = False
        self.window_deadline = 0.0
        self.led_command('red')

    def confirm_blink(self):
        """Fast blink+beep burst, distinct from the slow 'window open'
        blink -- fires the instant an action/restricted card is recognized
        inside the window, so there's unmistakable feedback before the
        action itself runs (reboot/shutdown can cut power within a
        second, well before any script-start log line would help)."""
        for _ in range(3):
            self.led_command('green', buzz=self.buzzer)
            time.sleep(0.15)
            self.led_command('red')
            time.sleep(0.15)

    def _session_open(self, uid_int, source):
        """Start tracking a play session for the play-log. Call only
        while holding self._lock, right after the new 'plugged' state
        is committed."""
        self._session_start = time.time()
        self._session_uid = uid_int
        self._session_source = source

    def _session_close(self):
        """Append one completed session to PLAYLOG_FILE (one line of
        JSON: start/end/duration_s/uid/game/source), if one was open.
        Call only while holding self._lock, right before the game that
        started it stops being the active one."""
        if self._session_start is None:
            return
        end = time.time()
        uid_str = str(self._session_uid)
        assoc = self.associations.get(uid_str, "")
        name = assoc.rsplit('/', 1)[-1] if assoc else uid_str
        entry = {
            "start": time.strftime("%Y-%m-%dT%H:%M:%S",
                                    time.localtime(self._session_start)),
            "end": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(end)),
            "duration_s": round(end - self._session_start, 1),
            "uid": uid_str,
            "game": name,
            "source": self._session_source,
        }
        try:
            with open(PLAYLOG_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
            print(f"[ACR] Play session logged: {entry['game']} "
                  f"({entry['duration_s']}s, {entry['source']})")
        except Exception as e:
            print(f"[ACR] Play log write error: {e}")
        self._session_start = None
        self._session_uid = None
        self._session_source = None

    def _set_game_state(self, uid_int, virtual):
        """Mark a card as 'plugged' -- from a real tap (virtual=False) or
        from the HTTP API (virtual=True). Shared by every caller that
        makes a game appear present to ES.

        If something else is already plugged with a *different* uuid,
        force a 'removed' edge first: a real physical swap always has
        this gap for free (you can't have two cards on the reader at
        once, so the poll loop sees 'no card' for a few cycles between
        them) -- but an in-place uuid change while 'plugged' stays
        continuously true would not, and it's unverified whether ES's
        card-reader driver reacts to anything other than the 'plugged'
        edge. The API (and a same-poll-cycle hardware swap, which hits
        this same branch) must fake that gap explicitly.

        A repeat call for the game that's already running is a no-op
        (not just skipping the edge trick) so it can't reset the
        play-log session's start time or flip its recorded source."""
        with self._lock:
            if self.plugged and self.uuid == uid_int:
                return
            if self.plugged:
                self._session_close()
                self.plugged = False
                # Also clear uuid (not just after the sleep below) so
                # write_state() clears 'association' too during the
                # interim -- matching a real removal exactly, same as
                # _clear_game_state. Leaving the old association in
                # place during this gap is suspected of being why one
                # API-to-API switch worked (the previous game was real)
                # and a later one didn't (ES/RetroArch may need to see
                # 'association' actually go empty, not just 'plugged'
                # flip, to register a genuine removal).
                self.uuid = 0
                self.write_state()
                time.sleep(0.3)
            self.plugged = True
            self.uuid = uid_int
            self.virtual_card = virtual
            self.write_state()
            self._session_open(uid_int, 'api' if virtual else 'physical')
        self.led_command('green', buzz=self.buzzer)

    def _clear_game_state(self):
        """Mark no card as plugged -- real removal or an explicit
        API 'stop'. Shared by every caller that returns ES to the menu."""
        with self._lock:
            if self.plugged:
                self._session_close()
            self.plugged = False
            self.uuid = 0
            self.virtual_card = False
            self.write_state()
        self.led_command('red')

    def run_whitelisted_script(self, script):
        """Validate and launch one of ACTIONS_DIR's whitelisted scripts.
        Shared by the real admin-window tap flow (run_action_now) and the
        HTTP API. Returns True iff the script was actually started."""
        if not SCRIPT_NAME_RE.match(script):
            print(f"[ACR] REFUSED (name not whitelisted): {script!r}")
            self.led_command('red')
            return False
        script_path = os.path.join(ACTIONS_DIR, script)
        if not os.path.isfile(script_path):
            print(f"[ACR] REFUSED (not found): {script_path}")
            self.led_command('red')
            return False
        try:
            # /recalbox/share is exFAT (fmask=0133): every file is
            # permanently rw-r--r--, chmod +x is a no-op there, so we
            # must invoke the interpreter explicitly rather than exec
            # the script directly.
            with self._lock, open("/tmp/nfc-acr.log", "a") as logf:
                subprocess.Popen(
                    ["/bin/sh", script_path],
                    cwd=ACTIONS_DIR,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            print(f"[ACR] Action started: {script_path}")
            self.led_command('green', buzz=self.buzzer)
            return True
        except Exception as e:
            print(f"[ACR] Action start error: {e}")
            self.led_command('red')
            return False

    # ---------------------------------------------------- HTTP API hooks ---
    # Called from api_server.py's request handler (a different thread).
    # Everything here just reads actions.json/associations.json state
    # already loaded by the poll loop, or calls the same shared methods a
    # real card tap would -- no new allow-listing logic.

    def api_list_actions(self):
        with self._lock:
            return {uid: dict(a) for uid, a in self.actions.items()}

    def api_list_games(self):
        with self._lock:
            return dict(self.associations)

    def _find_action(self, key):
        key_u = key.upper()
        if key_u in self.actions:
            return key_u, self.actions[key_u]
        for uid, a in self.actions.items():
            if a.get("label", "").lower() == key.lower():
                return uid, a
        return None, None

    def api_run_action(self, key):
        full_uid, action = self._find_action(key)
        if not action:
            return False, f"unknown action: {key!r}"
        script = action.get("script", "")
        ok = self.run_whitelisted_script(script)
        return ok, (f"started {script}" if ok else f"refused: {script}")

    def api_launch_game(self, uid_key):
        uid_str = str(uid_key).strip()
        if uid_str not in self.associations:
            return False, f"unknown game UID: {uid_str}"
        try:
            uid_int = int(uid_str)
        except ValueError:
            return False, f"invalid UID: {uid_str}"
        self._set_game_state(uid_int, virtual=True)
        name = self.associations[uid_str].rsplit('/', 1)[-1]
        return True, f"launched {name}"

    def api_stop_game(self):
        self._clear_game_state()
        return True, "stopped"

    def api_play_log(self, limit=50):
        """Most recent completed play sessions, newest first. Reads the
        file fresh each call -- at most a handful of sessions a day, no
        need to cache."""
        try:
            with open(PLAYLOG_FILE) as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        entries.reverse()
        return entries

    def run_action_now(self, cls, full_uid, uid_int):
        """cls is 'action' (run a whitelisted script) or 'restricted'
        (forward the game to ES exactly like a normal game card would be
        -- just gated behind an admin tap first). Called only while the
        window is open; always consumes it."""
        if cls == 'action':
            action = self.actions.get(full_uid, {})
            label = action.get('label', '?')
            script = action.get('script', '')
        else:
            assoc = self.associations.get(str(uid_int), "")
            name = assoc.rsplit('/', 1)[-1] if assoc else f"UID {uid_int}"
            label = f"Launch (restricted): {name}"

        print(f"[ACR] WINDOW consumed -> {label}")
        self.confirm_blink()

        if cls == 'restricted':
            self._set_game_state(uid_int, virtual=False)
        else:
            self.run_whitelisted_script(script)

        self.close_window("consumed")

    def handle_card_tap(self, cls, full_uid, uid_int):
        """cls is 'admin', 'action' or 'restricted'. Never touches
        ES-visible state by itself (run_action_now does, only for
        'restricted', only inside an open window)."""
        if cls == 'admin':
            self.open_window()
        elif cls in ('action', 'restricted'):
            if self.window_open:
                self.run_action_now(cls, full_uid, uid_int)
            else:
                print(f"[ACR] {cls} card seen outside admin window, "
                      f"ignored: {full_uid}")
                self.led_command('red')

    def run(self):
        print("[ACR] NFC ACR122U Daemon started")
        print(f"[ACR] Fake dir: {FAKE_DIR}")
        print(f"[ACR] Buzzer: {'ON' if self.buzzer else 'OFF'}")

        if not self.connect_acr():
            print("[ACR] ACR122U not found, retrying...")

        self._miss_count = 0

        while self.running:
            if not self.handle:
                time.sleep(5)
                self.connect_acr()
                continue

            uid, uid_bytes = self.poll_card()

            if uid == -1:
                print("[ACR] USB error, reconnecting...")
                self.handle = None
                time.sleep(2)
                continue

            now = time.time()

            if uid > 0:
                self._miss_count = 0
                full_uid = self.format_full_uid(uid_bytes)
                if full_uid != self._cur_full_uid:
                    self._cur_full_uid = full_uid
                    cls = self.classify(full_uid)
                    print(f"[ACR] Card detected: UID={uid} (0x{uid:08X}) "
                          f"full={full_uid} class={cls}")
                    if cls == 'game':
                        # Normal game cards are completely unaffected by
                        # the admin window -- always launch immediately,
                        # window open or not, and never consume it.
                        if self.nfc_games:
                            self._set_game_state(uid, virtual=False)
                        else:
                            print(f"[ACR] game card ignored "
                                  f"(nfc_games disabled): {full_uid}")
                    else:
                        self.handle_card_tap(cls, full_uid, uid)
            else:
                self._miss_count += 1
                if self._miss_count >= 3 and self._cur_full_uid is not None:
                    self._cur_full_uid = None
                    # Only auto-clear a card that a *real* tap plugged in.
                    # An API-launched game (virtual_card=True) must survive
                    # "no physical card seen" -- it only ever ends via an
                    # explicit API stop or a genuine new tap taking over
                    # (handled above, which always calls _set_game_state
                    # with virtual=False and so clears the flag itself).
                    if self.plugged and not self.virtual_card:
                        self._clear_game_state()
                        print("[ACR] Card removed")
                    # action/admin card removal: an open window keeps
                    # running, per spec it must survive the card being
                    # lifted off.

            if self.window_open:
                if now >= self.window_deadline:
                    self.close_window("timeout")
                elif now - self._window_tick_time >= 0.7:
                    self._window_tick_time = now
                    self._window_tick_phase = not self._window_tick_phase
                    self.led_command('green' if self._window_tick_phase else 'red')

            if self.plugged:
                self.check_association_write()

            time.sleep(0.3)

        # Cleanup
        self.plugged = False
        self.uuid = 0
        self.write_state()
        print("[ACR] Daemon stopped")

    def start(self):
        """Process entry point: start whichever subsystems are enabled in
        config.json and keep the process alive until SIGTERM, regardless
        of which (if any) of them actually started."""
        threads = []
        if self.nfc_games or self.nfc_actions:
            t = threading.Thread(target=self.run, name="nfc-poll", daemon=True)
            t.start()
            threads.append(t)
        else:
            print("[ACR] NFC hardware polling disabled "
                  "(nfc_games=false, nfc_actions=false)")

        if self.api_enabled:
            import api_server
            api_server.start(self)
            print(f"[ACR] API listening on :{self.api_port}")
        else:
            print("[ACR] API disabled")

        while self.running:
            time.sleep(1)

        for t in threads:
            t.join(timeout=5)


if __name__ == "__main__":
    ACRDaemon().start()
