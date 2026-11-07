#!/usr/bin/env python3
"""Suggest Analog Lab V presets for a Logic built-in preset / track name.

usage: preset_suggest.py "Light Harmonics" [--type pad|lead|bass|keys|...] [--top 10]

Steps: (1) look the Logic preset up in Logic's Alchemy tag DB / Sculpture+ES2+Patch
file names to get category + descriptive tags; (2) translate that vocabulary to
Analog Lab's (type, styles, characteristics, genres); (3) score every AL preset
on type/subtype match, tag overlap and keyword hits in name/comment.
"""
import argparse, json, re, shutil, sqlite3, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "out" / "analoglab_index.json"
HOME = Path.home()
ALCHEMY_DB = HOME / "Music/Audio Music Apps/Plug-In Settings/Alchemy/Alchemy_Preset_Ratings_And_Tags.db"
LIB = Path("/Applications/Logic Pro.app/Contents/Resources/Library.bundle")
USER_LIB = Path("/Library/Application Support/Logic")

# Logic category / folder words -> Analog Lab Type
CAT2TYPE = {
    "pad": "Pad", "pads": "Pad", "lead": "Lead", "leads": "Lead", "bass": "Bass",
    "key": "Keys", "keys": "Keys", "keyboard": "Keys", "mallet": "Keys", "mallets": "Keys", "bell": "Keys", "bells": "Keys",
    "organ": "Organ", "organs": "Organ", "string": "Strings", "strings": "Strings",
    "brass": "Brass & Winds", "woodwinds": "Brass & Winds", "blown": "Brass & Winds", "flute": "Brass & Winds",
    "vocal": "Vocal", "vocals": "Vocal", "voice": "Vocal", "flutes": "Brass & Winds", "choir": "Vocal",
    "drum": "Drums", "drums": "Drums", "percussion": "Drums",
    "sfx": "Sound Effects", "effects": "Sound Effects", "soundscape": "Sound Effects", "soundscapes": "Sound Effects",
    "arpeggiated": "Sequence", "arp": "Sequence", "rhythmic": "Sequence", "sequence": "Sequence",
    "piano": "Piano", "ep": "Electric Piano",
}
# Logic descriptive tag -> Analog Lab tag words (styles / characteristics / genres)
TAG_MAP = {
    "bright": ["Bright"], "dark": ["Dark"], "warm": ["Warm"], "thin": ["Thin"], "phat": ["Huge"],
    "nasty": ["Dirty", "Harsh"], "smooth": ["Soft", "Mellow"], "glitchy": ["Glitch"],
    "breathy": ["Airy"], "cold": ["Thin", "Clean"], "nice": ["Melodic"], "organic": ["Natural", "Acoustic"],
    "metallic": ["Sharp", "FM"], "noisy": ["Noise"], "detuned": ["Ensemble"], "mysterious": ["Atmospheric"],
    "ambient": ["Ambient", "Atmospheric"], "soundtrack": ["Cinematic", "Soundtrack"],
    "morphing": ["Evolving"], "evolving": ["Evolving"], "additive": ["Additive"], "sustained": ["Long"],
    "slow attack": ["Slow Attack"], "fast attack": ["Short"], "distorted": ["Distorted"], "clean": ["Clean"],
    "downtempo": ["Downtempo"], "electronica": ["IDM", "Electro"], "funk": ["Funk"], "cinematic": ["Cinematic"],
    "arpeggiated": ["Arpeggiated", "Sequence/Loop"], "poly": [], "synths": ["Synth"],
}

def tokens(s):
    return [t for t in re.findall(r"[a-z0-9&/]+", s.lower()) if len(t) > 1]

def logic_lookup(q):
    """Return dict(type=..., tags=[...], source=...) for a Logic preset name, best effort."""
    out = {"type": None, "tags": [], "source": "none", "match": None}
    ql = q.lower()
    # 1. Alchemy tag DB (458 items, ~10k attribute rows)
    if ALCHEMY_DB.exists():
        tmp = Path(tempfile.mkdtemp()); shutil.copy(ALCHEMY_DB, tmp / "a.db")
        con = sqlite3.connect(tmp / "a.db")
        row = con.execute("select item_id,item_name from items where lower(item_name)=? limit 1", (ql,)).fetchone() \
            or con.execute("select item_id,item_name from items where lower(item_name) like ? limit 1", (f"%{ql}%",)).fetchone()
        if row:
            attrs = con.execute("""select at.attribute_type_name,a.attribute_name from attributes_map m
                join attributes a using(attribute_id) join attribute_types at using(attribute_type_id)
                where m.item_id=?""", (row[0],)).fetchall()
            cat = [n for t, n in attrs if t == "Category"]
            if cat:
                out["type"] = CAT2TYPE.get(cat[0].lower())
            out["tags"] = [n for t, n in attrs if t in ("Timbre", "Genre", "Articulation", "Subcategory")]
            out.update(source="Alchemy tag DB", match=row[1])
        shutil.rmtree(tmp, ignore_errors=True)
        if row: return out
    # 2. Sculpture / ES2 / Quick Sampler / Patch file names: folder name gives category
    for base in (LIB / "Plug-In Settings", USER_LIB / "Plug-In Settings", LIB / "Patches", Path.home() / "Music/Audio Music Apps/Patches"):
        if not base.exists(): continue
        for f in base.rglob("*"):
            if f.is_file() and ql in f.stem.lower():
                for part in reversed(f.relative_to(base).parts[:-1]):
                    for w in tokens(re.sub(r"^\d+\s*", "", part)):
                        if w in CAT2TYPE:
                            out.update(type=CAT2TYPE[w], source=f"file {f.relative_to(base)}", match=f.stem); return out
    return out

def score(p, qtoks, want_type, tagwords, secondary=()):
    s = 0.0; why = []
    name = p["name"].lower(); comment = p["comment"].lower()
    if want_type:
        if p["type"] == want_type: s += 4; why.append(f"type={want_type}")
        elif p["type"] not in secondary: s -= 3
    if p["type"] in secondary: s += 4; why.append(f"type2={p['type']}")
    ntoks = tokens(name)
    for t in qtoks:
        if t in ntoks or any(n.startswith(t) or t.startswith(n) and len(n) > 3 for n in ntoks): s += 3; why.append(f"name:{t}")
        elif t in comment: s += 1; why.append(f"desc:{t}")
        if p["subtype"] and t in p["subtype"].lower(): s += 1.5; why.append(f"sub:{t}")
    pool = {x.lower() for x in p["characteristics"] + p["styles"] + p["genres"]}
    for w in tagwords:
        if w.lower() in pool: s += 1.2; why.append(f"tag:{w}")
    s += (p.get("rating") or 0) * 0.05
    return s, why

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query"); ap.add_argument("--type"); ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--instrument", help="restrict to AL instrument, e.g. 'Analog Lab V' (multi presets) or 'Mini V4'")
    a = ap.parse_args()
    if not INDEX.exists():
        sys.exit("run spikes/preset_index.py first")
    idx = [p for p in json.loads(INDEX.read_text()) if not p["hidden"] and p["type"] not in ("Template", "Standard")]
    lg = logic_lookup(a.query)
    want = CAT2TYPE.get(a.type.lower()) if a.type else lg["type"]
    qtoks = [t for t in tokens(a.query) if t not in ("the", "of", "a", "and", "synth", "becomes", "become", "is", "to", "in")]
    # generic words that are really categories, not keywords
    if not want:
        for t in qtoks:
            if t in CAT2TYPE: want = CAT2TYPE[t]; break
    secondary = {CAT2TYPE[t] for t in qtoks if t in CAT2TYPE and CAT2TYPE[t] != want}
    tagwords = [w for t in lg["tags"] for w in TAG_MAP.get(t.lower(), [])]
    # Alchemy tag vocab words also appear verbatim in AL tags
    tagwords += [t for t in lg["tags"] if t.lower() not in TAG_MAP]
    tagwords = list(dict.fromkeys(tagwords))
    print(f"query={a.query!r}  logic={lg['source']}({lg['match']})  type={want}  logic_tags={lg['tags']}  keywords={qtoks}")
    scored = []
    for p in idx:
        if a.instrument and p["instrument"] != a.instrument: continue
        s, why = score(p, qtoks, want, tagwords, secondary)
        scored.append((s, p, why))
    scored.sort(key=lambda x: -x[0])
    for s, p, why in scored[: a.top]:
        print(f"{s:5.1f}  {p['name']:<28} [{p['type']}/{p['subtype']}] {p['instrument']} / {p['pack']}  <{', '.join(why[:6])}>")

if __name__ == "__main__":
    main()
