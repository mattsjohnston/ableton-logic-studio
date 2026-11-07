"""Tiny synthetic song folder for setlist/fallback tests."""
import json, os
import numpy as np
from lls.ingest import write_wav

SR = 8000  # tiny stems; the builder/fallback only need consistent rates
TEMPO = 120.0  # 2 beats/s


def tone(sec, freq, amp=0.5):
    t = np.arange(int(sec * SR)) / SR
    x = (np.sin(2 * np.pi * freq * t) * amp).astype(np.float32)
    return np.stack([x, x], axis=1)


def make_song(root, slug="tiny-song", title="Tiny Song"):
    d = os.path.join(root, "songs", slug)
    os.makedirs(os.path.join(d, "stems", "raw"))
    os.makedirs(os.path.join(d, "midi"))
    write_wav(os.path.join(d, "stems/raw/pad.wav"), tone(8.5, 220), SR)
    write_wav(os.path.join(d, "stems/raw/vox.wav"), tone(8.5, 880), SR)
    json.dump({"notes": [[60, 0.0, 1.0, 100], [64, 8.0, 1.0, 100]]}, open(os.path.join(d, "midi/bass.json"), "w"))

    def part(i, kind, stem, tr):
        p = {"id": i, "name": i.title(), "kind": kind, "instrument": {"type": "audio" if kind == "audio" else "other", "name": "X"},
             "stem": None, "raw_stem": stem, "default_treatment": tr}
        if kind == "midi": p["midi"] = f"midi/{i}.json"
        return p

    song = {"schema": 1, "title": title, "tempo": TEMPO, "time_signature": [4, 4], "sample_rate": SR, "length_beats": 16.0,
            "sections": [{"id": "s1", "name": "Verse", "start": 0.0, "end": 8.0}, {"id": "s2", "name": "Chorus", "start": 8.0, "end": 16.0}],
            "parts": [part("pad", "audio", "stems/raw/pad.wav", "backing-audio"),
                      part("vox", "audio", "stems/raw/vox.wav", "live-vocal"),
                      part("bass", "midi", None, "backing-audio")],
            "ignored_tracks": [], "warnings": [], "performers": [],
            "lineups": {"default": {"description": "", "treatments": {}}}}
    json.dump(song, open(os.path.join(d, "song.json"), "w"))
    return d, song
