"""Smoke test: ingest the sample project and build the default lineup; the set must validate.
Run: .venv/bin/python -m unittest discover tests"""
import gzip, os, tempfile, unittest, xml.etree.ElementTree as ET
from lls.ingest import ingest
from lls.build import build
from lls import als
from tests.paths import SAMPLE, HAVE_SAMPLE, HAVE_LIVE


@unittest.skipUnless(HAVE_SAMPLE, "sample project not present (samples/ or $LLS_SAMPLE_PROJECT)")
@unittest.skipUnless(HAVE_LIVE, "needs Ableton Live 12")
class Smoke(unittest.TestCase):
    def test_ingest_and_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            song = ingest(SAMPLE, os.path.join(tmp, "song"))
            self.assertEqual(song["tempo"], 100.0)
            self.assertGreater(len(song["sections"]), 4)
            presets = {p["instrument"].get("preset") for p in song["parts"] if p["instrument"].get("type") == "analoglab"}
            self.assertEqual(presets, {"Blade Of Canada", "Da Fonk", "Desynk", "New Vision sw26"})
            out, report = build(os.path.join(tmp, "song"), out_root=os.path.join(tmp, "sets"))
            root = ET.fromstring(gzip.open(out).read())
            self.assertEqual(als.validate(root), [])
            self.assertEqual(len(root.find("LiveSet/Scenes")), len(song["sections"]) + 1)

if __name__ == "__main__":
    unittest.main()
