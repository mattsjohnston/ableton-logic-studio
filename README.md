# Ableton Logic Studio (`lls`)

Turns Logic Pro songs into Ableton Live Session sets for a 1–6 person synthwave band. You can re-map who plays what for each lineup without redoing the conversion. The song model (`songs/<song>/song.json`) is the source of truth. Every Live set is generated, self-contained, and runs on stage without this tool.

## Per song: from Logic to stage
1. **Export from Logic** (work on a copy; never the original):
   - *File → Export → All Tracks as Audio Files…*: WAVE, 24-bit, Normalize off, Bypass Effect Plug-ins **off**, Include Volume/Pan Automation **on**, range *Extend File Length to Project End*.
   - *File → Bounce → Project* (full mix, 24-bit WAV).
   - Aux returns (reverb/delay): create tracks for the aux strips and export them too, otherwise the backing loses its send FX.
   - Put everything in one folder.
2. `python -m lls ingest "<copy>.logicx" songs/<slug>` reads the project **read-only** and writes the song model:
   - sections, auto-named from the vocals (Intro / Verse / Chorus…);
   - parts, with instruments: Analog Lab presets incl. full state, Logic built-ins;
   - MIDI, plus raw audio stems;
   - an accounted-for list covering every Logic track.
   - Re-running keeps your edits.
3. `python -m lls link-stems songs/<slug> <export folder>` attaches the exported stems. Duplicate export names are resolved by audio activity.
4. `python -m lls ui` (Orca quick command **Dev**) opens http://127.0.0.1:8765. The whole workflow runs from the browser (the **?** button explains it). There you:
   - import a song (song picker → **+ New song**, or **↻ Re-import / Link stems**): the UI clones the `.logicx` into `samples/<slug>.logicx` (APFS copy), ingests the copy and links the export folder, then shows the parts, the link report and the accounted-for list. Steps 2–3 above are what it runs;
   - preview the backing in the browser (Space): *Lineup backing* plays exactly what the laptop will play in the current lineup, *Full song* plays the mix bounce; click, spoken cues (rendered with `say` like the Live set's Cues track, cached in `stems/preview/` and re-rendered when sections change), solo/mute, play from / loop a section. MIDI-only parts (no stem yet) play on a simple browser synth, marked *synth preview*. Rehearsal aid only: the show runs in Live. Hover anything for details (section, part and cell tooltips show bars, cue text, treatment, who plays it, stem source);
   - toggle spoken **Cues** and **Auto-advance**, set per-section cue text and vamp;
   - name and adjust sections;
   - set treatments per part and section;
   - build lineups by dragging parts onto performers (or *Suggest for N performers*);
   - fix what the checks panel flags;
   - click **Build**, then **Open in Live**.
5. CLI equivalents:
   - `python -m lls lineup suggest songs/<slug> matt keys-2 drummer bassist --save four`
   - `python -m lls lineup validate songs/<slug> four`
   - `python -m lls build songs/<slug> four`
6. Show: the **Setlists** tab (create/reorder songs, lineup + notes per song, **Build show**, **Reveal in Finder**), or `python -m lls setlist build setlists/<name>.json`, gives one Live set per song in show order, a printable running order, and phone fallback mixes (with a click-left variant). See `docs/stage-setup.md`.

## What a built set contains
- One scene per section, plus a **STOP** scene; scene tempo; launch quantize 1 bar.
- Backing parts: section clips from exported stems.
- Analog Lab parts: AU instrument loaded with **the exact Logic preset state**. Played as MIDI clips, or live from a performer's keyboard (port/channel routing, monitoring).
- Drums: a Drum Rack on GM notes for an e-kit. Pads use printed one-shots cut from the Logic stems, or the factory samples.
- Vocal and bass/guitar input tracks per performer.
- Generated click (and spoken cues, if enabled) on the in-ear outputs (3/4 by default, `band.json`).
- `accounted-for.md` next to the project: what happened to every Logic track.

## Files
- `band.json`: performers, their rigs (controller, MIDI port/channel, audio input), interface outputs.
- `songs/<slug>/song.json`: the song model (see `docs/model.md`). Stems are not committed.
- `lls/`: the package (ingest, logic_plugins, sections, stems, lineup, build, als, drumrack, oneshot, setlist, fallback, ui).
- `docs/`: Phase 0 research and spikes (`00`–`08`), `PHASE0-REPORT.md`, `model.md`, `stage-setup.md`.

## Requirements
macOS, Python 3.12 venv with `logic2ableton` (MIT) and `numpy`, Ableton Live 12 Suite (12.4.25 beta in use), Arturia Analog Lab V (AU). Tests: `.venv/bin/python -m unittest discover tests` (Orca **Test**). Tests that need the sample project (`samples/`, or `$LLS_SAMPLE_PROJECT`), the song's stems or Live itself skip when those are missing.

## Running on another Mac (e.g. the show laptop)
1. Clone the repo, `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
2. `python -m lls doctor` checks Python, packages, Live 12 (path, version, edition, set-format schema), the Core Library files lls copies (8-Track Template, the Patience demo set, input-chain presets, drum one-shots), Analog Lab V, and optionally Logic Pro and AbletonOSC. Each line is ✓/✗ with a fix. The UI shows a banner when something critical is missing (`GET /api/doctor`).
3. Live is found automatically (`lls/liveapp.py`): every `Ableton Live 12*.app` in `/Applications` and `~/Applications`, Suite editions first, then the newest version (a release beats a beta of the same number). Set `LIVE_APP="/Applications/Ableton Live 12 Suite.app"` to pick one. If Live's newest schema is older than our template's (`12.0_12402`), lls warns that sets may not open: update Live.
4. Move songs with **song packages** instead of copying folders: `python -m lls package export songs/<slug> [out.llsong]` and `python -m lls package import <file.llsong> [--slug x] [--force]`, or in the UI song picker use **⇪ Export song package…** (saved to ~/Downloads and revealed in Finder) and **⇩ Import song package…**. A `.llsong` is a zip with song.json, MIDI, instruments and stems (`export/`, `raw/`, `oneshots/`; no previews or `.bak`) plus a manifest with sha256 hashes. Import rejects a damaged or edited package and won't replace an existing song without `--force` (or the UI confirm). When it does replace one, lineups that only exist locally are kept. Logic isn't needed on the receiving Mac.
