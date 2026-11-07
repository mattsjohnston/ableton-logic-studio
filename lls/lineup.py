"""Lineups: who plays which part, how, in which section. Contract: docs/model.md.

API (other modules code against these signatures):
  load_band(path)                         -> band dict
  resolve(song, band, lineup_id)          -> {part_id: {section_id: {"treatment", "performer", "rig"}}}
  validate(song, band, lineup_id)         -> [{"level": "error"|"warn", "part", "section", "message", "code"}]
  suggest(song, band, performer_ids)      -> {"name", "performers", "assignments"}
  role_hint(part)                         -> "vocal"|"drums"|"bass"|"lead"|"pad"|"fx"|"keys"|None

A song may carry "_dir" (set by load_song/build); validate then also checks that stem files exist.
"""
import json, os, re

LIVE = {"live-vocal", "live-midi", "live-looped", "live-drums", "live-guitar"}
TREATMENTS = LIVE | {"backing-audio", "audio-loop", "midi-clip", "click", "dropped"}
KEYS_LIVE = {"live-midi", "live-looped"}
RIG_FIT = {"live-vocal": {"vocal"}, "live-guitar": {"guitar", "bass"}, "live-midi": {"keys", "grid"},
           "live-looped": {"keys", "grid"}, "live-drums": {"drums", "grid"}}
EPS = 1e-3  # beats; notes touching end-to-start do not overlap

DRUM_NAMES = re.compile(r"kick|snare|hi-?hat|\bhat\b|\btom\b|tom-|crash|ride|cymbal|shaker|clap|perc|drum|\bkit\b|tamb", re.I)
FX_NAMES = re.compile(r"\bfx\b|fx-|cuts|riser|impact|sweep|noise|textur|whoosh|downlifter", re.I)
VOCAL_NAMES = re.compile(r"vocal|vox|golden era plate|phone filter|choir|\bbv\b", re.I)
PAD_NAMES = re.compile(r"pad|backing|harmonic|string|chord|atmos|choir", re.I)
LEAD_NAMES = re.compile(r"lead|line|flute|solo|arp|melody", re.I)


def load_band(path="band.json"):
    band = json.load(open(path))
    if band.get("schema") != 1: raise ValueError(f"{path}: unsupported band schema {band.get('schema')}")
    return band


def load_song(song_dir):
    song = json.load(open(os.path.join(song_dir, "song.json")))
    song["_dir"] = os.path.abspath(song_dir)
    return song


def save_lineup(song_dir, lineup_id, lineup):
    p = os.path.join(song_dir, "song.json")
    song = json.load(open(p))
    song.setdefault("lineups", {})[lineup_id] = lineup
    json.dump(song, open(p, "w"), indent=2)


# ---------- part facts ----------

def role_hint(part):
    if part.get("role_hint"): return part["role_hint"]
    name, inst = part.get("name", ""), part.get("instrument") or {}
    if part.get("kind") == "audio":
        if VOCAL_NAMES.search(name): return "vocal"
        return "fx" if FX_NAMES.search(name) else None
    if "bass" in name.lower(): return "bass"
    if FX_NAMES.search(name): return "fx"
    if DRUM_NAMES.search(name) or inst.get("name") == "Quick Sampler" or inst.get("type") == "drumrack": return "drums"
    if PAD_NAMES.search(name): return "pad"
    if LEAD_NAMES.search(name): return "lead"
    return "keys" if inst.get("type") == "analoglab" else None


def is_drum(part):
    return part.get("kind") == "midi" and role_hint(part) == "drums"


def playable(part):
    """An instrument Live can load: Analog Lab (AU + injected state) or a Drum Rack."""
    return (part.get("instrument") or {}).get("type") in ("analoglab", "drumrack")


def drum_rack_source(part):
    t = part.get("instrument") or {}
    return t.get("type") == "drumrack" or t.get("name") == "Quick Sampler"


def has_stem(song, part):
    stem = part.get("stem") or part.get("raw_stem")
    if not stem: return False
    return not song.get("_dir") or os.path.exists(os.path.join(song["_dir"], stem))


def _notes(song, part):
    cache = song.setdefault("_notes", {})
    if part["id"] not in cache:
        notes = []
        if part.get("kind") == "midi" and part.get("midi") and song.get("_dir"):
            p = os.path.join(song["_dir"], part["midi"])
            if os.path.exists(p): notes = json.load(open(p))["notes"]
        elif "notes_data" in part:  # inline notes (tests, UI previews)
            notes = part["notes_data"]
        cache[part["id"]] = notes
    return cache[part["id"]]


def intervals(song, part, sec):
    """Merged [start, end) spans (beats) where the part sounds inside a section, from MIDI note times."""
    spans = sorted((max(st, sec["start"]), min(st + d, sec["end"])) for _, st, d, _ in _notes(song, part)
                   if st < sec["end"] and st + d > sec["start"])
    out = []
    for a, b in spans:
        if out and a < out[-1][1] - EPS: out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out


def _overlap(a, b):
    i = j = 0
    while i < len(a) and j < len(b):
        if min(a[i][1], b[j][1]) - max(a[i][0], b[j][0]) > EPS: return True
        if a[i][1] < b[j][1]: i += 1
        else: j += 1
    return False


def _max_simultaneous(span_lists):
    ev = sorted([(a, 1) for s in span_lists for a, _ in s] + [(b - EPS, -1) for s in span_lists for _, b in s],
                key=lambda e: (e[0], e[1]))
    cur = best = 0
    for _, d in ev:
        cur += d; best = max(best, cur)
    return best


def _inst_key(part):
    i = part.get("instrument") or {}
    return (i.get("type"), i.get("name"), i.get("preset"), i.get("state"))


# ---------- band ----------

def _performers(band):
    return {p["id"]: p for p in band.get("performers", [])}


def _rig(band, performer, rig_id):
    p = _performers(band).get(performer)
    if not p: return None
    return next((r for r in p.get("rigs", []) if r["id"] == rig_id), None)


# ---------- resolve ----------

def _lineup(song, lineup_id):
    lineups = song.get("lineups", {})
    if lineup_id in lineups: return lineups[lineup_id]
    if lineup_id == "default": return {}
    raise KeyError(f"song '{song.get('title')}' has no lineup '{lineup_id}' (has: {', '.join(lineups) or 'none'})")


def resolve(song, band, lineup_id):
    lu = _lineup(song, lineup_id)
    assigns = dict(lu.get("assignments", {}))
    for pid, t in lu.get("treatments", {}).items():  # Phase-1 lineups: {part: treatment}
        assigns.setdefault(pid, {"treatment": t})
    out = {}
    for part in song["parts"]:
        a = assigns.get(part["id"]) or {}
        base = {"treatment": a.get("treatment") or part["default_treatment"],
                "performer": a.get("performer"), "rig": a.get("rig")}
        per = {}
        for sec in song["sections"]:
            o = (a.get("sections") or {}).get(sec["id"])
            r = dict(base)
            if isinstance(o, str): r["treatment"] = o
            elif isinstance(o, dict): r.update({k: v for k, v in o.items() if k in r})
            if r["treatment"] not in LIVE: r["performer"] = r["rig"] = None
            per[sec["id"]] = r
        out[part["id"]] = per
    return out


# ---------- validate ----------

def validate(song, band, lineup_id):
    res = resolve(song, band, lineup_id)
    lu = _lineup(song, lineup_id)
    perfs = _performers(band)
    in_lineup = set(lu.get("performers") or [])
    parts = {p["id"]: p for p in song["parts"]}
    out, seen = [], set()

    def add(level, code, msg, part=None, section=None):
        k = (level, code, part, section, msg)
        if k not in seen:
            seen.add(k); out.append({"level": level, "part": part, "section": section, "message": msg, "code": code})

    for pid in lu.get("assignments", {}):
        if pid not in parts: add("warn", "unknown-part", f"lineup assigns unknown part '{pid}'", pid)

    for pid, per in res.items():
        part = parts[pid]
        for sid, r in per.items():
            t, who, rig_id = r["treatment"], r["performer"], r["rig"]
            if t not in TREATMENTS:
                add("error", "bad-treatment", f"unknown treatment '{t}'", pid, sid); continue
            if t in LIVE:
                if not who or not rig_id:
                    add("error", "no-performer", f"{t} needs a performer and a rig", pid, sid); continue
                if who not in perfs:
                    add("error", "no-performer", f"performer '{who}' is not in band.json", pid, sid); continue
                if in_lineup and who not in in_lineup:
                    add("warn", "not-in-lineup", f"performer '{who}' is not listed in this lineup's performers", pid, sid)
                rig = _rig(band, who, rig_id)
                if not rig:
                    add("error", "no-rig", f"performer '{who}' has no rig '{rig_id}'", pid, sid); continue
                if rig.get("kind") not in RIG_FIT[t]:
                    add("error", "rig-kind", f"{t} can't be played on a {rig.get('kind')} rig ({rig_id}); needs {'/'.join(sorted(RIG_FIT[t]))}", pid, sid)
                if t in ("live-vocal", "live-guitar") and rig.get("audio_input") is None:
                    add("error", "no-input", f"rig {rig_id} has no audio_input", pid, sid)
                if t in ("live-midi", "live-looped") and not playable(part):
                    add("error", "unplayable", f"{t} needs a playable instrument; {_inst_name(part)} is Logic-only", pid, sid)
                if t == "live-drums" and not drum_rack_source(part):
                    add("error", "unplayable", f"live-drums needs a Drum Rack source (Quick Sampler samples); {_inst_name(part)} has none", pid, sid)
                rng = rig.get("range")
                if rng and part.get("kind") == "midi":
                    sec = _section(song, sid)
                    bad = [n[0] for n in _notes(song, part) if sec["start"] <= n[1] < sec["end"] and not rng[0] <= n[0] <= rng[1]]
                    if bad: add("warn", "range", f"{len(bad)} notes outside {rig_id}'s range {rng[0]}-{rng[1]}", pid, sid)
            elif t in ("backing-audio", "audio-loop"):
                if not has_stem(song, part):
                    add("error", "no-stem", f"{t} needs a stem; export '{part['name']}' from Logic", pid, sid)
            elif t == "midi-clip":
                if part.get("kind") != "midi":
                    add("error", "no-midi", "midi-clip on an audio part", pid, sid)
                elif not playable(part):
                    add("error", "unplayable", f"midi-clip needs a playable instrument; {_inst_name(part)} is Logic-only", pid, sid)
            elif t == "dropped":
                add("warn", "dropped", f"'{part['name']}' is dropped", pid, sid)

    # per section: same performer+rig overlaps, and hands per performer
    for sec in song["sections"]:
        sid = sec["id"]
        by_rig, by_perf = {}, {}
        for pid, per in res.items():
            r = per[sid]
            if r["treatment"] not in LIVE or not r["performer"] or not r["rig"]: continue
            by_rig.setdefault((r["performer"], r["rig"]), []).append((pid, r["treatment"]))
            if r["treatment"] in KEYS_LIVE:
                by_perf.setdefault(r["performer"], []).append(pid)
        for (who, rig_id), lst in by_rig.items():
            midi = [(p, t) for p, t in lst if t != "live-drums" and parts[p].get("kind") == "midi"]
            for i in range(len(midi)):
                for j in range(i + 1, len(midi)):
                    a, b = parts[midi[i][0]], parts[midi[j][0]]
                    if _inst_key(a) == _inst_key(b): continue
                    ia, ib = intervals(song, a, sec), intervals(song, b, sec)
                    if _overlap(ia, ib):
                        add("error", "overlap", f"'{a['name']}' and '{b['name']}' sound at the same time on {who}/{rig_id} "
                            "with different instruments (needs a chain switch)", a["id"], sid)
                    elif ia and ib:
                        add("warn", "switch", f"'{a['name']}' and '{b['name']}' both play on {who}/{rig_id} in this section "
                            "(not at once): both sounds answer the rig for the whole section", a["id"], sid)
            # audio parts (several vocal takes on one mic) are not overlap-checked: no note times, by design
        for who, pids in by_perf.items():
            if len(pids) > 2 and _max_simultaneous([intervals(song, parts[p], sec) for p in pids]) > 2:
                add("warn", "hands", f"{who} has more than 2 live keys parts sounding at once ({', '.join(pids)})", None, sid)

    # two performers' rigs on the same MIDI port + channel (only rigs this lineup plays live)
    used = {(r["performer"], r["rig"]) for per in res.values() for r in per.values()
            if r["treatment"] in LIVE and r["performer"] and r["rig"]}
    midi_rigs = [(w, _rig(band, w, rid)) for w, rid in sorted(used) if _rig(band, w, rid) and _rig(band, w, rid).get("kind") != "vocal"
                 and "audio_input" not in _rig(band, w, rid)]
    for i in range(len(midi_rigs)):
        for j in range(i + 1, len(midi_rigs)):
            (wa, ra), (wb, rb) = midi_rigs[i], midi_rigs[j]
            if wa == wb: continue
            pa, pb, ca, cb = ra.get("midi_port"), rb.get("midi_port"), ra.get("midi_channel"), rb.get("midi_channel")
            if (pa is None or pb is None or pa == pb) and (ca is None or cb is None or ca == cb):
                add("error", "port-clash", f"{wa}/{ra['id']} and {wb}/{rb['id']} share MIDI port "
                    f"{pa or 'All Ins'}/{pb or 'All Ins'} + channel {ca or 'all'}/{cb or 'all'}: both would play each other's parts")
    return out


def _inst_name(part):
    i = part.get("instrument") or {}
    return i.get("name") or i.get("type") or "no instrument"


def _section(song, sid):
    return next(s for s in song["sections"] if s["id"] == sid)


# ---------- suggest ----------

def suggest(song, band, performer_ids):
    perfs = _performers(band)
    unknown = [p for p in performer_ids if p not in perfs]
    if unknown: raise ValueError(f"unknown performers: {', '.join(unknown)}")
    chosen = [perfs[p] for p in performer_ids]
    rigs = lambda kinds: [(p["id"], r) for p in chosen for r in p.get("rigs", []) if r.get("kind") in kinds]
    vocal_rigs, drum_rigs = rigs({"vocal"}), rigs({"drums"})
    bass_rigs, keys_rigs = rigs({"bass"}), rigs({"keys", "grid"})
    secs = song["sections"]
    assignments = {}

    def fallback(part):
        if part.get("kind") == "midi" and playable(part): return {"treatment": "midi-clip"}
        if has_stem(song, part) or part.get("kind") == "audio": return {"treatment": "backing-audio"}
        return {"treatment": "midi-clip" if playable(part) else "backing-audio"}

    # keys state: per (performer, rig) -> {sid: [(part, spans)]}; load per performer
    sounding = {}
    load = {p["id"]: 0.0 for p in chosen}
    singers = {w for w, _ in vocal_rigs}

    def density(part):
        act = sum(sum(b - a for a, b in intervals(song, part, s)) for s in secs)
        return len(_notes(song, part)) / max(act, 1.0)

    def try_keys(part):
        """Live on the least-loaded keys rig; conflicting sections fall back to midi-clip. None if not worth it."""
        active = [s for s in secs if intervals(song, part, s)]
        if not active: return None
        best = None
        for order, (who, rig) in enumerate(sorted(keys_rigs, key=lambda wr: (load[wr[0]] + (0.5 if wr[0] in singers else 0),
                                                                             performer_ids.index(wr[0])))):
            conflicts = []
            for s in active:
                mine = intervals(song, part, s)
                same_rig = sounding.get((who, rig["id"]), {}).get(s["id"], [])
                # stricter than validate: one sound per rig per section (no mid-section sound switch)
                if any(_inst_key(op) != _inst_key(part) for op, sp in same_rig):
                    conflicts.append(s["id"]); continue
                perf_spans = [sp for (w, _), d in sounding.items() if w == who for _, sp in d.get(s["id"], [])]
                if _max_simultaneous(perf_spans + [mine]) > 2: conflicts.append(s["id"])
            if best is None or len(conflicts) < len(best[2]): best = (who, rig, conflicts)
            if not conflicts: break
        who, rig, conflicts = best
        if len(conflicts) * 2 > len(active): return None
        for s in active:
            if s["id"] not in conflicts:
                sounding.setdefault((who, rig["id"]), {}).setdefault(s["id"], []).append((part, intervals(song, part, s)))
        load[who] += density(part) * (len(active) - len(conflicts)) / len(active)
        a = {"treatment": "live-midi", "performer": who, "rig": rig["id"]}
        if conflicts: a["sections"] = {sid: "midi-clip" for sid in conflicts}
        return a

    parts = song["parts"]
    roles = {p["id"]: role_hint(p) for p in parts}
    # 1. vocals: lead vocals to the first singer
    for part in parts:
        if part.get("kind") == "audio" and roles[part["id"]] == "vocal" and part["default_treatment"] == "live-vocal":
            if vocal_rigs:
                w, r = vocal_rigs[0]
                assignments[part["id"]] = {"treatment": "live-vocal", "performer": w, "rig": r["id"]}
            else:
                assignments[part["id"]] = {"treatment": "backing-audio"}
    # 2. drums: one live-drums group on the drummer's rig
    for part in parts:
        if is_drum(part) and part["id"] not in assignments:
            if drum_rigs and drum_rack_source(part):
                w, r = drum_rigs[0]
                assignments[part["id"]] = {"treatment": "live-drums", "performer": w, "rig": r["id"]}
            else:
                assignments[part["id"]] = fallback(part)
    # 3. bass: real bass player, else keys
    bass_parts = [p for p in parts if roles[p["id"]] == "bass" and p["id"] not in assignments]
    for part in bass_parts:
        if bass_rigs:
            w, r = bass_rigs[0]
            assignments[part["id"]] = {"treatment": "live-guitar", "performer": w, "rig": r["id"]}
        elif keys_rigs and part.get("kind") == "midi" and playable(part):
            assignments[part["id"]] = try_keys(part) or fallback(part)
        else:
            assignments[part["id"]] = fallback(part)
    # 4. playable keys parts (Analog Lab leads/pads), densest first
    keys_parts = [p for p in parts if p["id"] not in assignments and p.get("kind") == "midi" and playable(p)]
    for part in sorted(keys_parts, key=lambda p: (-density(p), p["id"])):
        assignments[part["id"]] = (try_keys(part) if keys_rigs else None) or fallback(part)
    # 5. everything else plays back
    for part in parts:
        if part["id"] not in assignments:
            assignments[part["id"]] = fallback(part) if part.get("kind") == "midi" else {"treatment": part["default_treatment"]
                                      if part["default_treatment"] not in LIVE else "backing-audio"}
    names = [perfs[p]["name"] for p in performer_ids]
    return {"name": f"{len(names)}-piece ({', '.join(names)})", "performers": list(performer_ids),
            "assignments": {p["id"]: assignments[p["id"]] for p in parts}}
