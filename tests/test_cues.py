"""Spoken cues on the click output and scene auto-advance. Run: .venv/bin/python -m unittest discover tests"""
import gzip, json, os, shutil, tempfile, unittest, xml.etree.ElementTree as ET
import numpy as np
from lls import als
from lls.build import build, cues_wav, _speak
from lls.ingest import _read_wav
from tests.synth import make_song, SR, tone
from tests.paths import HAVE_LIVE

SECTIONS = [{"id": "s1", "name": "Verse", "start": 0.0, "end": 8.0},
            {"id": "s2", "name": "Chorus", "start": 8.0, "end": 16.0, "vamp": True},
            {"id": "s3", "name": "Verse two", "start": 16.0, "end": 24.0},
            {"id": "s4", "name": "Outro", "start": 24.0, "end": 32.0, "cue": "Last time"}]
BAR_SEC = 2.0  # 4/4 at 120 bpm


def fake_speak(text, sr):
    return tone(0.5, 600, 0.5)


def load(out):
    return ET.fromstring(gzip.open(out).read())


def track(root, name):
    return next(t for t in root.find("LiveSet/Tracks") if t.find("Name/EffectiveName").get("Value") == name)


@unittest.skipUnless(HAVE_LIVE, "needs Ableton Live 12 (builds copy Live's template)")
class CuesAndFollow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.d, song = make_song(self.tmp)
        song["sections"], song["length_beats"] = SECTIONS, 32.0
        json.dump(song, open(os.path.join(self.d, "song.json"), "w"))
        self.song = song
        self.band = os.path.join(self.tmp, "band.json")
        json.dump({"schema": 1, "interface": {"outputs": {"click": [5, 6]}}, "performers": []}, open(self.band, "w"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def build(self, **kw):
        out, _ = build(self.d, out_root=os.path.join(self.tmp, "sets"), band_path=self.band, **kw)
        return load(out), out

    def set_options(self, opts):
        self.song["options"] = opts
        json.dump(self.song, open(os.path.join(self.d, "song.json"), "w"))

    def test_cues_wav_energy_only_at_cues(self):
        p = os.path.join(self.tmp, "Cues.wav")
        placed = cues_wav(p, self.song, speak=fake_speak)
        a, sr = _read_wav(p)
        self.assertEqual(sr, SR)
        self.assertGreaterEqual(len(a), 32 * 0.5 * SR)  # full length from bar 1
        self.assertEqual([i for i, _, _ in placed], [1, 2, 3])  # no cue before the first section
        mask = np.zeros(len(a), bool)
        for i, start, end in placed:
            sec_start = SECTIONS[i]["start"] * 0.5
            self.assertAlmostEqual(start, sec_start - BAR_SEC)  # downbeat of the bar before the section
            self.assertLessEqual(end, sec_start + 1e-6)  # finishes before the section starts
            mask[int(start * SR):int(end * SR)] = True
        self.assertGreater(np.abs(a[mask]).max(), 0.1)
        self.assertLess(np.abs(a[~mask]).max(), 1e-4)

    @unittest.skipUnless(shutil.which("say") and shutil.which("afconvert"), "needs macOS say/afconvert")
    def test_real_speech(self):
        a = _speak("Chorus", 44100)
        self.assertGreater(len(a), 4000)
        self.assertGreater(np.abs(a).max(), 0.05)

    def test_cues_track_routed_to_click_output(self):
        import lls.build as B
        orig = B._speak
        B._speak = fake_speak
        try:
            root, _ = self.build(cues=True)
        finally:
            B._speak = orig
        cues, click = track(root, "Cues"), track(root, "Click")
        for t in (cues, click):
            r = t.find("DeviceChain/AudioOutputRouting")
            self.assertEqual(r.find("Target").get("Value"), "AudioOut/External/S2")
            self.assertEqual(r.find("LowerDisplayString").get("Value"), "5/6")
        slots = cues.find("DeviceChain/MainSequencer/ClipSlotList")
        filled = [i for i, s in enumerate(slots) if s.find("ClipSlot/Value/AudioClip") is not None]
        self.assertEqual(filled, [0, 1, 2])  # window containing each next section's cue; last section has none
        self.assertEqual(als.validate(root), [])

    def test_cues_optional_and_song_option(self):
        root, _ = self.build()
        names = [t.find("Name/EffectiveName").get("Value") for t in root.find("LiveSet/Tracks")]
        self.assertNotIn("Cues", names)
        import lls.build as B
        orig, B._speak = B._speak, fake_speak
        try:
            self.set_options({"cues": True})
            root, _ = self.build()
        finally:
            B._speak = orig
        names = [t.find("Name/EffectiveName").get("Value") for t in root.find("LiveSet/Tracks")]
        self.assertIn("Cues", names)

    def test_auto_advance_non_vamp_scenes_only(self):
        def enabled(root):
            return [sc.find("FollowAction/FollowActionEnabled").get("Value") for sc in root.find("LiveSet/Scenes")]
        root, _ = self.build()
        self.assertEqual(enabled(root), ["false"] * 5)
        root, _ = self.build(auto_advance=True)
        # s1, s3, s4 advance; s2 is a vamp; STOP never
        self.assertEqual(enabled(root), ["true", "false", "true", "true", "false"])
        sc = root.find("LiveSet/Scenes")[0]
        self.assertEqual(sc.find("FollowAction/FollowActionA").get("Value"), "4")
        self.assertEqual(float(sc.find("FollowAction/FollowTime").get("Value")), 8.0)
        self.assertEqual(als.validate(root), [])
        self.set_options({"auto_advance": True})
        root, _ = self.build()
        self.assertEqual(enabled(root), ["true", "false", "true", "true", "false"])


if __name__ == "__main__":
    unittest.main()
