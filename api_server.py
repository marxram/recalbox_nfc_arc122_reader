#!/usr/bin/env python3
"""
api_server.py - optional HTTP remote-control API for the NFC daemon.
======================================================================
Stdlib-only (http.server) on purpose: Recalbox's site-packages lives on
the same read-only, update-wiped OS partition as /etc/init.d, so any
pip-installed framework (Flask, FastAPI, ...) would need the exact same
"redeploy after every Recalbox update" treatment this project already
works around for its init.d stub. Using only the standard library avoids
that problem entirely.

Exposes exactly what's already defined in actions.json/associations.json
-- an implicit allowlist, nothing new to configure per endpoint. Does
NOT duplicate the native Recalbox Manager v3 API (port 81, prefix
/api): no status/versions/systems/roms here, only triggers for things
the native API cannot do (run an action script, launch/stop a specific
game, toggle console mode, ...).

Auth: a single bearer token from config.json's "api.token", compared
with hmac.compare_digest. Required on every route except GET / (the
docs page below -- it only lists shapes/URLs, no secrets).
"""
import hmac
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote


def _basename(assoc):
    """associations.json values look like '<uuid>||/path/to/Rom.zip'."""
    return assoc.rsplit("||", 1)[-1].rsplit("/", 1)[-1]


def _make_handler(daemon):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RecalPuckAPI/1.0"

        def log_message(self, fmt, *args):
            print(f"[API] {self.address_string()} " + (fmt % args))

        # -------------------------------------------------------- auth ---
        def _authorized(self):
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            return hmac.compare_digest(auth[7:], daemon.api_token)

        def _require_auth(self):
            if not daemon.api_token:
                self._send_json(503, {"error": "API token not configured in config.json"})
                return False
            if not self._authorized():
                self._send_json(401, {"error": "missing or invalid bearer token"})
                return False
            return True

        # ------------------------------------------------------ helpers ---
        def _send_json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, code, body_str):
            body = body_str.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # --------------------------------------------------------- GET ---
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send_html(200, self._docs_page())
            elif self.path == "/actions":
                if self._require_auth():
                    self._send_json(200, daemon.api_list_actions())
            elif self.path == "/games":
                if self._require_auth():
                    self._send_json(200, daemon.api_list_games())
            elif self.path == "/playlog":
                if self._require_auth():
                    self._send_json(200, daemon.api_play_log())
            else:
                self._send_json(404, {"error": "not found"})

        # -------------------------------------------------------- POST ---
        def do_POST(self):
            if not self._require_auth():
                return
            # Labels (e.g. "Console mode ON") contain spaces, so a real
            # client must percent-encode the path segment -- decode it
            # back before matching against actions.json/associations.json.
            parts = [unquote(p) for p in self.path.split("/") if p]
            if len(parts) == 3 and parts[0] == "actions" and parts[2] == "trigger":
                ok, msg = daemon.api_run_action(parts[1])
                self._send_json(200 if ok else 400, {"ok": ok, "message": msg})
            elif len(parts) == 3 and parts[0] == "games" and parts[2] == "launch":
                ok, msg = daemon.api_launch_game(parts[1])
                self._send_json(200 if ok else 400, {"ok": ok, "message": msg})
            elif len(parts) == 2 and parts[0] == "games" and parts[1] == "stop":
                ok, msg = daemon.api_stop_game()
                self._send_json(200, {"ok": ok, "message": msg})
            else:
                self._send_json(404, {"error": "not found"})

        # ---------------------------------------------------- docs page ---
        def _docs_page(self):
            actions = daemon.api_list_actions()
            games = daemon.api_list_games()

            action_rows = "".join(
                f"<tr><td>{html.escape(a.get('label', uid))}</td>"
                f"<td><code>POST /actions/{html.escape(quote(a.get('label', uid)))}/trigger</code></td></tr>"
                for uid, a in actions.items()
            )
            game_rows = "".join(
                f"<tr><td>{html.escape(_basename(assoc))}</td>"
                f"<td><code>POST /games/{html.escape(uid)}/launch</code></td></tr>"
                for uid, assoc in games.items()
            )

            return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>RecalPuck remote API</title></head>
<body>
<h1>RecalPuck remote-control API</h1>
<p>Mirrors what is already defined on the cartridge reader itself
(<code>actions.json</code> / <code>associations.json</code>) &mdash;
nothing new to configure per action. For read-only status, system and
ROM-list lookups, use the native Recalbox API on port 81 instead
(<code>GET /api/status</code>, <code>/api/versions</code>,
<code>/api/systems</code>, <code>/api/systems/&lt;id&gt;/roms</code>)
&mdash; not duplicated here.</p>
<p>Every route below except this page requires
<code>Authorization: Bearer &lt;token&gt;</code>.</p>

<h2>Actions ({len(actions)})</h2>
<table border="1" cellpadding="4">
<tr><th>Label</th><th>Call</th></tr>
{action_rows}
</table>

<h2>Games ({len(games)})</h2>
<table border="1" cellpadding="4">
<tr><th>ROM</th><th>Call</th></tr>
{game_rows}
</table>

<h2>Other</h2>
<table border="1" cellpadding="4">
<tr><td>Stop the running game (return to menu)</td><td><code>POST /games/stop</code></td></tr>
<tr><td>List actions as JSON</td><td><code>GET /actions</code></td></tr>
<tr><td>List games as JSON</td><td><code>GET /games</code></td></tr>
<tr><td>Recent play sessions (start/end/duration/source), newest first</td><td><code>GET /playlog</code></td></tr>
</table>
</body></html>"""

    return Handler


def start(daemon):
    """Start the API server in a background daemon thread. Non-blocking."""
    handler = _make_handler(daemon)
    httpd = ThreadingHTTPServer(("0.0.0.0", daemon.api_port), handler)
    daemon._api_httpd = httpd
    t = threading.Thread(target=httpd.serve_forever, name="api-server", daemon=True)
    t.start()
    return t
