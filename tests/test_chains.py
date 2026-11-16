"""lls.chains: vocal / bass / guitar input-track effect chains in a built set.
Run: .venv/bin/python -m unittest discover tests"""
import copy, gzip, json, os, tempfile, unittest, wave, xml.etree.ElementTree as ET

import numpy as np

from lls import als, chains
from lls.build import build

from tests.paths import live_path
HAVE_LIVE = bool(live_path(als.demo_path) and live_path(chains.fx_dir))
BAND = {"schema": 1, "interface": {"outputs": {"main": [1, 2], "click": [3, 4]}}, "performers": [
    {"id": "ann", "name": "Ann", "roles": ["vocal"], "rigs": [{"id": "ann-vox", "kind": "vocal", "audio_input": 1}]},
    {"id": "bob", "name": "Bob", "roles": ["bass"], "rigs": [{"id": "bob-di", "kind": "bass", "audio_input": 2}]},
    {"id": "cy", "name": "Cy", "roles": ["guitar"], "rigs": [{"id": "cy-di", "kind": "guitar", "audio_input": 3}]}]}


def _wav(path, sr=8000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    a = (np.random.default_rng(0).standard_normal((sr * 2, 2)) * 0.1 * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(a.tobytes())


def _part(pid, treatment):
    return dict(id=pid, name=pid.title(), kind="audio", instrument={"type": "audio"}, default_treatment=treatment,
                stem=f"stems/{pid}.wav", raw_stem=f"stems/{pid}.wav")


@unittest.skipUnless(HAVE_LIVE, "Ableton Live resources not present")
class Chains(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = os.path.join(cls.tmp.name, "song")
        parts = [_part("vox", "live-vocal"), _part("bass", "live-guitar"), _part("gtr", "live-guitar")]
        for p in parts: _wav(os.path.join(cls.dir, p["stem"]))
        song = {"title": "T", "tempo": 120.0, "sample_rate": 8000, "time_signature": [4, 4], "length_beats": 32,
                "sections": [{"id": "s1", "name": "A", "start": 0, "end": 16}, {"id": "s2", "name": "B", "start": 16, "end": 32}],
                "parts": parts, "ignored_tracks": [],
                "lineups": {"band": {"name": "Band", "performers": ["ann", "bob", "cy"], "assignments": {
                    "vox": {"treatment": "live-vocal", "performer": "ann", "rig": "ann-vox"},
                    "bass": {"treatment": "live-guitar", "performer": "bob", "rig": "bob-di"},
                    "gtr": {"treatment": "live-guitar", "performer": "cy", "rig": "cy-di"}}}}}
        json.dump(song, open(os.path.join(cls.dir, "song.json"), "w"))
        cls.sets = os.path.join(cls.tmp.name, "sets")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _build(self, fx=None):
        band = copy.deepcopy(BAND)
        if fx:
            for per in band["performers"]:
                for r in per["rigs"]: r["fx"] = fx
        bp = os.path.join(self.tmp.name, f"band-{fx}.json"); json.dump(band, open(bp, "w"))
        out, report = build(self.dir, "band", out_root=os.path.join(self.sets, str(fx)), band_path=bp)
        root = ET.fromstring(gzip.open(out).read())
        self.assertEqual(als.validate(root), [])
        tracks = {t.find("Name/EffectiveName").get("Value"): t for t in root.find("LiveSet/Tracks") if t.tag == "AudioTrack"}
        return tracks, report

    @staticmethod
    def devs(t): return [d.tag for d in t.find("DeviceChain/DeviceChain/Devices")]

    def test_default_chains(self):
        tracks, report = self._build()
        vox, bass, gtr = tracks["Vocal (live: Ann)"], tracks["Bass (live: Bob)"], tracks["Guitar (live: Cy)"]
        self.assertEqual(self.devs(vox), ["Eq8", "Compressor2", "Compressor2"])
        self.assertEqual(self.devs(bass), ["Amp", "Cabinet"])
        self.assertEqual(self.devs(gtr), ["Amp", "Cabinet", "Delay"])
        eq = vox.find("DeviceChain/DeviceChain/Devices/Eq8")
        self.assertEqual(eq.find("Bands.0/ParameterA/Freq/Manual").get("Value"), "100")
        self.assertEqual(vox.find("DeviceChain/DeviceChain/Devices/Compressor2[2]/Ratio/Manual").get("Value"), "3")
        self.assertTrue(any("vocal chain: EQ Eight" in n for _, _, n in report))
        # list-local device ids and global pointee ids unique (validate() covers the latter)
        for t in (vox, bass, gtr):
            ids = [d.get("Id") for d in t.find("DeviceChain/DeviceChain/Devices")]
            self.assertEqual(len(ids), len(set(ids)))

    def test_vocal_routing_and_monitoring(self):
        vox = self._build()[0]["Vocal (live: Ann)"]
        dc = vox.find("DeviceChain")
        self.assertEqual(dc.find("MainSequencer/MonitoringEnum").get("Value"), str(als.MONITOR_IN))
        self.assertEqual(dc.find("MainSequencer/Recorder/IsArmed").get("Value"), "false")
        self.assertEqual(dc.find("AudioOutputRouting/Target").get("Value"), "AudioOut/Main")

    def test_fx_none(self):
        tracks, report = self._build("none")
        for name in ("Vocal (live: Ann)", "Bass (live: Bob)", "Guitar (live: Cy)"):
            self.assertEqual(self.devs(tracks[name]), [])
        self.assertTrue(any("fx: none" in n for _, _, n in report))

    def test_bad_fx(self):
        with self.assertRaises(ValueError): self._build("wobble")

    def test_fallback_when_presets_missing(self):
        real = chains.FX  # None: the detected Live's Core Library
        chains.FX = "/nonexistent"
        try:
            self.assertEqual([d.tag for d in chains.vocal_chain(als.Protos())], ["Eq8", "Compressor2", "Compressor2"])
            self.assertEqual([d.tag for d in chains.bass_chain(als.Protos())], ["Saturator"])
        finally:
            chains.FX = real


if __name__ == "__main__":
    unittest.main()
