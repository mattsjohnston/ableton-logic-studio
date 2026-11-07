import gzip, json, os, shutil, sys, tempfile, unittest, warnings
import xml.etree.ElementTree as ET

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from lls import als, drumrack as dr

SONG_DIR = os.path.join(ROOT, "songs", "thoughts-for-the-night")
HAVE_LIVE = os.path.exists(als.DEMO)
MAIN = os.environ.get("LLS_MAIN", "/Users/mattjohnston/Dev/Ableton Logic Studio")
LOGICX = f"{MAIN}/samples/Thoughts For The Night.logicx"


class Classify(unittest.TestCase):
    def test_names(self):
        for name, note in [("Kick 1 - Flashback", 36), ("Snare 2 - Deep End", 38), ("Hi-Hat 1 - LowTech Funk", 42),
                           ("Hi-Hat Open - X", 46), ("Tom Low - Flashback", 41), ("Crash - Hollywood Haze", 49),
                           ("Shaker 1 - Flashback", 70), ("FX 2 - Studio Cuts", 39)]:
            self.assertEqual(dr.classify(name), note, name)
        self.assertEqual(dr.classify({"id": "x", "name": "Bass Line"}), None)

    def test_unique_assignment(self):
        parts = [{"id": "k1", "name": "Kick 1"}, {"id": "k2", "name": "Kick 2"}, {"id": "s", "name": "Snare"}]
        gm = dr.assign_notes(parts)
        self.assertEqual(gm, {"k1": 36, "k2": 35, "s": 38})

    def test_match_sample(self):
        files = ["/x/Flashback/Kick 1 - Flashback.aif", "/x/Deep End Kit/TinySnare_DeepEnd.aif", "/x/Flashback/Kick 2 - Flashback.aif"]
        self.assertTrue(dr.match_sample("Kick 2 - Flashback", files).endswith("Kick 2 - Flashback.aif"))
        self.assertTrue(dr.match_sample("Snare 2 - Deep End", files).endswith("TinySnare_DeepEnd.aif"))

    def test_remap(self):
        self.assertEqual(dr.remap_notes([(59, 0, 0.5, 100)], 36), [(36, 0, 0.5, 100)])


@unittest.skipUnless(HAVE_LIVE and os.path.isdir(LOGICX), "needs Live 12 app and the sample Logic project")
class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.song = json.load(open(f"{SONG_DIR}/song.json"))
        cls.song["quicksampler_files"] = dr.quicksampler_files(LOGICX)
        cls.P = als.Protos()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_make_drum_track(self):
        ids = ["kick-1-flashback", "kick-2-flashback", "tom-low-flashback", "snare-2-deep-end"]
        info = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t, gm = dr.make_drum_track(self.P, ids, self.song, SONG_DIR, project_dir=self.tmp, info=info)
        ET.fromstring(ET.tostring(t))  # serialises
        self.assertEqual(gm, {"kick-1-flashback": 36, "kick-2-flashback": 35, "tom-low-flashback": 41, "snare-2-deep-end": 38})
        pads = {128 - int(b.find("BranchInfo/ReceivingNote").get("Value")): b for b in t.iter("DrumBranch")}
        self.assertEqual(sorted(pads), [35, 36, 38, 41])
        for n in (35, 36, 41):
            sims = pads[n].findall(".//OriginalSimpler")
            self.assertEqual(len(sims), 1)
            self.assertEqual(sims[0].find(".//RootKey").get("Value"), "60")
            self.assertEqual(pads[n].find("BranchInfo/SendingNote").get("Value"), "60")
            rel = sims[0].find(".//SampleRef/FileRef/RelativePath").get("Value")
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, rel)), rel)
        self.assertEqual(pads[38].findall(".//OriginalSimpler"), [])  # sample not installed -> empty pad
        self.assertTrue(any("Snare 2" in m for m in info))

    def test_simpler_root(self):
        smp = dr.sample_for_part(next(p for p in self.song["parts"] if p["id"] == "kick-1-flashback"), self.song)
        t = dr.make_simpler_track(self.P, smp, self.tmp, "K", 3, root=59)
        s = t.find(".//OriginalSimpler")
        self.assertEqual(s.find(".//RootKey").get("Value"), "59")
        self.assertEqual(s.find("Globals/PlaybackMode").get("Value"), "1")

    def test_demo_validates(self):
        sys.path.insert(0, os.path.join(ROOT, "spikes"))
        import drumrack_demo
        cwd = os.getcwd(); os.chdir(ROOT)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out, gm, info = drumrack_demo.build(out=os.path.join(self.tmp, "Demo Project", "Demo.als"))
        finally:
            os.chdir(cwd)
        root = ET.fromstring(gzip.open(out).read())
        self.assertEqual(als.validate(root), [])
        self.assertEqual(len(list(root.iter("DrumGroupDevice"))), 1)
        self.assertEqual(len(info), 2)


if __name__ == "__main__":
    unittest.main()
