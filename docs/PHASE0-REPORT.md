# Phase 0 report: Logic → Live band-set tool (2026-11-07)

Detail lives in `docs/00`–`06`. Tags: **✅ verified** on this Mac · **⚠️ assumed** or desk research only.

## Findings
| Topic | Result |
|---|---|
| Getting data out of Logic | Logic's own exports (all tracks as audio, all MIDI tracks as one MIDI file, AAF, FCP XML) ✅ exist in 13.0. AppleScript: generic commands only ✅. An in-Logic AU can't see the project ⚠️ (Scripter docs). DAWproject: unsupported by Logic ✅ (no trace in the app bundle) and by Live ⚠️. |
| .logicx parsing | logic2ableton (MIT) reads tracks, regions, notes, tempo and mixer from a Logic **11** project ✅. Its "19 missing" warning is a false alarm: those are unused takes ✅ (code + data traced). Apple Loops stored outside the package are a real gap (2 files). Logic 13 projects are untested ⚠️. |
| Plugin state | Every AU state sits in ProjectData as a plain `.aupreset` plist ✅. Analog Lab preset names are readable ✅. |
| **Analog Lab → Live** | **Logic's AU state injected into Live's AU block loads the exact preset ✅** (confirmed by a re-save read back from Live). The VST3 route fails ✅, so we use AU. |
| .als generation | A template-based generator builds Session sets that open in Live 12.4.25b1 ✅. The ID rules come from Live-saved sets ✅. Live needs an `Ableton Project Info` folder ✅. Live ships its own XML schema ✅. |
| Extensions SDK | Creates tracks, scenes, clips and devices. It can't do routing, quantize, colours, macro maps or save ⚠️ (wrapper names plus community docs; the official SDK zip is beta-portal only). |
| AbletonOSC/MCP | Fine for rehearsal control. They can't insert plugins or import audio clips ⚠️. |
| Setlist structure | Pending measurement: *Agent B result goes here.* |
| Driving Live's UI | Works through `orca computer` plus System Events menu clicks ✅. Useful for automated test opens and saves. |

## Recommended architecture
1. **Ingest (per song, about 5 min of your time).** Copy the `.logicx`, then in Logic run *Export All Tracks as Audio Files* (24-bit, from bar 1, plus an FX/returns stem) and *Export All MIDI Tracks*.
   - The tool then parses the `.logicx` copy read-only for structure, notes, tempo, mixer, plugin states and preset names.
   - It writes the **manifest itself**, and you only correct it.
   - An "accounted for" check ties every Logic track to a stem and/or MIDI file, so nothing goes missing silently.
2. **Song model (JSON, source of truth):** songs → sections → parts → treatment per section; instruments (Analog Lab aupreset / Logic built-in / audio); performers; lineups.
   - Sections are proposed automatically from clip boundaries. On *Thoughts* that gives clean 8-bar blocks.
   - Logic markers are used when present.
3. **Builder (Python, stdlib only):** model + lineup → a self-contained Live project folder (stems copied in, one scene per section, a STOP scene, scene tempos, 1-bar quantize, click/cue routing). Analog Lab tracks get the Logic state injected.
   - It is regenerated per lineup and never edited by hand.
   - On stage nothing of ours runs.
4. **UI: a local web app** (Python backend serving a browser drag-and-drop page, started from an Orca quick command).
   - Not an Extension webview: it's modal and one-way, a bad fit for drag and drop, and tied to the beta.
   - Not a native Mac app: too slow to iterate for a one-person tool.
   - Optional later: a tiny Extension command that runs "Rebuild from model" from inside Live.
5. **Rehearsal control (Phase 4):** AbletonOSC for lineup switching and remote scene launch.

## Risks
- **ProjectData parsing is reverse-engineered.** A Logic update can break it. The fallback is stems + MIDI + a hand-filled manifest, so we lose convenience, never the song.
- **Beta on stage.** Pin the version and turn off auto-update before gigs.
- **Plugin RAM** across 20–30 songs: see Setlist structure above.
- **Logic built-ins** (✅ mapped via `spikes/map_plugins.py`): 8 drum/FX tracks use **Quick Sampler**, Alchemy is on 3 tracks, Sculpture on 1, and Klopfgeist on a click strip. Only 4 of 16 MIDI tracks are Analog Lab. Quick Sampler drums are easy to move to a Drum Rack, because the samples are in `QuicksamplerFiles`. Alchemy and Sculpture state is Logic-only, so those parts become stems or get resampled.
- **Plugins missing at export:** Melodyne isn't installed here, so vocals must be bounced where it lives.
- **The .als format moves with Live versions.** Re-validate the generator on each Live update; Live's schema files make that diff easy.
- **Plugin-to-track mapping:** solved ✅. Each plugin slot is an `AuCU` record linked to its channel strip. All 4 Analog Lab presets and both Melodyne inserts map to named tracks with certainty.

## Phase 1 plan: core model + builder MVP for *Thoughts For The Night*
1. Promote the spikes into a package: `lls/ingest` (parser wrapper + aupreset extractor + manifest), `lls/model` (JSON schema), `lls/build` (generator).
2. Ingest *Thoughts*: you do the two Logic exports (I'll give exact settings), and the tool parses the rest and writes the manifest.
3. Section proposal and the accounted-for report (CLI output).
4. Build one default lineup: everything is backing audio except Analog Lab parts as MIDI clip + instrument. Then the click track (generated) routed to outputs 3-4.
5. **Acceptance:** opens in Live; scenes line up with sections; clips launch on the bar; tempo correct; Analog Lab parts play the Logic presets; the accounted-for report shows zero unexplained tracks. I verify by opening and re-saving in Live myself.

## What I need from you for Phase 1
- The two Logic exports for *Thoughts* (exact settings to follow), bounced on a machine that has Melodyne.
- Your audio interface output plan, even a rough one (e.g. 1-2 to PA, 3-4 to in-ears).
