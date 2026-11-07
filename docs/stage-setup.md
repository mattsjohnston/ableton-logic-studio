# Stage setup and show procedures

Rule: the show must run without our code. Everything below uses stock Live features plus the files in `out/setlists/<name>/`.
Status tags: **[V]** verified from a source, **[U]** unverified (from memory or practitioner lore; test before the gig).

Sources actually consulted: Ableton Live 12 manual (Live Concepts: scenes, launch quantization, follow actions exist [V]); Novation Launchpad Mini guide (session ring, pad = clip, right-hand scene-launch buttons, Session button zooms out [V]); a third-party Max for Live tool listing APC mini mk2, Launchpad Mini mk3 and Launchpad X as controllers with a session ring [V]. The Ableton control-surface manual page was not fetched (404), so exact script names and Live 12 menu wording are **[U]**.

## 1. Live preferences

**Audio** (Preferences > Audio)
- Driver type CoreAudio. Input and Output device: Volt 276 (both, not an aggregate device). [U: menu wording]
- Sample rate: pick 44.1 kHz (the stems are 44.1k) and use it for every set, the Volt's own settings and the phone files. Never let one set be 48k. If the band prefers 48k, re-render everything at 48k first.
- Output config: main = 1/2 (PA/in-ears mix as wired), click = 3/4. The builder routes the "Click" track to outputs 3/4 (`CLICK_OUT` in `lls/build.py`). Enable outputs 3/4 in Output Config.
- Buffer size: start at 128 samples. Analog Lab instances idle at about 4-5% of a core each (measured, Phase 0), and live keys/drums need low latency. Go 256 if the CPU meter spikes; do not go below 64 with plugin-heavy sets. Check round-trip latency shown in the Audio preferences. [U: values are starting points, test on the show laptop]
- Do not use the laptop's built-in output or input while the Volt is selected. Turn off Wi-Fi/Bluetooth and notifications on show day.

**MIDI** (Preferences > Link, Tempo & MIDI): each device has Track, Sync, Remote switches. [U]
| Device | Track | Sync | Remote |
|---|---|---|---|
| Novation Impulse (keys) and other keyboards | On | Off | Off |
| E-kit (drums) | On | Off | Off |
| APC mini mk2 / Launchpad (grid) | Off for the pad ports (the control surface script handles them) | Off | Off |
| Foot switch (generic MIDI) | Off | Off | On (it maps to scene launch/stop via MIDI Map) |
- Track = notes reach instrument tracks. Remote = the device can be MIDI-mapped (the foot switch). Keep Track off on the grid so pad presses do not play notes into tracks.
- Rigs in `band.json` carry `midi_port` and `midi_channel`; those port names must match what Live lists exactly.
- Control Surface slots (same preferences page): APC mini mk2 and Launchpad entries below. Input and Output must both be set to the device's port. [U]

## 2. Grid controller

Both grid devices have a built-in Live control surface script; no Max or third-party code is needed. Pick it in a Control Surface slot (Preferences > Link, Tempo & MIDI). Names as expected in the list (verify): **APC mini mk2** [U], **Launchpad Mini MK3** [U], **Launchpad X** [U]. Launchpad Mini MK3 needs Live 10.1.5 or later (per Novation) [V]; all three are listed as supporting the session ring [V].

How it works (generic Session View controller behaviour)
- The red rectangle in Session View (session ring) is the 8x8 area the pad grid controls. Pressing a pad launches the clip in that slot. [V]
- Scene-launch buttons run down the right side (the 8-button column) and launch the whole row. [V for Launchpad; U for APC mini mk2 where it is the SCENE LAUNCH column]
- Arrow/navigation buttons move the ring. Hold Session on a Launchpad to zoom out. [V for Launchpad; the APC mini mk2 arrow behaviour is U]
- "Stop all clips": APC mini mk2 has a dedicated Stop All Clips button [U]. The Launchpads have per-track stop in the bottom row via Stop Clip mode [U]. Do not rely on either on stage: our sets include a STOP scene (last scene) that launches an empty row.

Recommended mapping for this band
- Our builder makes one scene per section plus a final **STOP** scene, in song order, with scene names `Section (bar N)`. With one scene per section, the right-hand scene-launch column IS the section list. For a song with 8 or fewer sections the ring shows all of them; for longer songs (Thoughts For The Night has 15 plus STOP) use the down arrow to page the ring one row at a time, and keep the ring parked on the section the band is currently in.
- Bottom row of clip pads = **stop/next song** row. Because the sets contain only section-spanning clips, this row is free in Session View: stop-track buttons in the bottom row are the "bail" buttons. Treat it as the bail-out row; the STOP scene is the guaranteed one.
- Keep one person on the grid (usually the drummer or whoever has a free hand), and give them the printed `running-order.html` page.
- Never launch individual clips from the grid during the show; launch scenes (rows), so every track changes together.

## 3. Foot switch

Any class-compliant MIDI foot switch works (or one that sends a CC). Options [U]:
1. **Scene Launch Next:** Live has a MIDI-mappable "Scene Launch" and "Select Next Scene" / "Scene Down" style controls. Map one switch to "Launch Selected Scene" and a second to "Select Next Scene". Press = advance one section. Check the exact names in MIDI Map mode (Cmd+M) on the show version.
2. **Stop All Clips:** map the other switch to the "Stop All Clips" button in the Control Bar (or the STOP scene's launch).
3. Foot switch that only sends one note: map it to Next Scene and keep the grid for stop.
Test with the launch quantization at 1 bar: a press just before the barline fires on the downbeat. The builder sets global quantization to 1 bar (`GlobalQuantisation = 4` in the set).

## 4. Between songs

One Live set per song. There is no gapless switching between sets in Live [V, from docs/06]. Measured load is about 0.3 s with Analog Lab instances (Phase 0), but still allow for talking time.
1. In the show folder `out/setlists/<name>/`, projects are named `01 ... Project`, `02 ... Project`, so they sort in show order.
2. Before the gig, open each set once and let it fully load/save-as-needed (plugin states, authorizations).
3. During the show: STOP scene, then File > Open Recent Set (or Cmd+O and pick the next `NN Song Project/<Song>.als`). Live asks to save the current set; choose **Don't Save**. Better: set Live to not prompt by only ever running unmodified sets. [U: exact prompt wording]
4. Pre-load warning: while the next set loads, Live may beachball for a moment and audio from the old set stops. Do it in a talking gap, and do not touch pads until the meter is moving and the Session View shows the new set's name in the title bar.
5. Put the next set's tempo (Control Bar) in your head: each scene carries its own tempo, so the first scene launch sets it.
6. Keep `fallback/NN Song.wav` for the next song on the phone while the laptop loads, as an emergency.

## 5. Mistake recovery playbook

Context: sections are scenes, launch quantization is 1 bar, clips are section-length.
| Situation | Action |
|---|---|
| Band wants one more time through this section | Relaunch the same scene (row) before the section ends. It restarts the section at the next bar line. If it is a vamp section, the clip loops by itself. Turn Scene follow actions off for sets you plan to loop. |
| Band dropped a bar/lost the form | Everyone follows the backing: do not stop. Wait for the next scene change. If the drummer is lost, count from the click (in-ears 3/4). |
| Skip ahead (verse 2 to chorus) | Launch the target scene. It arrives on the next bar line. |
| Missed the next scene | Launch it late: it starts at the next quantize point, the backing is then offset from the band by up to a bar; fix by launching the scene again on the correct bar boundary. |
| Everything is a mess | Launch the **STOP** scene (or Stop All Clips). The live parts keep working (inputs and monitoring stay on). Count in the next section or the song's ending with the drummer. |
| Laptop audio stalls / dropouts | Raise the buffer size next break; if it persists, go to the fallback (section 6). |
| Scene tempo wrong | Re-launch the scene; each scene sets its own tempo. Never nudge Tempo by hand mid-song. |

## 6. Fallback procedure (phone)

Files: `out/setlists/<name>/fallback/NN Song.wav` (24-bit 44.1k) and `.m4a` (AAC) of each. Each has a 2-bar count-in click then the full backing from bar 1, with live parts excluded. `NN Song click-left` is for the drummer: click in the left ear, music (mono) in the right. The `fallback-report.md` lists parts missing from the fallback (MIDI-only parts with no stem).
1. Copy the `.m4a` files to the phone (AirDrop/Files) in show order, name-prefixed so they sort. Test volume and latency of the phone-to-PA path before the gig.
2. If Live dies: drummer gets the click-left file in the in-ears (second phone, or a splitter), main phone feeds the mixer. Both start together: drummer taps play on the first phone, FOH starts the other, or use one phone plus the drummer listening to the PA count-in.
3. Fallback is linear: no section looping or skipping. Start the song again from bar 1 if the band falls apart.
4. Ship the m4a to a cloud folder as well, so a second phone can fetch it.
5. Tempo-changing sections are not rendered by the fallback (it assumes the song's constant tempo).

## 7. Pre-gig checklist

- [ ] Live beta auto-update disabled (Preferences > Licenses & Updates > Auto-Update off) [U: menu name]. Do not update Live or Analog Lab within a week of the gig; keep the installer of the working version.
- [ ] Time Machine and system updates deferred (macOS Software Update off) and Do Not Disturb on.
- [ ] Every set opens clean: no missing samples, no missing plugin dialogs, Analog Lab authorized, sounds as expected. Run `python -m lls setlist build` once more on show day only if the sources changed.
- [ ] CPU test: play the densest song at full length with the real interface and the live parts armed; Live's CPU meter stays under about 50%, no dropouts. Repeat with battery unplugged (power connected, "Prevent automatic sleeping" on).
- [ ] Sample rate: 44.1k in Live, in the Volt (control panel), in every set, and in the fallback files.
- [ ] Click on outputs 3/4, main mix on 1/2: hear both, check the drummer's in-ear level.
- [ ] MIDI: each rig sends on its configured port/channel (matt-keys, keys-2, e-kit) and only those tracks respond.
- [ ] Vocal mics: input levels set on the Volt, monitoring In (no latency hit from a big buffer).
- [ ] Grid controller and foot switch tested with the final set; spare USB cable and hub.
- [ ] Phone fallback files on the phone, volume tested, in show order. Printed `running-order.html` for the person on the grid.
- [ ] A backup laptop is optional; if present, the same folder on it.
