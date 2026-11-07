# Phase 0, step 0: Logic → Live landscape survey (2026-11-07)

Tags: **[L]** verified locally on this Mac (Logic 13.0, Live 12 Suite 12.4.6) · **[S]** verified by reading source/docs · **[I]** inferred or vendor claim, untested.

## Logic side: ways to get data out

| Route | Audio | MIDI | Tempo map | Markers | Track structure | Plugin names | Plugin state | Automation | Maintained | Risk |
|---|---|---|---|---|---|---|---|---|---|---|
| **Export All Tracks as Audio Files** [L menu exists] | ✅ (FX printed) | – | – | – | file names only | – | – | printed in | Apple, stable | Low. Sends don't print when soloed; need FX stem policy |
| **Export All MIDI Tracks as MIDI File** / Selection as MIDI [L] | – | ✅ | ✅ [I] | ✅? [I] | track names [I] | – | – | CC only | Apple, stable | Low. Verify tempo/marker/name contents in spike |
| **Export Project as AAF File** [L — exists in 13.0; a research agent wrongly said it was removed] | ✅ | – | ? | ? | ✅ tracks + region positions [I] | – | – | vol/pan? [I] | Apple | Medium. Parse with pyaaf2 (MIT). Apple Loops are refused [L error string] |
| **Export Project to Final Cut Pro XML** [L] | ✅ (instruments bounced) | – | ? | ? | ✅ [I] | – | – | partial [I] | Apple, legacy | Medium. Live can't read it; we would parse it |
| Export Score as MusicXML [L] | – | notation only | – | – | – | – | – | – | Apple | Not useful |
| Export Selected Track(s) as MainStage Patch/Set [L] | – | – | – | – | – | – | – | – | Apple | Only for a MainStage fallback |
| **Read .logicx plists** (MetaData/ProjectInformation) [S] | – | – | first tempo, time sig, sample rate, key | – | track count, audio file list | – | – | – | – | Low. Plain plists |
| **Parse ProjectData binary** (logic2ableton's `logic_project_data.py`, MIT) [S] | region positions | ✅ notes | ✅ tempo events | ✅ | ✅ names, mixer, routing | ✗ | ✗ | ✗ | 1 dev, active (v2.5.1, 2026-10-06), 53★ | **High**: reverse-engineered, tested only on Logic 10.6/11, **untested on 13** |
| logicx-analyzer (GPL-3) [S] | – | – | ✅ | – | partial | ✅ incl. Alchemy refs | ✗ | ✗ | 2.0 Dec 2025 | GPL; read for knowledge only |
| lpx-explorer (Rust, GPL-3) / LogicProFormatWriter spec (MIT) [S] | | | | | | ✅ AU list (lpx) | | | small | Best format docs: `PROJECTDATA_FORMAT.md` |
| **AATranslator2** ($79/$249) [I] | ✅ | ? | ? | ? | ✅ | ? | ? | ? | commercial | .logicx "from & to" claimed; Logic 13 unconfirmed; has a demo |
| Doseedo ($12/mo, $40 desktop) [I] | claims all incl. 3rd-party plugin state | | | | | | | | commercial | Unverified vendor claims |
| AU / MIDI FX / Scripter inside Logic [S] | own channel only | own channel only | tempo/meter/transport | – | – | – | own state only | – | Apple | **Confirmed dead end**: no project access API |
| AppleScript [L] | – | – | – | – | – | – | – | – | – | **Dead end**: `sdef` shows only the standard suite (open/close/save/print) |
| Accessibility/UI scripting, Logic MCP (MongLong0214/logic-pro-mcp, Swift) [S] | can drive export menus | | flaky readback | | ✅ names, mixer | ✅ (UI census) | – | – | 1 dev, active | High fragility; useful for *plugin inventory*, maybe export automation |
| DAWproject | – | | | | | | | | | **Neither app supports it**: no string in Logic 13 bundle [L]; none for Live [I] |

## Ableton side: ways to build the set

| Route | Can create | Plugins (Analog Lab) | Racks/chains | Stage dependency | Requirements | Risk |
|---|---|---|---|---|---|---|
| **Generate .als (gzipped XML) from a template** | everything | ✅ by cloning a saved PluginDevice block (incl. state blob) | ✅ | **none**: output is a plain Set | none | Corrupt-set risk (IDs). **Live ships the full XML schema per version** [L: `App-Resources/Schema/12.0_12402.txt`, 8.7k lines], a huge help |
| Extensions SDK (JS/Node 24) | tracks, clips, audio clip from file, MIDI, devices (default presets), dialogs/webviews [S/I] | unconfirmed | unconfirmed | none if used only in prep | Suite **beta** 12.4.5+, Developer Mode, Centercode signup [S]. 12.4.6 release has an `ExtensionsPreferencesModel` pref [L]; enabled state unknown | Beta churn; runs per-trigger, no realtime |
| LOM via Max for Live / Remote Script | scenes, tracks, audio clips from path, native devices + chains (12.3+) [S] | ✗ (LOM can't insert plugins) | ✅ native | none if prep only | Suite (has M4L) | Mature |
| AbletonOSC (MIT) + MCP servers | clips, tracks, scenes, fire, params [S] | ✗ | ✗ | Remote Script must be loaded | Live 11+ | Fine for rehearsal control, not for building |
| ABLE-MCP | offline .als reading + SDK bridge [S] | | | | | Young (v0.2) |
| Live import of AAF/FCPXML/DAWproject | ✗ (MIDI + audio files only) [I] | | | | | |

## Recommendation

**Ingest: use Logic's own exports for the content, and a read-only parse of the .logicx to fill in structure, with a manual manifest as the final fallback.**
1. **Content comes from Logic's own exports** (All Tracks as Audio, All MIDI Tracks as MIDI). These are Apple-supported and don't break between Logic versions, and they handle Logic built-ins correctly (the instrument sound is printed into the audio).
2. **Structure comes from a read-only .logicx parse**: tempo map, markers (which become sections and then scenes), track names and order, region positions, mixer. Start from logic2ableton's MIT parser, verified against Logic 13 in a spike. **Plugin names** (Analog Lab vs Alchemy vs ES2) should come from the ProjectData plugin census (logicx-analyzer shows this works) or from the MIDI export's instrument names. If the parse breaks on a future Logic version, we drop back to the manifest and nothing else is lost.
3. **AAF/FCPXML** are a backup source for structure. They're official formats but only carry audio. They're worth one spike only if the ProjectData parse fails on Logic 13.
4. **Skip:** in-Logic plugins, AppleScript, DAWproject, Doseedo. **AATranslator** goes on the "maybe test the demo" list, but not as a core dependency.

**Build: a .als generator from a Live-saved template, kept free of any runtime dependency.**
- The output is a normal Set, so the show never depends on our code. Analog Lab parts work by capturing a real Live-saved PluginDevice block (with its preset) once and cloning it.
- **Extensions SDK** is optional for prep-time conveniences (an "open in Live / refresh lineup" UI inside Live) once we confirm its API. It isn't needed for the MVP.
- **AbletonOSC** is for Phase 4 rehearsal control only.

## Spike adjustments from this survey
- Spike 1 (logic2ableton) is now also **"does the ProjectData parse work on Logic 13?"** It is the key unknown.
- New mini-spike: open a MIDI export and check tempo map, markers and track names.
- Spike 2: use the shipped schema file plus a Live-saved template; test sample path references.
- Spike 3: the user needs to check *Preferences → Extensions* (or join the beta) so I can read the SDK `api/` docs.
