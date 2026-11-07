# Spike 1: logic2ableton v2.5.1 on "Thoughts For The Night" (2026-11-07)

Input: an APFS clone of the user's .logicx, **last saved from Logic 11.0.1**. That version is inside the tool's tested range, so this spike does not answer the Logic 13 question.
Run: `logic2ableton samples/... -o out/l2a`. Took about 10 s, with no errors. The output XML is well-formed. It targets schema 12.0_12117, which is older than what our Live writes (12402); Live should upgrade it when opening.

## What came across (verified in the output .als and the report)
- Tempo 100, 4/4, 44.1k, key C major (from the plists).
- **7 audio tracks / 37 arrangement clips**: vocal comps, the textural layer and backing choir, at correct bar positions. Take-folder comps are resolved to the active comp.
- **16 MIDI tracks / 81 clips / 1,979 notes**: drums (Drummer "Modern 80s" + Drum Machine Designer pieces), synths, bass, lead. Also exported as .mid.
- Mixer volumes for 23 tracks. 2 return tracks are copied from the template.
- Plugin census: 4× **Analog Lab V** and 2× an unknown "CLMY/MPLG" plugin (probably Alchemy). Every preset name reads **"Untitled"**, so it does not recover Analog Lab preset names.

## What did not come across
- **Session View: nothing.** It outputs Arrangement only (scenes are template defaults and clip slots are empty). This is fine, because we only want its parser.
- **Markers: 0 locators.** I don't yet know whether the song has no markers or the parser missed them.
- Instruments and plugin state, automation, sends/returns (reverb/delay buses), fades.
- **19 used audio files were dropped as "MISSING"**, a parser gap around comps. Also 2 Apple Loops that live outside the package (riser and FX).
- Logic built-ins in this song cover more than Alchemy: the whole drum kit is Drummer/Drum Machine Designer, about 8 tracks.

## Verdict
The parser is a strong reader of structure (tracks, regions, notes, tempo, mixer). Its gaps are exactly the things we'd take from Logic's own exports anyway (printed audio, FX stems). Plan: **use its parser as a library for structure plus notes, and use our own Session View builder.** Always cross-check against Logic's bounced stems, because a silent drop (the 19 files) is exactly what the "nothing silently dropped" rule exists to catch.
Still open: opening the output in Live (user), Logic 13-saved projects, and markers.
