# RecalPuck remote-control API

Integration reference for a future client (planned: the user's PiPhone
assistant "Andi" — **not yet integrated**, this is just the API). If
you're looking for how the API is *built* (daemon internals, the
`virtual_card` mechanism, the play-log file format, the admin-window
NFC flow it sits on top of), see `../ACTION_CARDS.md`'s "Remote-control
API" section instead — this document is written for someone who wants
to *call* the API, not maintain it.

## What it can do

A small HTTP service running on the Recalbox itself, alongside the NFC
card-reader daemon. It exposes exactly two kinds of actions, both of
which already exist on the physical cartridge reader — the API is a
second way to trigger the same things, not a new feature set:

1. **Run an admin action script** — reboot, shutdown, console-mode
   on/off, pad-profile switches, or any other script already wired up
   to a physical action card.
2. **Launch or stop a game** — make the console launch a specific ROM,
   or return to the menu, as if a card had been tapped or lifted.

It also exposes **read access to a play-session log** (start/end/
duration/source per session) that doesn't exist anywhere natively.

It explicitly does **not** re-implement anything the *native* Recalbox
Manager v3 API already covers — for current status, system list, or
ROM list, call that instead:

| Need | Call |
|---|---|
| What's running right now | `GET http://<recalbox-host>:81/api/status` |
| Recalbox/OS/core versions | `GET http://<recalbox-host>:81/api/versions` |
| List of systems | `GET http://<recalbox-host>:81/api/systems` |
| ROMs for one system | `GET http://<recalbox-host>:81/api/systems/<id>/roms` |

No login needed for any of those four — they're native Recalbox, not
part of this project.

## Discovering what's callable: `GET /`

```
GET http://<recalbox-host>:<api-port>/
```

Returns an HTML page, generated fresh from the live configuration on
every request — it always reflects whatever action scripts and game
cards actually exist right now, never a hand-maintained list that can
drift out of date. No authentication needed for this one page (it only
lists *shapes* — labels, ROM names, URL patterns — never a secret).

Machine-readable equivalents, both authenticated:
```
GET /actions   -> {"<card-uid>": {"label": "...", "script": "..."}, ...}
GET /games     -> {"<game-uid>": "<uuid>||<rom-path>", ...}
```

## Authentication

Every route **except** `GET /` requires:

```
Authorization: Bearer <token>
```

- The token is a long random string, stored in `config.json` on the
  Recalbox (`api.token`), generated once by the installer. It's a
  plain shared secret — there is no login flow, no per-user accounts,
  no OAuth, nothing to "sign in" with. Whoever holds the token can
  trigger anything the API exposes.
- Compared with a constant-time comparison (`hmac.compare_digest`) on
  the server side, so there's no meaningful timing side-channel, but
  there is also **no rate limiting and no TLS** — this is built for a
  LAN-only, single-client (PiPhone) setup, not for exposing the port
  to the internet. Don't port-forward it.
- A request with a missing or wrong token gets `401`. A request to an
  authenticated route when no token is configured at all (`api.token`
  empty) gets `503` — the server refuses to compare against an empty
  secret rather than silently accepting everyone.
- The token can be regenerated any time from the Pi:
  `python3 manage_actions.py config --regen-token` (also shows the
  current one). Regenerating invalidates the old one immediately (the
  daemon restarts to pick up the change) — update any client that has
  it cached.

## Calling an action or a game

```
POST /actions/<label>/trigger      e.g. POST /actions/Reboot/trigger
POST /games/<uid>/launch           e.g. POST /games/1038113728/launch
POST /games/stop
```

- `<label>` and ROM names can contain spaces — percent-encode them
  (`Console%20mode%20ON`, not a literal space in the URL).
- `<label>` lookup is case-insensitive; the card's raw UID also works
  if you already have it from `GET /actions`.
- All three return `{"ok": true/false, "message": "..."}` with `200`
  on success, `400` if the action/game wasn't found or the script was
  refused (e.g. not whitelisted), `401`/`503` per the auth rules above.
- **Switching games reliably:** always call `POST /games/stop` before
  a new `POST /games/<uid>/launch` if a *different* game might already
  be running. A single launch call while something else is active has
  been observed to work inconsistently (see `../ACTION_CARDS.md`'s
  "Remote-control API" section for the full writeup) — stop-then-
  launch is the pattern that's actually been confirmed reliable across
  repeated testing.

## Play-session log

```
GET /playlog   -> [{"start": "...", "end": "...", "duration_s": 19.4,
                    "uid": "...", "game": "...", "source": "physical"|"api"},
                   ...]   (newest first)
```

`source` tells you whether that session came from a real card tap or
an API call.

## What a client (e.g. PiPhone) still needs, beyond this document

There is **no service discovery** — nothing here tells a client where
the server even is. Three things have to be handed to a client
out of band, deliberately, before it can call anything:

1. **Host/IP** of the Recalbox. PiPhone's existing `recalbox_status.py`
   already has this in its own `config.yaml` (`recalbox_host`) for the
   native status API — the same value works here.
2. **Port** — `config.json`'s `api.port` on the Recalbox, `8080` by
   default.
3. **Bearer token** — a credential, so it must be copied in
   deliberately (e.g. a new `config.yaml` key on the PiPhone side), never guessed, derived, or auto-fetched. `GET /` won't hand it to you.

None of this is wired up yet — PiPhone-side integration (where exactly
these three values live in its config, how/when it calls this API) is
future work, intentionally out of scope for this repo.
