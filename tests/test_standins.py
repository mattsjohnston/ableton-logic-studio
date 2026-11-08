import json, os, shutil, sys, tempfile, unittest

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from lls import als, build, drumrack, ingest, lineup as LU, standins, stems

SONG_DIR = os.path.join(ROOT, "songs", "thoughts-for-the-night")
HAVE_CORE = os.path.isdir(standins.CORE_SAMPLES)
HAVE_STEMS = os.path.isdir(os.path.join(SONG_DIR, "stems", "export"))
HAVE_LIVE = os.path.exists(als.BASE)


@unittest.skipUnless(HAVE_CORE, "needs Ableton's Core Library")
class Finder(unittest.TestCase):
    def test_each_category_exists(self):
        for cat in standins.CATEGORIES:
            p = standins.find_standin(cat)
            self.assertTrue(p and os.path.isfile(p), cat)
            drumrack.audio_info(p)  # Simpler-readable

    def test_character(self):
        self.assertIn("Hihat Closed", standins.find_standin("hat_closed"))
        self.assertIn("Hihat Open", standins.find_standin("hat_open"))
        self.assertIn("Snare", standins.find_standin("snare"))

    def test_part_mapping_and_index(self):
        self.assertIn("Hihat Closed", standins.standin_for_part({"id": "h", "name": "Hi-Hat 1 - LowTech Funk"}))
        self.assertIn("Snare", standins.standin_for_part({"id": "s", "name": "Snare 2 - Deep End"}))
        self.assertIsNone(standins.standin_for_part({"id": "b", "name": "Bass Line"}))
        self.assertTrue(os.path.isfile(standins.INDEX_PATH))
        with self.assertRaises(ValueError): standins.find_standin("nope")


class Silence(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp()
    def tearDown(self): shutil.rmtree(self.d)

    def _wav(self, name, amp):
        p = os.path.join(self.d, name)
        buf = np.zeros((4410, 2), np.float32); buf[100] = amp
        ingest.write_wav(p, buf, 44100); return p

    def test_peak_threshold(self):
        quiet, loud = self._wav("q.wav", 10 ** (-103 / 20)), self._wav("l.wav", 0.1)
        self.assertTrue(ingest.is_silent_stem(quiet))
        self.assertFalse(ingest.is_silent_stem(loud))
        self.assertTrue(ingest.is_silent_stem(self._wav("z.wav", 0)))

    def test_has_stem_ignores_silent(self):
        self._wav("q.wav", 1e-5); self._wav("l.wav", 0.1)
        song = {"_dir": self.d}
        self.assertFalse(LU.has_stem(song, {"stem": "q.wav"}))
        self.assertTrue(LU.has_stem(song, {"stem": "l.wav"}))

    def test_link_ignores_silent_export(self):
        song_dir, exp = os.path.join(self.d, "song"), os.path.join(self.d, "exp")
        os.makedirs(song_dir); os.makedirs(exp)
        ingest.write_wav(os.path.join(exp, "Hi-Hat 1.wav"), np.zeros((4410, 2), np.float32), 44100)
        ingest.write_wav(os.path.join(exp, "Kick 1.wav"), np.full((4410, 2), 0.2, np.float32), 44100)
        song = {"title": "T", "time_signature": [4, 4], "tempo": 120, "length_beats": 8, "parts": [
            {"id": "hh", "name": "Hi-Hat 1", "kind": "midi", "instrument": {"type": "logic-builtin"}, "stem": None},
            {"id": "k", "name": "Kick 1", "kind": "midi", "instrument": {"type": "logic-builtin"}, "stem": None}]}
        json.dump(song, open(os.path.join(song_dir, "song.json"), "w"))
        report = stems.link(song_dir, exp)
        out = {p["id"]: p["stem"] for p in json.load(open(os.path.join(song_dir, "song.json")))["parts"]}
        self.assertIsNone(out["hh"]); self.assertTrue(out["k"])
        self.assertTrue(any("silent export ignored" in r[1] for r in report))
        self.assertFalse(os.path.exists(os.path.join(song_dir, "stems", "export", "hh.wav")))


@unittest.skipUnless(HAVE_CORE and HAVE_STEMS and HAVE_LIVE, "needs Core Library, the song's stems and Live's base set")
class RealSong(unittest.TestCase):
    def test_default_lineup_has_hat_and_snare_simplers(self):
        out = tempfile.mkdtemp()
        try:
            cwd = os.getcwd(); os.chdir(ROOT)
            try:
                als_path, report = build.build(SONG_DIR, "default", out_root=out)
            finally:
                os.chdir(cwd)
            root = als.load_gz(als_path)
            self.assertEqual(als.validate(root), [])
            tracks = {t.find("Name/EffectiveName").get("Value"): t for t in root.iter("MidiTrack")}
            song = LU.load_song(SONG_DIR)
            for pid, name in (("snare-2-deep-end", "Snare 2 - Deep End"), ("hi-hat-1-lowtech-funk", "Hi-Hat 1 - LowTech Funk")):
                part = next(p for p in song["parts"] if p["id"] == pid)
                if LU.has_stem(song, part): continue  # stem exists on this machine: normal audio path
                t = tracks.get(name)
                self.assertIsNotNone(t, name)
                self.assertEqual(len(t.findall(".//OriginalSimpler")), 1, name)
            text = open(os.path.join(os.path.dirname(os.path.dirname(als_path)), "accounted-for.md")).read()
            self.assertIn("stand-in sample", text)
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
