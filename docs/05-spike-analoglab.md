# Spike 5: Analog Lab state portability (2026-11-07): AU TRANSFER WORKS

## Verified: Logic side
- Logic stores each AU plugin's state in ProjectData as a complete **`.aupreset` XML plist** (keys: `Controller State`, `data`, `manufacturer`, `subtype`, `type`, `version`, `name`, `element-name`). It is plain text inside the binary, so you can find it without reverse-engineering the format.
- For Analog Lab V (`Artu/Alav`, v5.10.6), `Controller State` is base64 of a boost `serialization::archive` text stream. It contains **`OriginalPresetName`, `OriginalPackName`, `Type`/`Subtype`**, the preset description, and (presumably) every parameter.
- *Thoughts For The Night* contains: New Vision sw26 (User), Blade Of Canada (AL Factory DX7), Da Fonk (Factory, bass), Desynk (Factory, organ). It also has 2× **Melodyne** (`CLMY/MPLG`, an audio effect on vocals), and **Melodyne is not installed on this Mac**. Bounce the vocals on the machine that has it.
- `spikes/extract_aupresets.py` writes each state out as a valid `.aupreset` (plutil-lint OK) → `out/aupresets/`.
- Not yet mapped: which aupreset belongs to which track. The order in the file appears to follow the track/strip order. This needs the parser's channel-strip linkage.

## Verified: Live side (12.4.25b1)
- I made a set in Live by driving its UI (Orca computer-use plus System Events menus). It has Analog Lab V as AU and as VST3, and Live saved it → `samples/analoglab-live-saved.als`.
- **AU:** `AuPluginDevice/PluginDesc/AuPluginInfo` (ComponentType/SubType/Manufacturer as ints: aumu/Alav/Artu) + `Preset/AuPreset/Buffer`. The Buffer is hex of **the same aupreset XML plist Logic stores** (identical key set).
- **VST3:** `PluginDevice/PluginDesc/Vst3PluginInfo` + `Vst3Preset/ProcessorState`, which holds the raw boost archive text (the same as Logic's decoded `Controller State`). It also has a `ControllerState`. The class id is {41727475-4156-4953-416C-617650726F63}.
- **Injection test (`spikes/inject_analoglab.py`):** I wrote Logic's "Da Fonk" aupreset into the AU Buffer and "Blade Of Canada" into the VST3 ProcessorState, opened the set in Live, saved it from Live, and read the result back. Live asks each plugin for its state when saving.
  - **AU → "Da Fonk", full state. ✅ The exact Logic sound transfers.**
  - VST3 → default "Classic Jun Keys" ❌. ProcessorState alone gets rejected, probably because ControllerState is needed too. Not pursued, since we're Mac-only and AU works.
- **Decision: generated sets use Analog Lab V as AU**, with the Logic aupreset injected. The preset name is still recorded in the song model as a manual fallback.
- Bonus: Live logs "VST3: Found 16 program change parameters for plugin Analog Lab V", which hints that Program Change preset switching may work (relevant for a shared-pool setlist). Untested.

## Generator learnings from this
- Live won't save a set that sits outside a Project folder. The generator must create `<Project>/Ableton Project Info/`.
