"""Attach Logic's exported stems to song parts:  python -m lls link-stems <song_dir> <export_dir>

Matches files to parts by normalized track name (Logic names exports after the track, sometimes with a
project prefix or numbering). Unmatched files become new backing-audio parts (aux/FX returns etc.), and the
full-mix bounce is recorded as song["mix"]. Every file and every part is reported, so nothing slips through.
"""
import json, os, re, shutil, difflib

AUDIO = (".wav", ".aif", ".aiff")


def norm(s):
    s = os.path.splitext(os.path.basename(s))[0].lower()
    s = re.sub(r"^\d+[\s_.-]+", "", s)  # leading numbering
    return re.sub(r"[^a-z0-9]+", "", s)


def link(song_dir, export_dir, copy=True):
    path = os.path.join(song_dir, "song.json")
    song = json.load(open(path))
    title = norm(song["title"])
    files = sorted(f for f in os.listdir(export_dir) if f.lower().endswith(AUDIO))
    parts = {norm(p["name"]): p for p in song["parts"]}
    matched, extra, report = set(), [], []
    from .ingest import is_silent_stem
    for f in files:
        n = norm(f)
        if "fullmix" not in n and is_silent_stem(os.path.join(export_dir, f)):
            # Logic exports a silent file when a track's sample/plugin is missing: keep the stem null so the
            # builder plays the part's MIDI through a sample instead
            key = n if n in parts else (n[len(title):] if n.startswith(title) and n[len(title):] in parts else None)
            report.append((f, "silent export ignored (peak < -60 dBFS)" + (f"; part '{parts[key]['name']}' keeps no stem" if key else "")))
            if key and parts[key].get("stem"): parts[key]["stem"] = None
            if key: matched.add(key)
            continue
        if "fullmix" in n or n == title or n.endswith("mix") and title in n:
            song["mix"] = _place(song_dir, export_dir, f, "mix", copy); report.append((f, "full mix")); continue
        key = n if n in parts else (n[len(title):] if n.startswith(title) and n[len(title):] in parts else None)
        if key is None:
            close = difflib.get_close_matches(n, parts.keys(), n=1, cutoff=0.88)
            key = close[0] if close else None
        if key and key not in matched:
            p = parts[key]; matched.add(key)
            p["stem"] = _place(song_dir, export_dir, f, p["id"], copy)
            report.append((f, f"→ part '{p['name']}'"))
        else:
            extra.append(f)
    extra = _resolve_duplicates(song, song_dir, export_dir, extra, parts, matched, report, copy)
    ids = {p["id"] for p in song["parts"]}
    for f in extra:
        pid = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(f)[0].lower()).strip("-")
        while pid in ids: pid += "-x"
        ids.add(pid)
        song["parts"].append({"id": pid, "name": os.path.splitext(f)[0], "kind": "audio", "instrument": {"type": "audio"},
                              "stem": _place(song_dir, export_dir, f, pid, copy), "raw_stem": None,
                              "default_treatment": "backing-audio", "origin": "export-only (aux/FX return or unmatched track)"})
        report.append((f, f"→ NEW part '{pid}' (backing-audio): no Logic track matched; check it"))
    for p in song["parts"]:
        if p["kind"] == "midi" and not p.get("stem") and p["instrument"].get("type") != "analoglab":
            report.append((p["name"], "⚠ no exported stem: Logic built-in, can only be played via a rebuilt instrument"))
    tmp = path + ".tmp"; json.dump(song, open(tmp, "w"), indent=2); os.replace(tmp, path)
    return report


def _place(song_dir, export_dir, f, pid, copy):
    rel = f"stems/export/{pid}{os.path.splitext(f)[1].lower()}"
    dst = os.path.join(song_dir, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
    (shutil.copy2 if copy else os.symlink)(os.path.abspath(os.path.join(export_dir, f)), dst) if not os.path.exists(dst) else None
    return rel


def _bars_active(wav, tempo, bpb, n_bars):
    from .ingest import _read_wav
    import numpy as np
    a, sr = _read_wav(wav)
    spb = int(sr * 60 / tempo * bpb)
    return {b for b in range(n_bars) if a[b * spb:(b + 1) * spb].size and float(np.abs(a[b * spb:(b + 1) * spb]).max()) > 1e-3}


def _bars_with_notes(song_dir, part, bpb):
    notes = json.load(open(os.path.join(song_dir, part["midi"])))["notes"] if part.get("midi") else []
    return {int(st // bpb) for _, st, d, _ in notes} | {int((st + d) // bpb) for _, st, d, _ in notes}


def _resolve_duplicates(song, song_dir, export_dir, extra, parts, matched, report, copy):
    """Logic names exports after the track's display name, which can repeat (e.g. two tracks both showing their
    'Light Harmonics' patch -> 'Light Harmonics.wav' + 'Light Harmonics_1.wav'). For each such group, re-decide which
    file belongs to which candidate part by comparing where the audio is active with where the part has notes."""
    bpb = song["time_signature"][0] * 4 / song["time_signature"][1]
    n_bars = int(song["length_beats"] // bpb) + 2
    left = []
    groups = {}
    for f in extra:
        m = re.match(r"(.*)_\d+$", os.path.splitext(f)[0])
        (groups.setdefault(norm(m.group(1)), []) if m else left).append(f)
    for base, files in groups.items():
        cands = [p for p in song["parts"] if p["kind"] == "midi" and (norm(p["name"]) == base or
                 norm(re.sub(r"\.\w+$", "", (p["instrument"].get("preset") or ""))) == base)]
        named = [f2 for f2 in os.listdir(export_dir) if norm(f2) == base]
        files = files + named
        if len(cands) < 2:
            left += [f for f in files if f not in named]; continue
        act = {f: _bars_active(os.path.join(export_dir, f), song["tempo"], bpb, n_bars) for f in files}
        notes = {p["id"]: _bars_with_notes(song_dir, p, bpb) for p in cands}
        score = lambda f, p: len(act[f] & notes[p["id"]]) / max(1, len(act[f] | notes[p["id"]]))
        pairs = sorted(((score(f, p), f, p["id"]) for f in files for p in cands), reverse=True)
        used_f, used_p = set(), set()
        for sc, f, pid in pairs:
            if f in used_f or pid in used_p: continue
            used_f.add(f); used_p.add(pid)
            p = next(x for x in song["parts"] if x["id"] == pid)
            dst = os.path.join(song_dir, "stems", "export", f"{pid}.wav")
            if os.path.exists(dst): os.remove(dst)
            p["stem"] = _place(song_dir, export_dir, f, pid, copy); matched.add(norm(p["name"]))
            report.append((f, f"→ part '{p['name']}' (duplicate name resolved by activity, overlap {sc:.2f})"))
        left += [f for f in files if f not in used_f and f not in named]
    return left
