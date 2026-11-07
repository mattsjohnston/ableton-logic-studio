# Spike 3: Extensions SDK (API 1.0.0, Live 12.4.25b1), desk research (2026-11-07)

Sources: method names from the beta's `Helpers/ExtensionHost/ExtensionHostNodeModule.node` (via `strings`), plus the community `ableton-extensions-skill` API reference. The official typings, docs and examples are **only in the Centercode beta download** (npm `@ableton-extensions/*` returns 404).

| Can | Can't (no API found) |
|---|---|
| create/delete/rename audio & MIDI tracks | create return/group tracks, track color, **routing** (e.g. click → outs 3-4) |
| create/delete/rename scenes, set song tempo | scene color, scene tempo/time-sig setters |
| session clips: `createAudioClip({filePath})`, `createMidiClip`, write notes; clip name/color/loop/warp | launch quantization, follow actions, clip envelopes |
| `insertDevice(name)` on tracks/chains, `rack.insertChain`, set any device param | load presets, chain zones / selector ranges, **macro mapping**, MIDI mapping |
| modal webview dialogs (one-way JSON return), progress dialogs, `withinTransaction` undo | save/open sets |
| full Node fs; `renderPreFxAudio` (bounce a track to WAV) | realtime/audio/MIDI callbacks |
| Unknown: whether `insertDevice("Analog Lab V")` loads a 3rd-party plugin | |

**Verdict:** this can't be the builder, because it has no routing, quantize, macro maps or save. It can be a prep-time convenience: a "Refresh lineup" command and a dialog inside Live. `renderPreFxAudio` is interesting for printing Live-side stems for the phone fallback.
