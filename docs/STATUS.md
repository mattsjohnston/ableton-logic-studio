# Status (2026-11-07)

✅ = verified in Live or by a Live re-save · 🧪 = covered by tests only · ⏳ = blocked or pending

## Phase 1: core model + builder
- ✅ Ingest of Logic 11 and **Logic 13** projects (read-only): tempo, sections (auto-named), parts, MIDI, plugin→track map, Analog Lab full state, accounted-for list. Re-ingest keeps user edits.
- ✅ Set opens in Live 12.4.25b1. Scenes per section + STOP, 1-bar quantize, tempo correct, Analog Lab presets survive a Live re-save, click on outputs 3/4.
- ✅ `link-stems`: 21/23 Logic stems linked; the duplicate "Light Harmonics" export was resolved by audio activity.
- ⏳ Snare 2 stem (export dies before finishing) and the aux reverb/delay returns: need the screen awake.
- ⏳ Hi-Hat 1 + Snare 2 are **silent in Logic on this Mac** (kits not installed; Logic offers 12.7 GB of packs). The full-mix bounce lacks them too. Needs the user's choice: download here, or export on the original Mac.
- ⏳ Playback CPU test of a full set (the display went to sleep).

## Phase 2: UI + lineups
- ✅ Lineup engine: resolve / validate / suggest for 2–5 performers; live treatments (vocal/bass inputs, keyboard port+channel routing, drums). Named-port routing verified in Live (Impulse (MIDI) Ch.1).
- ✅ Local web UI (`python -m lls ui`): sections, treatments, lineup board, grouped checks, build + open in Live. Headless-browser click-through passed.
- ✅ Whole workflow in the UI (headless Chrome click-through with the real sample): Import (APFS clone → ingest → link-stems job with live log; result shows parts, link report, accounted-for), Setlists tab (CRUD, drag/⌥↑↓ reorder, Build show job, running order, fallback report, Reveal in Finder), song options (cues / auto-advance), section cue text + vamp, browser preview (Web Audio: lineup backing / full song, click, solo/mute, loop, playhead), "How it works" help. Native file pickers (osascript) are covered by tests with a stubbed osascript only.
- 🧪 Sound switching on one keyboard (Auto monitoring + placeholder clips). It relies on Live's documented Auto behaviour; untested with a real keyboard.

## Phase 3: instruments
- ✅ Drum Rack for the e-kit (GM notes; pad note stored as 128 − n, verified in Live).
- 🧪 Printed one-shots from the drum stems (6/8; hat and snare wait on the samples above).
- 🧪 Analog Lab preset suggestions for Logic built-ins, from Arturia db + Alchemy tags (`spikes/preset_suggest.py`). Audio-similarity matching deferred: no preset previews exist.

## Phase 4: show
- 🧪 Setlist build (one set per song, show order), running order md/html, phone fallback mixes (+ click-left), `docs/stage-setup.md` (controller details partly unverified).
- 🧪 Spoken cues track (on for Thoughts) and optional scene auto-advance (follow-action enum inferred from Live-saved sets, **unverified in Live**).
- Not built: AbletonOSC rehearsal remote. It's optional, because rebuilding/opening a set per lineup from the UI covers lineup switching.

## Needs Matt
1. Wake/unlock the Mac so the GUI checks and Logic exports can finish.
2. Drum kits: download the Logic packs here (12.7 GB) **or** export Hi-Hat 1, Snare 2 and the full mix on the original Mac.
3. Real-hardware checks at rehearsal: Impulse into Live (enable *Track* for its port), the e-kit's note map, sound switching on one keyboard.
