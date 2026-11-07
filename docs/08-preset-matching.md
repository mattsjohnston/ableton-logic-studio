# 08: Matching Logic built-in presets to Analog Lab V presets (Phase 3 exploration, 2026-11-07)

Goal: for parts played by Logic-only instruments (Alchemy, Sculpture, ES2, Quick Sampler) that can't load in Live, suggest the nearest Analog Lab V (AL) preset so a keys player can play it live.

## Where the data lives (all verified on this Mac)

### Analog Lab V
- **`/Library/Arturia/Presets/db.db3`**: a SQLite browser DB (13.6 MB, WAL mode; copy it with `-wal`/`-shm` before reading). It is owned by root but world-readable.
- **12,042 presets**, 39 packs, 54 instruments. 10,078 are "Factory" and 840 "Vintage Factory". The rest are Pigments packs and sample-library packs (Mellotron, CMI and so on). 254 are AL "multi" presets (two instruments layered).
- Tables: `Preset_Id` (name, pack, type_v2, subtype_v2, instrument_key, comment, file_path, rating); `Types_V2`/`Subtypes_V2`; `Preset_Characteristics` (122k rows, 11,400 presets tagged) joined to `Characteristics`/`Characteristics_Types`; `Preset_Metadata` (key/value copy of what's in the preset files); `Playlists_V2`; `Tags_V2` (just 7 colour labels).
- Taxonomy:
  - 14 Types: Pad 1955, Sequence 1500, Keys 1456, Lead 1426, Bass 1414, SFX 1194, Organ 707, Strings 509, Drums 505, Brass & Winds 479, E.Piano 448, Piano 225, Vocal 101, plus Template.
  - About 60 Subtypes (Atmosphere, Evolving Pad, Poly Lead, Winds Synth, Choir...).
  - Tags in 3 groups: **Characteristics** (46: Evolving, Ensemble, FM, Analog, Arpeggiated...), **Genres** (51) and **Styles** (25: Bright, Warm, Dark, Thin...).
  - `Preset_Id.comment` is a free-text description. The Factory ones are useful, for example "A very expressive flute patch...".
- Preset files are extensionless blobs at `/Library/Arturia/Presets/<Instrument>/<Factory|...>/<Pack>/<Name>`. All 12,042 `file_path`s exist on disk. They are the same boost-archive state as in the aupreset `Controller State`.
- **No audio previews exist locally.** There is no mp3/ogg/wav per preset. `/Library/Arturia/Samples` (2.6 GB) holds instrument source samples only. `Preset_Recommendation_Coords` and `Preset_Recommendation_Names` exist (Arturia's own similarity feature) but have **0 rows**. `Demo_Songs` has 825 rows, but those are pack demo songs (URLs), not per-preset previews.

### Logic
- **Alchemy** is the rich one: `~/Music/Audio Music Apps/Plug-In Settings/Alchemy/Alchemy_Preset_Ratings_And_Tags.db` (SQLite).
  - 458 items and 10,592 attribute links.
  - Attribute types: Category (17: Bass, Pads, Leads, Keys...), Subcategory (94), Timbre (21: Bright, Dark, Warm, Thin, Phat...), Genre (18), Articulation (23: Morphing, Sustained...) and Sound Library.
  - `items.STORAGE_PATH` points at the `.acp`.
  - "Light Harmonics" resolves to: Pads / Fast Attack / Poly / Bright / Thin / Nice / Cold / Ambient / Downtempo / Electronica / Soundtrack / Funk / Processed / Sustained / Morphing / Additive.
- `.acp` files are plain text (`Attribs = 4009,5055,...` are IDs into the same attribute table, plus `Comments`). 429 are installed at `/Library/Application Support/Logic/Plug-In Settings/Alchemy/<Library>/<Category>/`. More are in `/Applications/Logic Pro.app/Contents/Resources/Library.bundle/Plug-In Settings/Alchemy`.
- **Sculpture, ES2 and Quick Sampler** have no tag DB, only file names and folders. The Logic app bundle holds 225 Sculpture `.pst` files under folders like `02 Modeled Pads`, `07 Blown Instruments`, and 261 ES2 files. The only metadata is the folder name, the preset name and the Patch folders (`Patches/Instrument/Synthesizer/Pad`).
- **"Amped Synth Lead" was not found** in any file name on this Mac. It is probably a name inside the project (a Logic Patch or a Sculpture setting name). We would take it from the exported track or preset name. The script falls back to keywords ("lead" gives Lead).

## Approach A: metadata matching (built: `spikes/preset_index.py`, `spikes/preset_suggest.py`)
- The index (`out/analoglab_index.json`, 6.7 MB, runs in about a second) holds name, pack, type, subtype, instrument, characteristics/genres/styles and comment.
- The suggester has three steps:
  1. Resolve the Logic preset in the Alchemy tag DB, or by Sculpture/ES2/Patch file folder, to a category and tags.
  2. Map that vocabulary onto AL's (Pads to Pad, Bright to Bright, Phat to Huge, Morphing to Evolving and so on). The map is hand-written, about 40 entries.
  3. Score each AL preset on type match, tag overlap, name/description keywords and rating.
- Results:
  - **"Light Harmonics"** gives Harmonic Sweep (Pigments), Light Panorama (Synclavier) and Lightwave (Matrix-12), all pads with Bright/Ambient/Atmospheric tags. Plausible.
  - **"Voice Becomes Flute"** gives Analog Flute, Digital Voices and Voices and Strings. It handles the two-concept name sensibly.
  - **"Amped Synth Lead"** gives generic Lead presets (Anthem Lead, Saw Pulse Lead...). It is only a type match, since there are no Logic tags to use.
- This is **cheap, offline and explainable**. It tells you the category correctly but not the *timbre*. Ranking within a category is weak, and "type + keywords" is mostly a shortlist for the player.

## Approach B: audio similarity
- **Blocker: AL ships no audio previews and its own recommendation tables are empty.** To compare audio we would have to render each AL preset ourselves: load the AU, send a note (say C3 held for 4 s), record. That needs a plugin host. Options are Live, which was ruled out for this task, `pedalboard` (about 30 MB, loads AUs headless) or AudioUnit offline render.
- At 12k presets, at about 5 s each, that is about 17 hours on one core (less in parallel). It is a one-time job, but AL's own license and its state-restore behaviour are the risks (the plugin may show nag/GUI dialogs or fail in headless hosts).
- The Logic side has the same problem. Alchemy presets would have to be rendered too, or compared on the stems (which carry the arrangement, effects and note content, not just the timbre). Rendering the same single note through both gives a fair comparison.
- Embeddings:

| Option | Size | Licence | Notes |
|---|---|---|---|
| MFCC and spectral stats (numpy/librosa) | none | n/a | Cheap and crude. It captures brightness and envelope, not "pad-ness". |
| OpenL3 | about 20 MB | MIT | Needs TF or torch. |
| LAION-CLAP | about 600 MB | Apache-2.0 | Best semantic match. It can also compare text ("warm evolving pad") against audio. |

- Compute is not the problem on an M3 Pro: about 0.1 to 0.3 s per clip for CLAP.
- No tiny demo was done. With no previews there is nothing to compare without a rendering host, and that is a larger step than "cheap with numpy".

## Recommendation
1. **Ship A first.** The data is local, it needs no dependencies (stdlib only), and it runs instantly. Present it as a ranked shortlist of 5 to 10 per part, plus the player's own pick. Cache the choice in the song model (as with `OriginalPresetName`).
2. **Make it better with cheap steps.** Weight by Type/Subtype first. Allow a per-song instrument restriction (for example "Analog Lab V" multis only, which are the best for live use). Let the player pin a mapping so it learns. Add a Logic-to-AL tag table that you can edit, with TAG_MAP growing from real songs.
3. **Defer B**, and only do it if A isn't good enough. If done, render only a filtered subset (say pads, leads and keys from the Factory packs, about 5k), and use CLAP, using its text side for "describe the Logic preset in words" as a bridge for Sculpture/ES2 presets that have no tags.

## Risks
- Logic's Sculpture, ES2 and Quick Sampler tag data is sparse, so matches for those come from the category folder and name only.
- Preset names that are only user-defined (like "Amped Synth Lead") may not exist on disk anywhere.
- The `db.db3` schema is Arturia-internal and may change between AL versions. It was read here from v5.10.6. The index script copies the DB and does not write to it.
- The index covers *every* installed instrument, but a preset only works if that instrument is installed and licensed. 12,042 rows are listed even though the user may own only some. A per-instrument filter is needed.
- Name-keyword scoring can be misleading (for example "Light Panorama" for "Light Harmonics").

## Status
- **Verified:** DB path, schema and counts, tag vocabularies, that files exist, that no previews exist, Alchemy tag DB contents, and the three demo queries.
- **Assumed / not tested:** CLAP/OpenL3 sizes and speeds (from general knowledge, not measured here), the render-time estimate, and whether AL can be driven headless.
