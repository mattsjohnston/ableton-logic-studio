"""lls.lineup: resolve / validate / suggest, plus build() honouring a lineup.
Run: .venv/bin/python -m unittest discover tests"""
import copy, gzip, json, os, tempfile, unittest, wave, xml.etree.ElementTree as ET

import numpy as np

from lls import als, lineup as L

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = next((p for p in (os.path.join(REPO, "samples", "Thoughts For The Night.logicx"),
                           "/Users/mattjohnston/Dev/Ableton Logic Studio/samples/Thoughts For The Night.logicx")
               if os.path.isdir(p)), None)

AL = lambda preset: {"type": "analoglab", "preset": preset, "state": f"instruments/{preset}.aupreset"}
ALCHEMY = {"type": "logic-builtin", "name": "Alchemy", "preset": None}
QS = {"type": "logic-builtin", "name": "Quick Sampler", "preset": None}


def band():
    return {"schema": 1, "interface": {"inputs": {"vocal-1": 1}}, "performers": [
        {"id": "ann", "name": "Ann", "roles": ["vocal", "keys"], "rigs": [
            {"id": "ann-keys", "kind": "keys", "midi_port": "Impulse (MIDI)", "midi_channel": 1},
            {"id": "ann-keys2", "kind": "keys", "midi_port": "Launchkey", "midi_channel": 1},
            {"id": "ann-keys3", "kind": "keys", "midi_port": "Pads", "midi_channel": 1},
            {"id": "ann-vox", "kind": "vocal", "audio_input": 1}]},
        {"id": "bob", "name": "Bob", "roles": ["keys"], "rigs": [
            {"id": "bob-keys", "kind": "keys", "midi_port": None, "midi_channel": 2}]},
        {"id": "cal", "name": "Cal", "roles": ["keys"], "rigs": [
            {"id": "cal-keys", "kind": "keys", "midi_port": None, "midi_channel": 2}]},
        {"id": "dee", "name": "Dee", "roles": ["drums"], "rigs": [
            {"id": "dee-kit", "kind": "drums", "midi_port": None, "midi_channel": 10}]}]}


def midi_part(pid, inst, notes, treatment="midi-clip", **kw):
    return dict(id=pid, name=pid.title(), kind="midi", instrument=inst, default_treatment=treatment,
                stem=None, notes_data=notes, **kw)


def song(parts, lineups=None):
    return {"title": "T", "tempo": 120.0, "sections": [{"id": "s1", "name": "A", "start": 0, "end": 16},
                                                       {"id": "s2", "name": "B", "start": 16, "end": 32}],
            "parts": parts, "lineups": lineups or {}}


def codes(problems, level=None):
    return {(p["code"], p["part"], p["section"]) for p in problems if level is None or p["level"] == level}


class Resolve(unittest.TestCase):
    def setUp(self):
        self.s = song([midi_part("lead", AL("X"), [[60, 0, 4, 100]]),
                       midi_part("pad", AL("Y"), [[60, 20, 4, 100]]),
                       dict(id="vox", name="Vox", kind="audio", instrument={"type": "audio"}, default_treatment="live-vocal",
                            stem=None, raw_stem="stems/raw/vox.wav")],
                      {"duo": {"performers": ["ann", "bob"], "assignments": {
                          "lead": {"treatment": "live-midi", "performer": "ann", "rig": "ann-keys", "sections": {"s2": "midi-clip"}},
                          "pad": {"treatment": "live-midi", "performer": "bob", "rig": "bob-keys",
                                  "sections": {"s1": {"performer": "ann", "rig": "ann-keys2"}}}}},
                       "old": {"treatments": {"lead": "backing-audio"}}})

    def test_defaults(self):
        r = L.resolve(self.s, band(), "duo")
        self.assertEqual(r["vox"]["s1"], {"treatment": "live-vocal", "performer": None, "rig": None})
        self.assertEqual(set(r), {"lead", "pad", "vox"})
        self.assertEqual(L.resolve(self.s, band(), "default")["lead"]["s2"]["treatment"], "midi-clip")

    def test_overrides(self):
        r = L.resolve(self.s, band(), "duo")
        self.assertEqual(r["lead"]["s1"], {"treatment": "live-midi", "performer": "ann", "rig": "ann-keys"})
        self.assertEqual(r["lead"]["s2"], {"treatment": "midi-clip", "performer": None, "rig": None})
        self.assertEqual(r["pad"]["s1"], {"treatment": "live-midi", "performer": "ann", "rig": "ann-keys2"})
        self.assertEqual(r["pad"]["s2"]["rig"], "bob-keys")

    def test_phase1_treatments_and_unknown(self):
        self.assertEqual(L.resolve(self.s, band(), "old")["lead"]["s1"]["treatment"], "backing-audio")
        with self.assertRaises(KeyError): L.resolve(self.s, band(), "nope")


class Validate(unittest.TestCase):
    def check(self, parts, assignments, performers=("ann", "bob", "cal", "dee"), b=None):
        s = song(parts, {"x": {"performers": list(performers), "assignments": assignments}})
        return L.validate(s, b or band(), "x")

    def test_clean(self):
        p = self.check([midi_part("lead", AL("X"), [[60, 0, 4, 100]])],
                       {"lead": {"treatment": "live-midi", "performer": "ann", "rig": "ann-keys"}})
        self.assertEqual([x for x in p if x["level"] == "error"], [])

    def test_missing_performer_and_rig_kind(self):
        p = self.check([midi_part("lead", AL("X"), [[60, 0, 4, 100]]),
                        dict(id="vox", name="Vox", kind="audio", instrument={"type": "audio"}, default_treatment="backing-audio",
                             stem=None, raw_stem="x.wav")],
                       {"lead": {"treatment": "live-midi"},
                        "vox": {"treatment": "live-vocal", "performer": "ann", "rig": "ann-keys"}})
        c = codes(p, "error")
        self.assertIn(("no-performer", "lead", "s1"), c)
        self.assertIn(("rig-kind", "vox", "s1"), c)

    def test_missing_stem_and_unplayable(self):
        p = self.check([midi_part("alc", ALCHEMY, [[60, 0, 4, 100]], treatment="backing-audio"),
                        midi_part("alc2", ALCHEMY, [[60, 0, 4, 100]]),
                        midi_part("alc3", ALCHEMY, [[60, 0, 4, 100]]),
                        midi_part("snare", ALCHEMY, [[38, 0, 1, 100]])],
                       {"alc3": {"treatment": "live-midi", "performer": "ann", "rig": "ann-keys"},
                        "snare": {"treatment": "live-drums", "performer": "dee", "rig": "dee-kit"}})
        c = codes(p, "error")
        self.assertIn(("no-stem", "alc", "s1"), c)
        self.assertIn(("unplayable", "alc2", "s1"), c)
        self.assertIn(("unplayable", "alc3", "s2"), c)
        self.assertIn(("unplayable", "snare", "s1"), c)

    def test_overlap_same_rig(self):
        live = lambda r="ann-keys": {"treatment": "live-midi", "performer": "ann", "rig": r}
        p = self.check([midi_part("a", AL("X"), [[60, 0, 8, 100], [60, 20, 2, 100]]),
                        midi_part("b", AL("Y"), [[64, 4, 8, 100], [64, 24, 2, 100]]),
                        midi_part("c", AL("X"), [[67, 0, 8, 100]])],  # same instrument as a: allowed
                       {"a": live(), "b": live(), "c": live()})
        c = codes(p)
        self.assertIn(("overlap", "a", "s1"), c)            # 0-8 vs 4-12 in s1
        self.assertNotIn(("overlap", "a", "s2"), c)         # 20-22 vs 24-26: same section, not at once
        self.assertIn(("switch", "a", "s2"), codes(p, "warn"))
        self.assertFalse(any(x[0] == "overlap" and x[1] == "c" for x in c))
        # different rigs: no overlap error
        p2 = self.check([midi_part("a", AL("X"), [[60, 0, 8, 100]]), midi_part("b", AL("Y"), [[64, 4, 8, 100]])],
                        {"a": live(), "b": live("ann-keys2")})
        self.assertNotIn("overlap", {x["code"] for x in p2})

    def test_hands(self):
        live = lambda r: {"treatment": "live-midi", "performer": "ann", "rig": r}
        parts = [midi_part(f"p{i}", AL(str(i)), [[60, 0, 8, 100]]) for i in range(3)]
        p = self.check(parts, {"p0": live("ann-keys"), "p1": live("ann-keys2"), "p2": live("ann-keys3")})
        self.assertIn(("hands", None, "s1"), codes(p, "warn"))
        self.assertNotIn(("hands", None, "s2"), codes(p, "warn"))

    def test_shared_port_channel(self):
        live = lambda w: {"treatment": "live-midi", "performer": w, "rig": f"{w}-keys"}
        p = self.check([midi_part("a", AL("X"), [[60, 0, 4, 100]]), midi_part("b", AL("Y"), [[60, 20, 4, 100]])],
                       {"a": live("bob"), "b": live("cal")})
        self.assertIn("port-clash", {x["code"] for x in p if x["level"] == "error"})
        b = band(); b["performers"][2]["rigs"][0]["midi_channel"] = 3
        p = self.check([midi_part("a", AL("X"), [[60, 0, 4, 100]]), midi_part("b", AL("Y"), [[60, 20, 4, 100]])],
                       {"a": live("bob"), "b": live("cal")}, b=b)
        self.assertNotIn("port-clash", {x["code"] for x in p})

    def test_dropped_and_range(self):
        b = band(); b["performers"][0]["rigs"][0]["range"] = [48, 72]
        p = self.check([midi_part("a", AL("X"), [[90, 0, 4, 100]]), midi_part("d", AL("Y"), [], treatment="dropped")],
                       {"a": {"treatment": "live-midi", "performer": "ann", "rig": "ann-keys"}}, b=b)
        w = codes(p, "warn")
        self.assertIn(("range", "a", "s1"), w)
        self.assertIn(("dropped", "d", "s1"), w)


def _write_wav(path, seconds=1.0, sr=8000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    a = (np.random.default_rng(0).standard_normal((int(seconds * sr), 2)) * 0.1 * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(a.tobytes())


@unittest.skipUnless(SAMPLE, "sample project not present")
class SampleSong(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from lls.ingest import ingest
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = os.path.join(cls.tmp.name, "song")
        ingest(SAMPLE, cls.dir)
        # simulate the Logic stem export for parts that only have MIDI (Logic built-ins)
        p = os.path.join(cls.dir, "song.json"); s = json.load(open(p))
        for part in s["parts"]:
            if not part.get("stem") and not part.get("raw_stem"):
                part["stem"] = f"stems/export/{part['id']}.wav"; _write_wav(os.path.join(cls.dir, part["stem"]))
        json.dump(s, open(p, "w"), indent=2)
        cls.band = L.load_band(os.path.join(REPO, "band.json"))
        cls.band5 = copy.deepcopy(cls.band)
        cls.band5["performers"].append({"id": "keys-3", "name": "Keys 3", "roles": ["keys"], "rigs": [
            {"id": "keys-3-keys", "kind": "keys", "midi_port": None, "midi_channel": 3}]})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    COMBOS = {2: ["matt", "drummer"], 3: ["matt", "drummer", "bassist"], 4: ["matt", "keys-2", "drummer", "bassist"],
              5: ["matt", "keys-2", "drummer", "bassist", "keys-3"]}

    def test_suggest_valid_for_2_to_5(self):
        live_counts = []
        for n, ids in self.COMBOS.items():
            b = self.band5 if n == 5 else self.band
            s = L.load_song(self.dir)
            lu = L.suggest(s, b, ids)
            self.assertEqual(lu, L.suggest(L.load_song(self.dir), b, ids), "suggest must be deterministic")
            self.assertEqual(lu["performers"], ids)
            s["lineups"]["t"] = lu
            errors = [p for p in L.validate(s, b, "t") if p["level"] == "error"]
            self.assertEqual(errors, [], f"{n} performers")
            a = lu["assignments"]
            self.assertEqual(set(a), {p["id"] for p in s["parts"]})
            drums = [p["id"] for p in s["parts"] if L.is_drum(p)]
            if "drummer" in ids:
                self.assertTrue(drums and all(a[d] == {"treatment": "live-drums", "performer": "drummer", "rig": "drummer-kit"} for d in drums))
            if "bassist" in ids:
                self.assertEqual(a["bass-line"]["treatment"], "live-guitar")
            self.assertEqual(a["phone-filter-verse"]["performer"], "matt")
            res = L.resolve(s, b, "t")
            live_counts.append(sum(r["treatment"].startswith("live") for per in res.values() for r in per.values()))
        self.assertEqual(live_counts, sorted(live_counts), "more performers must not mean less live")

    def test_build_4_piece(self):
        from lls.build import build
        lu = L.suggest(L.load_song(self.dir), self.band, self.COMBOS[4])
        L.save_lineup(self.dir, "four", lu)
        out, report = build(self.dir, "four", out_root=os.path.join(self.tmp.name, "sets"),
                            band_path=os.path.join(REPO, "band.json"))
        root = ET.fromstring(gzip.open(out).read())
        self.assertEqual(als.validate(root), [])
        tracks = {t.find("Name/EffectiveName").get("Value"): t for t in root.find("LiveSet/Tracks") if t.tag in ("AudioTrack", "MidiTrack")}
        vox = tracks["Vocal (live: Matt)"].find("DeviceChain")
        self.assertEqual(vox.find("AudioInputRouting/Target").get("Value"), "AudioIn/External/M0")
        self.assertEqual(vox.find("MainSequencer/MonitoringEnum").get("Value"), str(als.MONITOR_IN))
        bass = tracks["Bass (live: Bass)"].find("DeviceChain")
        self.assertEqual(bass.find("AudioInputRouting/Target").get("Value"), "AudioIn/External/M1")
        live_midi = [t for t in tracks.values() if t.find("DeviceChain/MidiInputRouting/Target").get("Value").startswith("MidiIn/External.Dev:Impulse (MIDI)/0")]
        self.assertTrue(live_midi)
        drums = tracks["Drums (live: drummer)"].find("DeviceChain/MidiInputRouting")
        self.assertEqual(drums.find("Target").get("Value"), "MidiIn/External.All/9")
        md = open(os.path.join(os.path.dirname(os.path.dirname(out)), "accounted-for.md")).read()
        self.assertIn("## Validation", md)


if __name__ == "__main__":
    unittest.main()
