import json, os, shutil, tempfile, unittest
from lls import setlist
from tests.synth import make_song

SAMPLE = next((p for p in ("samples/Thoughts For The Night.logicx", "/Users/mattjohnston/Dev/Ableton Logic Studio/samples/Thoughts For The Night.logicx") if os.path.isdir(p)), "samples/missing")


class Setlist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        make_song(self.tmp, "tiny-song", "Tiny Song")
        make_song(self.tmp, "second", "Second: Tune")
        json.dump({"schema": 1, "performers": [{"id": "matt", "name": "Matt", "roles": ["vocal"], "rigs": []}]},
                  open(os.path.join(self.tmp, "band.json"), "w"))
        os.makedirs(os.path.join(self.tmp, "setlists"))
        self.sl = os.path.join(self.tmp, "setlists", "show.json")
        json.dump({"name": "show", "band": "band.json", "songs": [
            {"song": "tiny-song", "lineup": "default", "notes": "Open <loud>"},
            {"song": "second"}]}, open(self.sl, "w"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_build(self):
        r = setlist.build_setlist(self.sl, out_root=os.path.join(self.tmp, "out"), m4a=False)
        names = sorted(os.listdir(r["dir"]))
        self.assertIn("01 Tiny Song Project", names)
        self.assertIn("02 Second- Tune Project", names)
        self.assertNotIn(".staging", names)
        self.assertTrue(os.path.isfile(os.path.join(r["dir"], "01 Tiny Song Project", "Tiny Song.als")))
        self.assertTrue(os.path.isfile(os.path.join(r["dir"], "fallback", "01 Tiny Song.wav")))
        self.assertTrue(os.path.isfile(os.path.join(r["dir"], "fallback", "02 Second- Tune click-left.wav")))
        md = open(r["running_order"]).read()
        self.assertIn("## 01. Tiny Song", md)
        self.assertIn("120 BPM", md)
        self.assertIn("| 1 | Verse |", md)
        self.assertIn("| 3 | Chorus |", md)  # 8 beats = 2 bars -> bar 3
        h = open(os.path.join(r["dir"], "running-order.html")).read()
        self.assertIn("Open &lt;loud&gt;", h)
        self.assertIn("@media print", h)
        rep = open(os.path.join(r["dir"], "fallback", "fallback-report.md")).read()
        self.assertIn("Missing from fallback", rep)

    def test_empty_setlist_rejected(self):
        json.dump({"name": "x", "songs": []}, open(self.sl, "w"))
        with self.assertRaises(ValueError): setlist.load(self.sl)

    @unittest.skipUnless(os.path.isdir(SAMPLE), "sample project not present")
    def test_sample_song(self):
        from lls.ingest import ingest
        shutil.rmtree(os.path.join(self.tmp, "songs", "second"))
        ingest(SAMPLE, os.path.join(self.tmp, "songs", "thoughts-for-the-night"))
        json.dump({"name": "s", "songs": [{"song": "thoughts-for-the-night"}]}, open(self.sl, "w"))
        r = setlist.build_setlist(self.sl, out_root=os.path.join(self.tmp, "out"), m4a=False)
        self.assertTrue(r["fallback"][0]["included"])
        self.assertTrue(r["fallback"][0]["missing"])  # MIDI-only parts without stems


if __name__ == "__main__":
    unittest.main()
