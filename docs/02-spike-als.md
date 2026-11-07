# Spike 2: .als Session View generator (2026-11-07)

`spikes/als_gen.py`: stdlib-only Python. Outputs `out/spike2/Spike Set Project/Spike Set.als`.

## How it works
- **Base set:** Live's own `Core Library/Templates/8-Track Template.als` (schema `12.0_12402`, the version both 12.4.6 and the 12.4.25 beta write).
- **Prototypes:** the session `AudioClip`, `MidiClip` and `MidiTrack` are copied from the Live 12 Suite demo song (Live-saved, 12402). They get deep-copied and their fields rewritten. Clip envelopes are stripped because they point at foreign IDs.
- **Generated:** 3 tracks (2 audio stems, 1 MIDI) and 4 scenes (Intro/Verse/Chorus/STOP). Clips are 4 bars. Tempo is 120. Global launch quantize is 1 bar. Every slot has a stop button, so an empty scene works as a STOP scene.
- **Samples:** WAVs go in `<Project>/Samples/`. FileRef holds both an absolute `Path` and a project-relative `RelativePath` (`RelativePathType=1`, the same as logic2ableton, whose set loads cleanly).

## ID rules (verified by analysing the Live-saved demo set)
- **Global pointee IDs** (`AutomationTarget`, `ModulationTarget`, `Pointee`, `*ModulationTarget`, `ControllerTargets.N`): 0 duplicates across about 12k elements, all below `NextPointeeId`. We renumber these on every cloned track.
- **Track IDs:** unique within `Tracks`. **Scene IDs:** 0..n-1.
- **Everything else** (ClipSlot, KeyTrack, WarpMarker, clip Ids…) is local to its parent list. Duplicates across lists are normal.
- **Return tracks** keep an empty `ClipSlotList`. Audio/MIDI tracks have two lists (Main + Freeze sequencer), and both must match the scene count.

## Status
- ✅ Valid XML. My validator passes (unique global IDs, slot counts match scenes).
- ✅ logic2ableton's set (same FileRef style, older schema) loads in Live 12.4.25b1 with no errors in Log.txt.
- ⏳ **Open in Live: pending.** Live was playing, so the open was cancelled at the "stop audio?" prompt. The acceptance check is: loads, 4 named and coloured scenes, clips launch on the bar, tempo 120.
- **Reference:** Live ships an XML schema per version at `App-Resources/Schema/12.0_12402.txt`.
