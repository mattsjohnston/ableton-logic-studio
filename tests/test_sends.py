"""Logic-style aux reverb returns + sends. Run: .venv/bin/python -m unittest discover tests"""
import gzip, json, os, shutil, tempfile, unittest, xml.etree.ElementTree as ET
from lls import als, sends as SD
from lls.build import build
from tests.synth import make_song


def load(out):
    return ET.fromstring(gzip.open(out).read())


def by_name(root):
    return {t.find("Name/EffectiveName").get("Value"): t for t in root.find("LiveSet/Tracks")}


def send_vals(t):
    return [float(h.find("Send/Manual").get("Value")) for h in t.find("DeviceChain/Mixer/Sends")]


class Sends(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d, self.song = make_song(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def build(self, **opts):
        self.song["options"] = opts
        json.dump(self.song, open(os.path.join(self.d, "song.json"), "w"))
        out, _ = build(self.d, out_root=os.path.join(self.tmp, "sets"))
        return load(out)

    def test_returns_and_sends(self):
        root = self.build()
        self.assertEqual(als.validate(root), [])
        tracks = root.find("LiveSet/Tracks")
        rets = [t for t in tracks if t.tag == "ReturnTrack"]
        names = [t.find("Name/EffectiveName").get("Value") for t in rets]
        self.assertEqual(len(rets), len(SD.REVERBS) + 1)
        self.assertTrue(names[-1].endswith("Delay"))
        for t in tracks:  # one holder per return on every track, local ids 0..n-1
            hs = t.find("DeviceChain/Mixer/Sends")
            self.assertEqual([h.get("Id") for h in hs], [str(i) for i in range(len(rets))], t.tag)
        self.assertEqual(len(root.find("LiveSet/SendsPre")), len(rets))
        pad = next(t for n, t in by_name(root).items() if n.startswith("Pad"))
        self.assertGreater(max(send_vals(pad)), SD.SEND_OFF * 2)
        # reverb parameters follow the Logic IR lengths
        decays = [float(t.find("DeviceChain/DeviceChain/Devices/Reverb/DecayTime/Manual").get("Value")) for t in rets[:4]]
        self.assertEqual(decays, [4400, 1100, 3900, 400])

    def test_option_off_keeps_template(self):
        root = self.build(returns=False)
        self.assertEqual(als.validate(root), [])
        rets = [t for t in root.find("LiveSet/Tracks") if t.tag == "ReturnTrack"]
        self.assertEqual(len(rets), 2)
        for t in root.find("LiveSet/Tracks"):
            self.assertEqual(len(t.find("DeviceChain/Mixer/Sends")), 2)


if __name__ == "__main__":
    unittest.main()
