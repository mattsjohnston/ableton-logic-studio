"""Drum Rack / Simpler generation from Live-saved prototypes (Live 12 Suite demo song, schema 12.0_12402).

Why: Logic's Quick Sampler parts (kick, snare, ...) are not playable in Live, but each only holds ONE sample, so we
rebuild them as Simpler (one track per part) or as pads of one Drum Rack that an e-kit can play over General MIDI.

Prototypes: `DrumGroupDevice` (with 11 `DrumBranch`) and its `OriginalSimpler` are copied from the demo song. The
core-library Drum Rack .adg files are NOT usable as prototypes: they store `DrumBranchPreset`s, not live branches.

ID rules (docs/02-spike-als.md): global pointee IDs must be unique file-wide. Tracks returned here still carry the
demo's IDs; pass `ids=als.Ids(root)` to renumber, or renumber at add time (build.add_track does).
"""
import copy, difflib, os, re, shutil, struct, warnings, wave
import xml.etree.ElementTree as ET
from collections import Counter

from . import als

# ---------------------------------------------------------------- General MIDI drum map
# category -> notes in order of preference (the 2nd/3rd are used when two parts share a category)
GM_MAP = {
    "kick": (36, 35), "snare": (38, 40), "rim": (37,), "clap": (39,),
    "hat": (42, 44), "open-hat": (46,), "tom-low": (41, 43), "tom-mid": (45, 47), "tom-high": (48, 50),
    "crash": (49, 57), "ride": (51, 59), "cowbell": (56,), "tambourine": (54,), "shaker": (70, 82),
    "conga": (63, 64), "percussion": (75, 76), "fx": (39, 56, 55, 53),
}
# (category, regex on lowercase name); first match wins, so specific rules go first
_KEYWORDS = [
    ("open-hat", r"open"), ("kick", r"kick|bass drum|\bbd\b"), ("snare", r"snare|\bsd\b"), ("rim", r"\brim|sidestick"),
    ("clap", r"clap"), ("hat", r"hi-?\s?hat|\bhh\b|\bhat"), ("crash", r"crash"), ("ride", r"ride"),
    ("tom-low", r"tom.*(low|floor)|(low|floor).*tom"), ("tom-high", r"tom.*high|high.*tom"),
    ("tom-mid", r"\btom"), ("shaker", r"shaker|maraca"), ("tambourine", r"tamb"), ("cowbell", r"cowbell"),
    ("conga", r"conga|bongo"), ("percussion", r"perc"), ("fx", r"\bfx\b|zap|stomp|cymbal fx|impact|riser|sweep"),
]
_OPEN_HAT = re.compile(r"open")


def _name(part):
    return (part if isinstance(part, str) else part.get("name") or part.get("id", "")).lower()


def category(part):
    """Drum category of a part by name keywords, or None."""
    n = _name(part)
    if "hat" in n and _OPEN_HAT.search(n): return "open-hat"
    for cat, rx in _KEYWORDS:
        if cat == "open-hat": continue
        if re.search(rx, n): return cat
    return None


def classify(part):
    """Preferred GM note for a part (dict or name) by name keywords, or None if it is not a recognisable drum."""
    cat = category(part)
    return GM_MAP[cat][0] if cat else None


def assign_notes(parts):
    """{part_id: unique GM note}. Parts sharing a category take that category's alternates (Kick 1 -> 36, Kick 2 -> 35)."""
    used, out = set(), {}
    for p in parts:
        cat = category(p)
        cands = list(GM_MAP.get(cat, ())) + [n for n in range(35, 82) if n not in GM_MAP.get(cat, ())]
        note = next((n for n in cands if n not in used), None)
        if note is None: raise ValueError("out of GM drum notes")
        used.add(note); out[p["id"] if isinstance(p, dict) else p] = note
    return out


def remap_notes(notes, gm_note):
    """[(pitch, start, dur, vel)] -> same notes on the part's GM pad note (for clips driving the Drum Rack)."""
    return [(gm_note, s, d, v) for _, s, d, v in notes]


# ---------------------------------------------------------------- part -> sample mapping
def _norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())


def match_sample(part_name, files):
    """Pick the Quick Sampler file for a part: 'Kick 1 - Flashback' <-> '.../Kick 1 - Flashback.aif'.
    Falls back to word containment ('Snare 2 - Deep End' <-> 'TinySnare_DeepEnd.aif') then fuzzy ratio."""
    stems = {f: _norm(os.path.splitext(os.path.basename(f))[0]) for f in files}
    pn = _norm(part_name)
    for f, s in stems.items():
        if s == pn: return f
    inst, _, kit = part_name.partition(" - ")
    word = _norm(re.sub(r"[\d]+", "", inst))
    hits = [f for f, s in stems.items() if kit and _norm(kit) in s and word and word in s]
    if len(hits) == 1: return hits[0]
    best = max(files, key=lambda f: difflib.SequenceMatcher(None, pn, stems[f]).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, pn, stems[best]).ratio() >= 0.6: return best
    return None


def quicksampler_files(logicx):
    """Quick Sampler sample paths listed in a .logicx project's MetaData.plist (QuicksamplerFiles)."""
    import plistlib
    p = os.path.join(logicx, "Alternatives", "000", "MetaData.plist")
    with open(p, "rb") as f: return list(plistlib.load(f).get("QuicksamplerFiles", []))


def sample_for_part(part, song, samples=None):
    """Resolve a part's sample path: explicit `samples[id]`, `instrument.sample`, else name match in
    `song['quicksampler_files']`. May return a path that does not exist (caller warns)."""
    if samples and part["id"] in samples: return samples[part["id"]]
    s = part.get("instrument", {}).get("sample")
    if s: return s
    return match_sample(part["name"], song.get("quicksampler_files", []))


def pitch_stats(song_dir, part):
    """Counter {pitch: n_notes} of a MIDI part."""
    import json
    return Counter(n[0] for n in json.load(open(os.path.join(song_dir, part["midi"])))["notes"])


def main_pitch(song_dir, part):
    return pitch_stats(song_dir, part).most_common(1)[0][0]


# ---------------------------------------------------------------- audio file info
def audio_info(path):
    """(frames, sample_rate) of a WAV or AIFF file (stdlib only; aifc is gone in 3.13)."""
    with open(path, "rb") as f: head = f.read(12)
    if head[:4] == b"RIFF":
        with wave.open(path) as w: return w.getnframes(), w.getframerate()
    if head[:4] == b"FORM":
        with open(path, "rb") as f: d = f.read()
        i = 12
        while i + 8 <= len(d):
            cid, sz = d[i:i + 4], struct.unpack(">I", d[i + 4:i + 8])[0]
            if cid == b"COMM":
                frames = struct.unpack(">I", d[i + 10:i + 14])[0]
                exp, mant = struct.unpack(">HQ", d[i + 16:i + 26])
                return frames, int(round(mant * 2.0 ** ((exp & 0x7FFF) - 16383 - 63)))
            i += 8 + sz + (sz & 1)
    raise ValueError(f"unsupported audio file {path}")


# ---------------------------------------------------------------- prototypes
class _Drum:
    """Cleaned prototypes: a single-pad DrumGroupDevice and a bare Simpler."""
    def __init__(self):
        demo = als.load_gz(als.DEMO)
        dgd = copy.deepcopy(next(demo.iter("DrumGroupDevice")))
        br = dgd.find("Branches")
        self.branch = copy.deepcopy(br[0])
        als.clear(br)
        self.rack = dgd
        sim = self.branch.find("DeviceChain/MidiToAudioDeviceChain/Devices/OriginalSimpler")
        self.simpler = _clean_simpler(copy.deepcopy(sim))
        for t in ("OriginalFileRef", "PresetRef"):
            als.clear(self.branch.find(f"SourceContext/Value/BranchSourceContext/{t}"))
        als.setv(self.branch, "SourceContext/Value/BranchSourceContext/BrowserContentPath", "")
        als.clear(self.branch.find("DeviceChain/MidiToAudioDeviceChain/Devices"))


def _clean_simpler(sim):
    """Single sample part, trigger-style one-shot, 0 dB."""
    sp = sim.find("Player/MultiSampleMap/SampleParts")
    for extra in list(sp)[1:]: sp.remove(extra)
    als.setv(sim, "VolumeAndPan/Volume/Manual", 0)
    als.setv(sim, "Globals/PlaybackMode", 1)            # One-Shot
    als.setv(sim, "VolumeAndPan/OneShotEnvelope/SustainMode/Manual", 0)  # Trigger: ignore note-off
    return sim


def _drum(protos):
    d = getattr(protos, "_drum", None)
    if d is None: d = protos._drum = _Drum()
    return d


def _simpler(protos, sample_path, project_dir, root):
    """OriginalSimpler element loaded with `sample_path` (copied into project_dir/Samples/)."""
    sim = copy.deepcopy(_drum(protos).simpler)
    smp = os.path.join(project_dir, "Samples"); os.makedirs(smp, exist_ok=True)
    dst = os.path.join(smp, os.path.basename(sample_path))
    if os.path.abspath(dst) != os.path.abspath(sample_path): shutil.copy2(sample_path, dst)
    frames, sr = audio_info(dst)
    part = sim.find("Player/MultiSampleMap/SampleParts/MultiSamplePart")
    setv = lambda p, v: als.setv(part, p, v)
    setv("Name", os.path.splitext(os.path.basename(dst))[0]); setv("RootKey", root)
    end = max(frames - 1, 0)
    for p, v in [("SampleStart", 0), ("SampleEnd", end), ("SustainLoop/Start", 0), ("SustainLoop/End", end),
                 ("ReleaseLoop/Start", 0), ("ReleaseLoop/End", end)]: setv(p, v)
    sref = part.find("SampleRef"); fr = sref.find("FileRef")
    for p, v in [("RelativePathType", 1), ("RelativePath", os.path.relpath(dst, project_dir)), ("Path", os.path.abspath(dst)),
                 ("Type", 1), ("LivePackName", ""), ("LivePackId", ""), ("OriginalFileSize", os.path.getsize(dst)),
                 ("OriginalCrc", 0)]: als.setv(fr, p, v)
    als.setv(sref, "LastModDate", int(os.path.getmtime(dst))); als.setv(sref, "DefaultDuration", frames)
    als.setv(sref, "DefaultSampleRate", sr)
    als.clear(sref.find("SourceContext"))
    wm = part.find("SampleWarpProperties/WarpMarkers"); als.clear(wm)
    secs = frames / sr
    ET.SubElement(wm, "WarpMarker", Id="0", SecTime="0", BeatTime="0")
    ET.SubElement(wm, "WarpMarker", Id="1", SecTime=repr(secs), BeatTime=repr(secs * 2))
    als.setv(part, "SampleWarpProperties/IsWarped", False)
    return sim


def _clean_track(t, name, color):
    """Copy-safe track: als.strip_track leaves the demo's arrangement take-lane clips (foreign PointeeIds), drop them."""
    for tl in t.iter("TakeLanes"):
        for ev in tl.iter("Events"): als.clear(ev)
    als.setv(t, "Name/EffectiveName", name); als.setv(t, "Name/UserName", name); als.setv(t, "Color", color)

# ---------------------------------------------------------------- public builders
def make_simpler_track(protos, sample_path, project_dir, name, color, root=60, one_shot=True, ids=None, info=None):
    """MIDI track with a Simpler playing `sample_path` (copied to <project_dir>/Samples/).

    `root` is the MIDI note that plays the sample unpitched: pass the part's main pitch (`main_pitch`) so a clip
    with the part's original notes triggers it unchanged. Other pitches are transposed relative to `root`.
    one_shot=True ignores note-off (drum hits ring out). A missing sample gives an empty track + a warning in `info`."""
    t = copy.deepcopy(protos.midi_track)
    devs = t.find("DeviceChain/DeviceChain/Devices"); als.clear(devs)
    if os.path.isfile(sample_path or ""):
        sim = _simpler(protos, sample_path, project_dir, root)
        als.setv(sim, "Globals/PlaybackMode", 1 if one_shot else 0)
        devs.append(sim)
    else:
        msg = f"{name}: sample not found ({sample_path}); track has no instrument"
        warnings.warn(msg)
        if info is not None: info.append(msg)
    _clean_track(t, name, color)
    if ids is not None: ids.renumber(t)
    return t


def make_drum_track(protos, part_ids, song, song_dir, project_dir=None, name="Drums", color=5, samples=None,
                    notes=None, ids=None, info=None):
    """MIDI track with a Drum Rack: one pad per part, receiving its GM note, holding a Simpler with the part's sample.

    Returns (track_element, {part_id: gm_note}); sequence the rack by remapping each part's clip notes with
    `remap_notes(part_notes, gm[part_id])`. `notes` overrides the GM note per part id. Parts whose sample is missing
    get an empty pad (still on its GM note) and a message appended to `info` (and warnings.warn). `project_dir`
    defaults to song_dir; samples are copied to <project_dir>/Samples/."""
    project_dir = project_dir or song_dir
    by_id = {p["id"]: p for p in song["parts"]}
    parts = [by_id[i] for i in part_ids]
    gm = assign_notes(parts)
    if notes: gm.update(notes)
    D = _drum(protos)
    rack = copy.deepcopy(D.rack)
    br = rack.find("Branches")
    for idx, p in enumerate(parts):
        b = copy.deepcopy(D.branch); b.set("Id", str(idx))
        als.setv(b, "Name/EffectiveName", p["name"]); als.setv(b, "Name/UserName", p["name"])
        als.setv(b, "BranchInfo/ReceivingNote", gm[p["id"]]); als.setv(b, "BranchInfo/SendingNote", 60)
        als.setv(b, "BranchInfo/ChokeGroup", 0)
        als.setv(b, "Color", 8 + idx % 14); als.setv(b, "AutoColored", False)
        path = sample_for_part(p, song, samples)
        if path and os.path.isfile(path):
            b.find("DeviceChain/MidiToAudioDeviceChain/Devices").append(_simpler(protos, path, project_dir, 60))
        else:
            msg = f"{p['name']}: sample not found ({path}); empty pad on GM note {gm[p['id']]}"
            warnings.warn(msg)
            if info is not None: info.append(msg)
        br.append(b)
    t = copy.deepcopy(protos.midi_track)
    devs = t.find("DeviceChain/DeviceChain/Devices"); als.clear(devs); devs.append(rack)
    _clean_track(t, name, color)
    if ids is not None: ids.renumber(t)
    return t, gm
