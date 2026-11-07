# Data model (contract for every module), schema 1

Files:
- `band.json` (repo root): who is in the band, what they play, and how the rig is wired. Shared by all songs.
- `songs/<slug>/song.json`: one song, written by `lls ingest` and then edited by the UI.
- `setlists/<slug>.json`: an ordered list of songs, with the lineup to use for each.

Times are **beats from bar 1** (quarter notes). IDs are kebab-case slugs.

## band.json
```json
{
  "schema": 1,
  "interface": {"name": "Volt 276", "outputs": {"main": [1, 2], "click": [3, 4]}, "inputs": {"vocal-1": 1, "guitar": 2}},
  "performers": [
    {"id": "matt", "name": "Matt", "roles": ["vocal", "keys"],
     "rigs": [
       {"id": "matt-keys", "kind": "keys", "controller": "Novation Impulse", "midi_port": "Impulse (MIDI)", "midi_channel": 1},
       {"id": "matt-vox", "kind": "vocal", "audio_input": 1}
     ]}
  ]
}
```
- `kind`: `keys | drums | guitar | bass | vocal | grid`.
- `midi_port` is the name Live shows. `null` means all ins.
- `midi_channel` is 1-16, or `null` for all.
- `audio_input` is an interface input number (mono), or `[l, r]` for a stereo pair.

## song.json (fields added on top of what ingest writes)
```json
{
  "sections": [{"id": "s1", "name": "Intro", "start": 0, "end": 16, "tempo": null, "vamp": false}],
  "parts": [{"id": "bass-line", "name": "Bass Line", "kind": "midi", "instrument": {...}, "default_treatment": "midi-clip",
             "stem": "stems/export/bass-line.wav", "raw_stem": null, "midi": "midi/bass-line.json", "role_hint": "bass"}],
  "lineups": {
    "duo": {
      "name": "Duo (Matt + Sam)",
      "performers": ["matt", "sam"],
      "assignments": {
        "bass-line": {"treatment": "live-midi", "performer": "sam", "rig": "sam-keys",
                      "sections": {"s7": "midi-clip"}},
        "lead-vocal": {"treatment": "live-vocal", "performer": "matt", "rig": "matt-vox"}
      }
    }
  }
}
```
- A part without an assignment in a lineup uses `default_treatment`. `sections` holds per-section treatment overrides.
- `stem` is an exported Logic stem, which is preferred. `raw_stem` is rendered from the raw regions (no FX) as a fallback.

## Treatments
| id | Needs | Build output |
|---|---|---|
| `live-vocal` | performer + rig (vocal) | audio track, input = rig audio_input, monitoring In, no clips |
| `backing-audio` | stem | audio track with section clips from the stem |
| `audio-loop` | stem | same as backing-audio, but clips loop (for vamps) |
| `midi-clip` | midi + playable instrument | MIDI track with instrument + section clips |
| `live-midi` | performer + rig (keys) + instrument | MIDI track with instrument, input = rig port/channel, monitoring In; no clips (or muted guide clips) |
| `live-looped` | as live-midi | live-midi + empty clip slots armed for looping |
| `live-drums` | performer + rig (drums) + drum rack | MIDI track with Drum Rack, input = rig port/channel |
| `live-guitar` | performer + rig (guitar/bass) | audio track, input = rig audio_input, monitoring In |
| `click` | none | generated click → outputs.click |
| `dropped` | none | nothing (listed in report) |

Playable instrument: `analoglab` (AU with injected state) or `drumrack` (from Quick Sampler samples). Logic built-ins are not playable in Live, so their parts need a `stem` or a rebuilt instrument.

## Validation (lls/lineup.py → list of {level, part, section, message})
- `error`: a live treatment with no performer/rig; a rig kind that doesn't fit the treatment; backing-audio with no stem; midi-clip/live-midi with no playable instrument; two live parts on the same performer+rig sounding at the same time when they need different instruments (that needs a chain switch, see below); two performers' rigs sharing the same MIDI port+channel.
- `warn`: a performer with more than 2 overlapping live keys parts (hands); a part with notes outside the rig's playable range; a lineup that leaves a part "dropped".
- **Sound switching:** if one performer+rig plays several instrument parts in different sections, the builder puts them on separate MIDI tracks that all take input from the same rig. Each section's scene arms only the right track through clip-launch track monitoring. MVP: separate tracks with monitoring Auto plus a launched placeholder clip.
