#!/usr/bin/env python3
"""Build a JSON index of Arturia Analog Lab V presets from its SQLite browser DB.

Source: /Library/Arturia/Presets/db.db3 (read-only; copied with its WAL first so
we see the app's latest state without touching the original).
Output: out/analoglab_index.json  (list of dicts)
"""
import json, shutil, sqlite3, sys, tempfile, os
from pathlib import Path

DB = Path("/Library/Arturia/Presets/db.db3")
OUT = Path(__file__).resolve().parent.parent / "out" / "analoglab_index.json"

QUERY = """
SELECT p.key_id, p.name, k.name, t.name, s.name, i.name, p.comment, p.file_path,
       p.rating, p.favorite, p.hide_in_browser
FROM Preset_Id p
LEFT JOIN Packs k ON k.key_id=p.pack
LEFT JOIN Types_V2 t ON t.key_id=p.type_v2
LEFT JOIN Subtypes_V2 s ON s.key_id=p.subtype_v2
LEFT JOIN Instruments i ON i.key_id=p.instrument_key
"""
TAGQ = """
SELECT pc.preset_key, ct.name, c.name
FROM Preset_Characteristics pc
JOIN Characteristics c ON c.key_id=pc.characteristic_key
JOIN Characteristics_Types ct ON ct.key_id=c.type_key
"""

def build():
    tmp = Path(tempfile.mkdtemp())
    for suf in ("", "-wal", "-shm"):
        src = Path(str(DB) + suf)
        if src.exists():
            shutil.copy(src, tmp / ("db.db3" + suf))
    con = sqlite3.connect(tmp / "db.db3")
    tags = {}
    for pid, group, name in con.execute(TAGQ):
        tags.setdefault(pid, {}).setdefault(group.lower(), []).append(name.strip())
    rows = []
    for pid, name, pack, typ, sub, inst, comment, path, rating, fav, hidden in con.execute(QUERY):
        tg = tags.get(pid, {})
        rows.append(dict(
            id=pid, name=name, pack=pack, type=typ, subtype=sub or None, instrument=inst,
            characteristics=tg.get("characteristics", []), genres=tg.get("genres", []),
            styles=tg.get("styles", []), comment=comment or "", file=path,
            rating=rating, favorite=bool(fav), hidden=bool(hidden)))
    shutil.rmtree(tmp, ignore_errors=True)
    return rows

if __name__ == "__main__":
    rows = build()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} presets -> {OUT}  ({OUT.stat().st_size/1e6:.1f} MB)")
    tagged = sum(1 for r in rows if r["characteristics"] or r["genres"] or r["styles"])
    print(f"with tags: {tagged}; missing file on disk: {sum(1 for r in rows if not os.path.exists(r['file']))}")
