"""Propose song sections from where parts start and stop (synthwave is built in 4/8-bar blocks)."""
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
