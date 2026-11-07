# Spike 5: Analog Lab state portability (in progress, 2026-11-07)

## Verified: Logic side
- Logic stores each AU plugin's state in ProjectData as a complete **`.aupreset` XML plist** (keys: `Controller State`, `data`, `manufacturer`, `subtype`, `type`, `version`, `name`, `element-name`). It is plain text inside the binary, so you can find it without reverse-engineering the format.
- For Analog Lab V (`Artu/Alav`, v5.10.6), `Controller State` is base64 of a boost `serialization::archive` text stream. It contains **`OriginalPresetName`, `OriginalPackName`, `Type`/`Subtype`**, the preset description, and (presumably) every parameter.
- *Thoughts For The Night* contains: New Vision sw26 (User), Blade Of Canada (AL Factory DX7), Da Fonk (Factory, bass), Desynk (Factory, organ). It also has 2× **Melodyne** (`CLMY/MPLG`, an audio effect on vocals), and **Melodyne is not installed on this Mac**. Bounce the vocals on the machine that has it.
- `spikes/extract_aupresets.py` writes each state out as a valid `.aupreset` (plutil-lint OK) → `out/aupresets/`.
- Not yet mapped: which aupreset belongs to which track. The order in the file appears to follow the track/strip order. This needs the parser's channel-strip linkage.

## Still to test
1. Load an extracted `.aupreset` into **Analog Lab V (AU) in Live**. Live lists AU presets from `~/Library/Audio/Presets/<Manufacturer>/<Plugin>/`. If this works, the exact sound transfers, tweaks included.
2. Compare with **Live's VST3 state** for the same preset (needs a Live-saved set). If the VST3 chunk carries the same boost archive, we can write Logic's state straight into generated `.als` PluginDevice blocks.
3. A fallback that always works: the preset name lets the user load it by hand from the Analog Lab browser.
