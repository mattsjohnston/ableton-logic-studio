"""Run from repo root: .venv/bin/python -m unittest discover -s tests -t ."""
import os, shutil, tempfile, unittest, wave
import numpy as np
from lls import fallback as fb
from lls.ingest import _read_wav
from tests.synth import make_song, SR, TEMPO

COUNT_IN = int(2 * 4 * 60 / TEMPO * SR)


class Fallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir, self.song = make_song(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_fb(self, **kw):
        return fb.fallback(self.dir, "default", os.path.join(self.tmp, "out"), "01 Tiny", **kw)

    def test_excludes_live_and_lists_missing(self):
        r = self.run_fb(m4a=False)
        self.assertEqual([i["part"] for i in r["included"]], ["pad"])
        self.assertEqual([m["part"] for m in r["missing"]], ["bass"])

    def test_count_in_length_peak_and_bitdepth(self):
        r = self.run_fb(m4a=False)
        wav = [f for f in r["files"] if f.endswith("01 Tiny.wav")][0]
        a, sr = _read_wav(wav)
        self.assertEqual(sr, SR)
        self.assertGreaterEqual(len(a), COUNT_IN + int(16 * 60 / TEMPO * SR))
        self.assertAlmostEqual(20 * np.log10(np.abs(a).max()), -1.0, delta=0.05)
        self.assertEqual(wave.open(wav).getsampwidth(), 3)
        self.assertGreater(np.abs(a[:200]).max(), 0.1)  # count-in click at the very start

    def test_click_left_variant(self):
        r = self.run_fb(m4a=False)
        wav = [f for f in r["files"] if f.endswith("click-left.wav")][0]
        a, _ = _read_wav(wav)
        self.assertEqual(np.abs(a[:COUNT_IN, 1]).max(), 0.0)  # count-in on the left only
        self.assertGreater(np.abs(a[:COUNT_IN, 0]).max(), 0.1)
        self.assertGreater(np.abs(a[COUNT_IN:, 1]).max(), 0.1)  # music on the right

    def test_section_override_changes_inclusion(self):
        plan = fb.resolve_lineup(self.song, {}, "default")
        plan["pad"]["s2"]["treatment"] = "live-midi"  # chorus played live -> pad absent from fallback chorus
        mix, sr, inc, _ = fb.render_fallback(self.song, self.dir, plan)
        half = len(mix) // 2
        self.assertGreater(np.abs(mix[:half - 10]).max(), 0.1)
        self.assertEqual(np.abs(mix[half + 10:]).max(), 0.0)

    @unittest.skipUnless(shutil.which("afconvert"), "afconvert not available")
    def test_m4a(self):
        r = self.run_fb(m4a=True)
        m = [f for f in r["files"] if f.endswith(".m4a")]
        self.assertEqual(len(m), 2)
        self.assertGreater(os.path.getsize(m[0]), 100)


if __name__ == "__main__":
    unittest.main()
