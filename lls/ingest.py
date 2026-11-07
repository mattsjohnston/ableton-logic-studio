"""Logic .logicx (read-only) -> song folder: song.json + midi/ + instruments/ + stems/raw/.

The song model (song.json) is the source of truth; see docs/model.md.
"""
import gzip, json, os, re, shutil, tempfile, wave, xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.ableton_generator import generate_als

from . import logic_plugins, sections

VOCAL_HINTS = re.compile(r"vocal|vox|golden era plate|phone filter|male vocal|female vocal", re.I)
BACKING_HINTS = re.compile(r"backing|bv|choir|harmon|double", re.I)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "part"


def _arrangement_audio(project, workdir):
    """Let logic2ableton resolve comps/trims/positions, then read its arrangement clips back."""
    als = generate_als(project, Path(workdir), copy_audio=False)
    root = ET.fromstring(gzip.open(als).read())
    out = {}
    for t in root.iter("AudioTrack"):
        name = t.find("Name/EffectiveName").get("Value")
        for c in t.findall(".//ArrangerAutomation/Events/AudioClip"):
            v = lambda p: float(c.find(p).get("Value"))
            if c.find("Disabled") is not None and c.find("Disabled").get("Value") == "true": continue
            out.setdefault(name, []).append(dict(
                file=c.find("SampleRef/FileRef/Path").get("Value"), start=float(c.get("Time")),
                end=v("CurrentEnd"), src_start=v("Loop/LoopStart"), name=c.find("Name").get("Value")))
    return out


def _read_wav(path):
    with wave.open(path) as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        a = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        a = np.where(a & 0x800000, a - 0x1000000, a) / 8388608.0
    elif sw == 2:
        a = np.frombuffer(raw, "<i2") / 32768.0
    elif sw == 4:
        a = np.frombuffer(raw, "<i4") / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width {sw} in {path}")
    a = a.reshape(-1, ch)
    if ch == 1: a = np.repeat(a, 2, axis=1)
    return a[:, :2].astype(np.float32), sr


def write_wav(path, data, sr):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pcm = (np.clip(data, -1, 1) * 8388607).astype(np.int32)
    b = np.empty((pcm.size, 3), np.uint8)
    flat = pcm.reshape(-1)
    b[:, 0], b[:, 1], b[:, 2] = flat & 0xFF, (flat >> 8) & 0xFF, (flat >> 16) & 0xFF
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(3); w.setframerate(sr); w.writeframes(b.tobytes())


def render_stem(clips, tempo, sr, length_beats, out_path):
    """Mix placed regions into one full-length stem from bar 1 (raw audio: no Logic FX)."""
    spb = 60.0 / tempo
    buf = np.zeros((int(length_beats * spb * sr) + sr, 2), np.float32)
    cache = {}
    for c in clips:
        if c["file"] not in cache:
            try: cache[c["file"]] = _read_wav(c["file"])
            except Exception as e:
                print(f"   ! cannot read {c['file']}: {e}"); cache[c["file"]] = None
        if cache[c["file"]] is None: continue
        a, fsr = cache[c["file"]]
        s0 = int(c["src_start"] * spb * fsr); n = int((c["end"] - c["start"]) * spb * fsr)
        seg = a[s0:s0 + n]
        d0 = int(c["start"] * spb * sr)
        seg = seg[: max(0, len(buf) - d0)]
        buf[d0:d0 + len(seg)] += seg
    write_wav(out_path, buf, sr)


def _quicksampler_files(logicx):
    import glob, plistlib
    md = sorted(glob.glob(os.path.join(logicx, "Alternatives", "*", "MetaData.plist")))
    return list(plistlib.load(open(md[0], "rb")).get("QuicksamplerFiles", [])) if md else []


def default_treatment(part):
    inst = part.get("instrument") or {}
    if part["kind"] == "audio":
        if VOCAL_HINTS.search(part["name"]) and not BACKING_HINTS.search(part["name"]):
            return "live-vocal"
        return "backing-audio"
    if inst.get("type") == "analoglab":
        return "midi-clip"
    return "backing-audio"  # Logic built-ins can't load in Live: needs the exported stem


def ingest(logicx, song_dir):
    logicx, song_dir = os.path.abspath(logicx), os.path.abspath(song_dir)
    os.makedirs(song_dir, exist_ok=True)
    proj = parse_logic_project(Path(logicx))
    tempo, bpb = proj.tempo, proj.time_sig_numerator * 4 / proj.time_sig_denominator
    plugins = logic_plugins.track_plugins(logicx)
    with tempfile.TemporaryDirectory() as tmp:
        audio = _arrangement_audio(proj, tmp)

    parts, spans, used = [], [], set()
    length = 0.0
    for name, clips in audio.items():
        for c in clips: spans.append((c["start"], c["end"])); length = max(length, c["end"])
    for t in proj.midi_tracks:
        for r in t.regions:
            if not r.is_muted: spans.append((r.start_beats, r.end_beats)); length = max(length, r.end_beats)
    length = -(-length // bpb) * bpb

    def instrument_for(track):
        p = plugins.get(track) or {}
        rec = p.get("instrument")
        if not rec: return {"type": "none"}
        if rec["display"] == "Analog Lab V" and rec.get("aupreset"):
            pid = slug(track); path = f"instruments/{pid}.aupreset"
            os.makedirs(os.path.join(song_dir, "instruments"), exist_ok=True)
            open(os.path.join(song_dir, path), "wb").write(rec["aupreset"])
            return {"type": "analoglab", "preset": rec["state_name"], "state": path}
        if rec["display"] == "Quick Sampler":
            return {"type": "logic-builtin", "name": "Quick Sampler", "preset": rec["preset"].replace("#Custom#", "") or None}
        return {"type": "logic-builtin", "name": rec["display"], "preset": (rec["preset"] or "").replace("#Custom#", "") or None}

    for name, clips in audio.items():
        pid = slug(name); used.add(pid)
        stem = f"stems/raw/{pid}.wav"
        print(f" rendering raw stem: {name} ({len(clips)} regions)")
        render_stem(clips, tempo, proj.sample_rate, length, os.path.join(song_dir, stem))
        parts.append({"id": pid, "name": name, "kind": "audio", "instrument": {"type": "audio"},
                      "stem": None, "raw_stem": stem, "regions": len(clips),
                      "inserts": [r["display"] for r in (plugins.get(name) or {}).get("inserts", [])]})
    for t in proj.midi_tracks:
        pid = slug(t.name)
        while pid in used: pid += "-midi"
        used.add(pid)
        notes = [[n.pitch, round(n.start_beats, 6), round(n.duration_beats, 6), n.velocity] for n in t.notes]
        os.makedirs(os.path.join(song_dir, "midi"), exist_ok=True)
        json.dump({"notes": notes}, open(os.path.join(song_dir, "midi", f"{pid}.json"), "w"))
        parts.append({"id": pid, "name": t.name, "kind": "midi", "instrument": instrument_for(t.name),
                      "stem": None, "midi": f"midi/{pid}.json", "notes": len(notes),
                      "regions": len([r for r in t.regions if not r.is_muted]),
                      "inserts": [r["display"] for r in (plugins.get(t.name) or {}).get("inserts", [])]})
    from .lineup import role_hint
    for p in parts:
        p["default_treatment"] = default_treatment(p)
        p["role_hint"] = role_hint(p)

    # every Logic track must be accounted for
    covered = {p["name"] for p in parts}
    missing_loops = {m for w in proj.compatibility_warnings for m in re.findall(r"/([^/,]+)\.(?:caf|aif|aiff|wav)", w)}
    ignored = []
    for n, p in plugins.items():
        if n in covered: continue
        if n in missing_loops:
            ignored.append({"name": n, "status": "MISSING", "reason": "plays an Apple Loop stored outside the project; include it in the Logic stem export"})
        elif p["strip"] == "0x42":
            ignored.append({"name": n, "status": "covered-by-fx-stem", "reason": "aux return (" + ", ".join(r["display"] for r in p["inserts"]) + "); print it as an FX stem"})
        elif n in ("Master", "Preview", "Click") or p["strip"] == "0x46":
            ignored.append({"name": n, "status": "internal", "reason": "Logic internal (master/preview/metronome); the tool generates its own click"})
        else:
            ignored.append({"name": n, "status": "empty", "reason": "no regions"})

    secs = sections.propose(spans, length, beats_per_bar=bpb)
    song = {
        "schema": 1, "title": proj.name, "tempo": tempo,
        "time_signature": [proj.time_sig_numerator, proj.time_sig_denominator],
        "sample_rate": proj.sample_rate, "length_beats": length,
        "source": {"logicx": logicx, "parser": "logic2ableton " + __import__("logic2ableton").__version__ if hasattr(__import__("logic2ableton"), "__version__") else "logic2ableton"},
        "sections": [{"id": f"s{i+1}", "name": f"Section {i+1}", "start": a, "end": b} for i, (a, b) in enumerate(secs)],
        "parts": parts, "ignored_tracks": ignored,
        "quicksampler_files": _quicksampler_files(logicx),
        "warnings": proj.compatibility_warnings,
        "performers": [], "lineups": {"default": {"description": "Everything plays back; vocals live", "treatments": {}}},
    }
    path = os.path.join(song_dir, "song.json")
    if os.path.exists(path):
        song = _merge_user_edits(json.load(open(path)), song)
    if not song.get("sections_edited"):
        apply_section_names(song, song_dir)
    tmp = path + ".tmp"; json.dump(song, open(tmp, "w"), indent=2); os.replace(tmp, path)
    return song


def apply_section_names(song, song_dir):
    try:
        for sec, name in zip(song["sections"], sections.suggest_names(song, song_dir)):
            sec["name"] = name
    except Exception as e:  # naming is a convenience; never fail ingest over it
        print(f"   (section naming skipped: {e})")


def _merge_user_edits(old, new):
    """Re-ingest keeps what the user (or link-stems) set: lineups, performers, section names/boundaries, stems,
    treatment overrides, and parts that only exist from exports."""
    new["lineups"] = old.get("lineups") or new["lineups"]
    new["performers"] = old.get("performers", new["performers"])
    if old.get("sections_edited"):
        new["sections"], new["sections_edited"] = old["sections"], True
    if old.get("mix"): new["mix"] = old["mix"]
    old_parts = {p["id"]: p for p in old.get("parts", [])}
    for p in new["parts"]:
        o = old_parts.pop(p["id"], None)
        if not o: continue
        for k in ("stem", "default_treatment", "role_hint", "notes_user"):
            if o.get(k) is not None and (k != "default_treatment" or o.get("treatment_edited")):
                p[k] = o[k]
        if o.get("treatment_edited"): p["treatment_edited"] = True
    new["parts"] += [o for o in old_parts.values() if o.get("origin")]  # export-only parts (aux/FX stems)
    return new
