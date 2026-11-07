"""Printed one-shots: one clean hit per drum part, cut from its exported stem (so it carries Logic's channel strip).

extract() -> (path, meta) in <song_dir>/stems/oneshots/<part_id>.wav, or None when the part has no stem / notes.
"""
import json, os
from collections import Counter

import numpy as np

from .ingest import _read_wav, write_wav

PRE_S = 0.002           # cut starts this long before the note
FADE_S = 0.005
SILENCE_DB = -60.0
MAX_LEN = (("crash", 3.0), ("kick", 1.2), ("snare", 1.2), ("tom", 1.2), ("hat", 0.5), ("shaker", 0.5), ("fx", 2.0))
DEFAULT_MAX = 1.2


def max_len_for(part):
    n = (part.get("name") or part["id"]).lower()
    for key, v in MAX_LEN:
        if key in n: return v
    return DEFAULT_MAX


def pick_hit(notes, max_len_beats):
    """Choose [pitch, start, dur, vel] from a part's notes: main pitch, upper-velocity half, longest gap to the next
    own note (gap capped at max_len, so all roomy hits tie and the loudest wins). Returns (note, next_start|None)."""
    if not notes: return None
    main = Counter(n[0] for n in notes).most_common(1)[0][0]
    starts = sorted({round(n[1], 4) for n in notes})

    def nxt(s):
        return next((t for t in starts if t > s + 1e-3), None)
    cands = [n for n in notes if n[0] == main]
    cut = sorted(n[3] for n in cands)[len(cands) // 2]
    loud = [n for n in cands if n[3] >= cut]

    def score(n):
        nx = nxt(n[1])
        gap = max_len_beats if nx is None else min(nx - n[1], max_len_beats)
        return (round(gap, 3), n[3], -n[1])
    best = max(loud, key=score)
    return best, nxt(best[1])


def extract(song_dir, part, song, out_dir=None):
    """Write the part's one-shot; returns (path, meta) or None (no stem file / no notes / silent)."""
    stem, midi = part.get("stem"), part.get("midi")
    if not stem or not midi: return None
    sp, mp = os.path.join(song_dir, stem), os.path.join(song_dir, midi)
    if not (os.path.isfile(sp) and os.path.isfile(mp)): return None
    notes = json.load(open(mp)).get("notes") or []
    spb = 60.0 / float(song["tempo"])
    picked = pick_hit(notes, max_len_for(part) / spb)
    if not picked: return None
    note, nxt = picked
    a, sr = _read_wav(sp)  # float32 stereo (24-bit precision survives float32)
    t0 = note[1] * spb
    end_s = t0 + max_len_for(part)
    if nxt is not None: end_s = min(end_s, nxt * spb)
    i0 = max(0, int(round((t0 - PRE_S) * sr))); i1 = min(len(a), int(round(end_s * sr)))
    seg = a[i0:i1].copy()
    if len(seg) == 0: return None
    loud = np.nonzero(np.abs(seg).max(axis=1) > 10 ** (SILENCE_DB / 20))[0]
    if len(loud) == 0: return None
    seg = seg[: loud[-1] + 1]
    nf = min(len(seg), int(FADE_S * sr))
    if nf: seg[-nf:] *= np.linspace(1, 0, nf, dtype=np.float32)[:, None]
    peak = float(np.abs(seg).max())
    path = os.path.join(out_dir or os.path.join(song_dir, "stems", "oneshots"), f"{part['id']}.wav")
    write_wav(path, seg, sr)
    meta = {"path": path, "pitch": note[0], "source_beat": note[1], "source_sec": t0, "velocity": note[3],
            "length_ms": len(seg) / sr * 1000, "peak_dbfs": float(20 * np.log10(peak)) if peak > 0 else -120.0,
            "sample_rate": sr}
    return path, meta


def bar_beat(beat, bpb=4.0):
    return f"{int(beat // bpb) + 1}.{beat % bpb + 1:.2f}"


def extract_all(song_dir, song, out_dir=None):
    """[(part, result|None)] for every part drumrack.category() recognises as a drum and that has a midi file."""
    from .drumrack import category
    return [(p, extract(song_dir, p, song, out_dir)) for p in song["parts"] if p.get("midi") and category(p)]
