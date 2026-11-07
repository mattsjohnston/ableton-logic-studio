"""Propose song sections from where parts start and stop (synthwave is built in 4/8-bar blocks)."""
import json
from collections import Counter


def propose(spans, length_beats, beats_per_bar=4, min_gap_bars=4, min_score=4):
    """spans: [(start_beats, end_beats)] of every clip/region. Returns [(start, end)] in beats, bar-aligned."""
    score = Counter()
    for s, e in spans:
        for b in (s, e):
            score[round(b / beats_per_bar)] += 1  # bar index from 0
    last_bar = int(-(-length_beats // beats_per_bar))
    cands = sorted((c for c in score.items() if c[1] >= min_score and 0 < c[0] < last_bar), key=lambda c: -c[1])
    chosen = [0]
    for bar, _ in cands:  # strongest first, keep a minimum distance between boundaries
        if all(abs(bar - c) >= min_gap_bars for c in chosen):
            chosen.append(bar)
    chosen = sorted(chosen) + [last_bar]
    return [(a * beats_per_bar, b * beats_per_bar) for a, b in zip(chosen, chosen[1:])]


KEYWORDS = [("chorus", "Chorus"), ("hook", "Chorus"), ("verse", "Verse"), ("pre", "Pre-Chorus"),
            ("bridge", "Bridge"), ("transition", "Bridge"), ("intro", "Intro"), ("outro", "Outro")]


def suggest_names(song, song_dir):
    """Name sections from which vocal parts sing in them (Logic track names often say 'Chorus'/'Verse'),
    falling back to Intro / Instrumental / Break / Outro by position and density. Returns a list of names."""
    import os, re
    import numpy as np
    from .ingest import _read_wav, VOCAL_HINTS
    tempo = song["tempo"]
    secs = song["sections"]
    def active(part):
        f = part.get("stem") or part.get("raw_stem")
        if not f or not os.path.exists(os.path.join(song_dir, f)): return [False] * len(secs)
        a, sr = _read_wav(os.path.join(song_dir, f))
        out = []
        for s in secs:  # RMS level of the part in each section
            seg = a[int(s["start"] * 60 / tempo * sr):int(s["end"] * 60 / tempo * sr)]
            out.append(float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0)
        return out
    vocals = [p for p in song["parts"] if p["kind"] == "audio" and VOCAL_HINTS.search(p["name"])]
    vact = {p["id"]: active(p) for p in vocals}
    density = []
    for i, s in enumerate(secs):
        n = 0
        for p in song["parts"]:
            if p.get("midi"):
                notes = json.load(open(os.path.join(song_dir, p["midi"])))["notes"]
                n += any(s["start"] <= st < s["end"] for _, st, _, _ in notes)
        density.append(n + sum(vact[v][i] > 0.01 for v in vact))
    labels = []
    for i, s in enumerate(secs):
        singing = sorted((p for p in vocals if vact[p["id"]][i] > 0.01 and not re.search(r"backing|choir|bv", p["name"], re.I)),
                         key=lambda p: -vact[p["id"]][i])
        label = None
        if singing:  # the loudest lead vocal names the section
            label = next((name for kw, name in KEYWORDS if kw in singing[0]["name"].lower()), None)
        if label is None:
            if singing: label = "Verse"
            elif i == 0: label = "Intro"
            elif i == len(secs) - 1: label = "Outro"
            else: label = "Break" if density[i] <= max(3, int(0.3 * max(density))) else "Instrumental"
        labels.append(label)
    names, count, prev = [], {}, None
    for lab in labels:
        if lab == prev: names.append(f"{names[-1].split(' (')[0]} (cont.)")
        else:
            count[lab] = count.get(lab, 0) + 1
            names.append(lab if lab in ("Intro", "Outro") else f"{lab} {count[lab]}")
        prev = lab
    return names
