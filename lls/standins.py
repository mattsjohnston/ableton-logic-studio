"""Stand-in drum samples from Ableton's own Core Library, for Logic Quick Sampler samples that are not installed here.

find_standin(category) -> path of a Core Library one-shot (existing file) or None.
Categories: kick, snare, clap, hat_closed, hat_open, tom, crash, ride, shaker, perc, fx.
The Samples tree of the detected Live (lls.liveapp) is indexed once per tree into out/core_samples_index-<hash>.json
(override the tree with $LLS_CORE_SAMPLES).
Picks favour 80s / electronic character (synthwave): per-category regex preferences, then shorter name.
"""
import hashlib, json, os, re

from . import liveapp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_SAMPLES = None   # override (tests); default: <detected Live's Core Library>/Samples
CATEGORIES = ("kick", "snare", "clap", "hat_closed", "hat_open", "tom", "crash", "ride", "shaker", "perc", "fx")
AUDIO = (".wav", ".aif", ".aiff")

# folder under One Shots/Drums -> category (hats are split by name, cymbals keep only crashes)
_FOLDERS = {"Kick": "kick", "Snare": "snare", "Clap": "clap", "Tom": "tom", "Ride": "ride", "Shaker": "shaker",
            "Tambourine": "perc", "Electronic Percussion": "perc", "Misc Percussion": "perc", "Bongo": "perc",
            "Conga": "perc", "Timbales": "perc", "Rim": "perc", "FX Hit": "fx"}
# regexes on the lowercase name, best first; the first one a file matches is its rank
PREFS = {
    "kick": [r"80s|linn|dmx", r"909", r"808", r"."],
    "snare": [r"80s (uk|space)", r"80s", r"linn|dmx", r"909 (hard|dmx)", r"909", r"808"],
    "clap": [r"80s", r"linn|dmx", r"909", r"808"],
    "hat_closed": [r"80s", r"linn|dmx", r"909", r"808"],
    "hat_open": [r"80s", r"linn|dmx", r"909", r"808"],
    "tom": [r"80s|linn|dmx", r"808|909", r"707|505"],
    "crash": [r"80s|linn|dmx", r"909", r"808|707"],
    "ride": [r"80s|linn|dmx", r"909", r"707|505"],
    "shaker": [r"80s|linn|dmx", r"808|maracas"],
    "perc": [r"80s|linn|dmx", r"808|909|707|505"],
    "fx": [r"impact|zap|stab", r"."],
}
_AVOID = re.compile(r"reverse|\brev\b|loop|\d+ ?bpm|riser|swell")


def _tree():
    """The Core Library Samples folder to use, or None without a Live 12."""
    t = os.environ.get("LLS_CORE_SAMPLES") or CORE_SAMPLES
    if t: return t
    try:
        return os.path.join(liveapp.core_library(), "Samples")
    except liveapp.LiveNotFound:
        return None


def index_path(tree=None):
    """Cache file for a Samples tree: keyed by its path, so two Live installs (Suite + Beta) don't evict each other."""
    tree = tree or _tree() or ""
    return os.path.join(REPO, "out", f"core_samples_index-{hashlib.sha1(tree.encode()).hexdigest()[:10]}.json")


def _classify(rel):
    """Category of a Samples-relative path, or None."""
    parts = rel.split(os.sep)
    if len(parts) < 3 or parts[0] != "One Shots": return None
    name = os.path.basename(rel).lower()
    if parts[1] == "FX": return "fx"
    folder = parts[2] if parts[1] == "Drums" and len(parts) > 3 else None
    if folder is None: return None
    if folder == "Hihat":
        return "hat_open" if "open" in name else ("hat_closed" if "closed" in name else None)
    if folder == "Cymbal": return "crash" if name.startswith("crash") else None
    return _FOLDERS.get(folder)


def build_index(tree=None, path=None):
    """Walk the Samples tree and write {root, files: {category: [relative path, ...]}} to `path`."""
    tree = tree or _tree()
    path = path or index_path(tree)
    files = {c: [] for c in CATEGORIES}
    for dp, _, fns in os.walk(tree):
        for fn in fns:
            if not fn.lower().endswith(AUDIO): continue
            rel = os.path.relpath(os.path.join(dp, fn), tree)
            cat = _classify(rel)
            if cat: files[cat].append(rel)
    for v in files.values(): v.sort()
    idx = {"root": tree, "files": files}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(idx, f, indent=1)
    os.replace(tmp, path)
    return idx


def load_index(path=None):
    """The cached index (built on first use, or when the tree location changed); None without a Core Library."""
    tree = _tree()
    if not tree or not os.path.isdir(tree): return None
    path = path or index_path(tree)
    try:
        with open(path) as f: idx = json.load(f)
        if idx.get("root") == tree: return idx
    except (OSError, ValueError):
        pass
    return build_index(tree, path)


def _rank(rel, cat):
    name = os.path.basename(rel).lower()
    prefs = PREFS.get(cat, [])
    hit = next((i for i, rx in enumerate(prefs) if re.search(rx, name)), len(prefs))
    return (bool(_AVOID.search(name)), hit, len(name), name)


def candidates(category, limit=10):
    """Best-first Core Library paths for a category (existing files only)."""
    if category not in CATEGORIES: raise ValueError(f"unknown stand-in category {category!r}")
    idx = load_index()
    if not idx: return []
    rels = sorted(idx["files"].get(category, []), key=lambda r: _rank(r, category))
    out = [os.path.join(idx["root"], r) for r in rels]
    return [p for p in out if os.path.isfile(p)][:limit]


_picked = {}


def find_standin(category):
    """Path of the preferred Core Library sample for `category` that Simpler can read, or None."""
    from .drumrack import audio_info
    key = (category, _tree())
    if key not in _picked:
        _picked[key] = None
        for p in candidates(category, 25):
            try: audio_info(p)
            except Exception: continue
            _picked[key] = p; break
    return _picked[key]


# drumrack.category() name -> stand-in category
_FROM_DRUM = {"kick": "kick", "snare": "snare", "rim": "perc", "clap": "clap", "hat": "hat_closed", "open-hat": "hat_open",
              "tom-low": "tom", "tom-mid": "tom", "tom-high": "tom", "crash": "crash", "ride": "ride", "cowbell": "perc",
              "tambourine": "perc", "shaker": "shaker", "conga": "perc", "percussion": "perc", "fx": "fx"}


def standin_for_part(part):
    """Stand-in sample path for a drum part (by name keywords), or None."""
    from .drumrack import category
    cat = _FROM_DRUM.get(category(part))
    return find_standin(cat) if cat else None
