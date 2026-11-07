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
    for f in files:
        n = norm(f)
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
