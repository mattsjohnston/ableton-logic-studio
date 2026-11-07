import json, os, shutil, sys, tempfile, unittest

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from lls import oneshot as O
from lls.ingest import _read_wav, write_wav

SONG_DIR = os.path.join(ROOT, "songs", "thoughts-for-the-night")
SR, TEMPO = 44100, 120.0  # 0.5 s per beat


def _tone(freq, dur, amp):
    t = np.arange(int(dur * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * 20)).astype(np.float32)


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "stems")); os.makedirs(os.path.join(self.d, "midi"))
        self.notes = [[36, 0, .25, 60], [36, 1, .25, 120], [36, 1.25, .25, 90], [36, 4, .25, 100], [40, 6, .25, 127]]
        self._midi(self.notes)
        buf = np.zeros((SR * 6, 2), np.float32)
        for beat, amp in [(0, .2), (1, .5), (1.25, .5), (4, .4)]:
            i = int(beat * .5 * SR); tone = _tone(100, 0.4, amp); buf[i:i + len(tone)] += tone[:, None]
        write_wav(os.path.join(self.d, "stems", "kick.wav"), buf, SR)
        self.part = {"id": "kick", "name": "Kick 1 - X", "stem": "stems/kick.wav", "midi": "midi/kick.json"}
        self.song = {"tempo": TEMPO}

    def _midi(self, notes): json.dump({"notes": notes}, open(os.path.join(self.d, "midi", "kick.json"), "w"))

    def tearDown(self): shutil.rmtree(self.d)

    def test_picks_roomy_loud_hit(self):
        path, m = O.extract(self.d, self.part, self.song)
        self.assertEqual(m["source_beat"], 4)          # main pitch, upper-velocity half, longest gap
        self.assertEqual(m["velocity"], 100)
        self.assertTrue(path.endswith("stems/oneshots/kick.wav"))
        a, sr = _read_wav(path)
        self.assertEqual(a.shape[1], 2)
        self.assertLess(abs(a[:int(.0015 * SR)]).max(), 1e-3)                  # 2 ms pre-roll is silent
        self.assertGreater(abs(a[int(.004 * SR):int(.01 * SR)]).max(), .1)     # onset right after
        self.assertTrue(250 < m["length_ms"] < 400)                            # tail below -60 dB trimmed
        self.assertAlmostEqual(m["peak_dbfs"], 20 * np.log10(.4), delta=0.5)
        self.assertAlmostEqual(float(abs(a[-1]).max()), 0, places=3)           # faded out

    def test_length_limited_by_next_note(self):
        self._midi([[36, 1, .25, 120]])                # single hit: full tail
        _, full = O.extract(self.d, self.part, self.song)
        note, nxt = O.pick_hit([[36, 1, .25, 120], [36, 1.25, .25, 10]], 2.4)
        self.assertEqual((note[1], nxt), (1, 1.25))    # the only candidate has a 0.25-beat gap
        self._midi([[36, 1, .25, 120], [36, 1.25, .25, 10]])
        _, cut = O.extract(self.d, self.part, self.song)
        self.assertLess(cut["length_ms"], 130)         # 0.25 beat = 125 ms
        self.assertGreater(full["length_ms"], 250)

    def test_missing(self):
        self.assertIsNone(O.extract(self.d, {**self.part, "stem": None}, self.song))
        self.assertIsNone(O.extract(self.d, {**self.part, "stem": "stems/nope.wav"}, self.song))
        self._midi([])
        self.assertIsNone(O.extract(self.d, self.part, self.song))

    def test_max_len(self):
        self.assertEqual(O.max_len_for({"id": "x", "name": "Crash - H"}), 3.0)
        self.assertEqual(O.max_len_for({"id": "x", "name": "Hi-Hat 1"}), 0.5)
        self.assertEqual(O.max_len_for({"id": "x", "name": "FX 2"}), 2.0)


@unittest.skipUnless(os.path.isdir(os.path.join(SONG_DIR, "stems", "export")), "real song stems not present")
class RealSong(unittest.TestCase):
    def test_all(self):
        song = json.load(open(os.path.join(SONG_DIR, "song.json")))
        out = tempfile.mkdtemp()
        try:
            ok = [(p, r) for p, r in O.extract_all(SONG_DIR, song, out_dir=out) if r]
            self.assertGreaterEqual(len(ok), 5)
            for p, (path, m) in ok:
                a, sr = _read_wav(path)
                self.assertEqual(a.shape[1], 2)
                self.assertLessEqual(len(a) / sr, O.max_len_for(p) + 0.01)
                self.assertGreater(m["peak_dbfs"], -60)
        finally:
            shutil.rmtree(out)


if __name__ == "__main__":
    unittest.main()
