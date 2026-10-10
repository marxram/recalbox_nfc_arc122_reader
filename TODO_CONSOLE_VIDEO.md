# TODO: Custom console-mode idle video

Status: **blocked** — waiting on the user to supply the actual video
(and ideally matching audio) file. Mechanism is already fully
understood, nothing technical left to figure out.

## Current state (verified on the device, 2026-10-11)

`recalbox.conf`:
```
cardreader.consolemode=1
cardreader.consolemodeexit=hkstart
cardreader.consolemodevideo=Bios2
cardreader.consolemodesound=music
```

`consolemodevideo`/`consolemodesound` just select a filename under
`/recalbox/system/resources/cardreader/`:
```
Bios1.mp4 / Bios1.mp3
Bios2.mp4 / Bios2.mp3 / Bios2.wav
```

## Plan once the video file is available

1. `mount -o remount,rw /`
2. Copy the new video (and audio, if separate) into
   `/recalbox/system/resources/cardreader/` under a new name, e.g.
   `Custom.mp4` (+ `Custom.mp3`) — don't overwrite `Bios1`/`Bios2`, keep
   them as fallback.
3. Edit `recalbox.conf` following the project's standard safe-edit
   procedure (see `ACTION_CARDS.md` — stop ES, timestamped backup,
   targeted `sed` on the exact key, `sync`, restart ES):
   ```
   cardreader.consolemodevideo=Custom
   cardreader.consolemodesound=Custom   # if using a separate custom audio track
   ```
4. Test by entering console mode.

## Caveat — not update-persistent

`/recalbox/system/...` is the **system partition**, overwritten by
Recalbox updates (unlike `/recalbox/share/system/nfc-daemon/...`, which
survives them). After any future Recalbox update, the custom video file
and the `recalbox.conf` line will need to be reapplied — this isn't
automated by anything in this repo's installer.
