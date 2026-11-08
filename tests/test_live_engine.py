"""Live engine: AbletonOSC client, expected scene/track names, and the /api/live/* endpoints against a fake Live
(a tiny UDP responder speaking the AbletonOSC subset we use, on alternate ports).
Run: python -m unittest tests.test_live_engine"""
import json, os, shutil, socket, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pythonosc.osc_message import OscMessage  # noqa: E402
from pythonosc.osc_message_builder import OscMessageBuilder  # noqa: E402

from lls import als, live_engine as LE, live_osc  # noqa: E402
from lls import lineup as LU  # noqa: E402
from lls.ui import server  # noqa: E402
from tests.synth import make_song  # noqa: E402


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


class FakeLive:
    """Answers AbletonOSC getters from a dict of state and records setters/launches."""
    def __init__(self, reply_port, scenes=(), tracks=()):
        self.reply_port = reply_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.scenes, self.tracks = list(scenes), list(tracks)
        self.tempo, self.playing, self.slot, self.launched_at = 100.0, False, -1, None
        self.mute, self.solo, self.log = {}, {}, []
        self.loop_start = lambda t, c: 8.0 * c
        self.running = True
        self.th = threading.Thread(target=self.run, daemon=True); self.th.start()

    def close(self):
        self.running = False; self.sock.close()

    def beats(self):
        return 0.0 if not self.playing else (time.time() - self.launched_at) * self.tempo / 60

    def reply(self, addr, *args):
        b = OscMessageBuilder(address=addr)
        for a in args: b.add_arg(a)
        self.sock.sendto(b.build().dgram, ("127.0.0.1", self.reply_port))

    def run(self):
        while self.running:
            try: data, _ = self.sock.recvfrom(65536)
            except OSError: return
            m = OscMessage(data); a, p = m.address, list(m.params)
            self.log.append((a, tuple(p)))
            if a == "/live/test": self.reply(a, "ok")
            elif a == "/live/song/get/tempo": self.reply(a, self.tempo)
            elif a == "/live/song/get/is_playing": self.reply(a, int(self.playing))
            elif a == "/live/song/get/current_song_time": self.reply(a, self.beats())
            elif a == "/live/song/get/scenes/name": self.reply(a, *self.scenes)
            elif a == "/live/song/get/track_names": self.reply(a, *self.tracks)
            elif a == "/live/track/get/playing_slot_index": self.reply(a, p[0], self.slot)
            elif a == "/live/track/get/fired_slot_index": self.reply(a, p[0], -1)
            elif a == "/live/clip/get/loop_start": self.reply(a, p[0], p[1], self.loop_start(p[0], p[1]))
            elif a == "/live/clip/get/playing_position": self.reply(a, p[0], p[1], self.loop_start(p[0], p[1]) + 2.5)
            elif a == "/live/scene/fire": self.playing, self.slot, self.launched_at = True, p[0], time.time()
            elif a == "/live/song/stop_playing": self.playing = False
            elif a == "/live/track/set/mute": self.mute[p[0]] = p[1]
            elif a == "/live/track/set/solo": self.solo[p[0]] = p[1]


def band_json():
    return {"schema": 1, "interface": {"inputs": {}, "outputs": {"click": [3, 4]}},
            "performers": [{"id": "matt", "name": "Matt", "roles": [], "rigs": [
                {"id": "matt-mic", "kind": "vocal", "audio_input": 1},
                {"id": "matt-keys", "kind": "keys", "midi_port": None, "midi_channel": 1}]}]}


def setup_song(root):
    d, song = make_song(root)
    song["lineups"]["live"] = {"name": "Live", "performers": ["matt"], "assignments": {
        "vox": {"treatment": "live-vocal", "performer": "matt", "rig": "matt-mic", "sections": {"s2": "backing-audio"}},
        "bass": {"treatment": "live-midi", "performer": "matt", "rig": "matt-keys"}}}
    for path, data in ((os.path.join(d, "song.json"), song), (os.path.join(root, "band.json"), band_json())):
        with open(path, "w") as f: json.dump(data, f)
    return d


class Names(unittest.TestCase):
    """expected_scenes / expected_tracks must match what lls.build actually writes."""
    def test_names_match_a_real_build(self):
        from lls.build import build
        with tempfile.TemporaryDirectory() as tmp:
            d = setup_song(tmp)
            band = LU.load_band(os.path.join(tmp, "band.json"))
            for lid in ("default", "live"):
                out, _ = build(d, lid, out_root=os.path.join(tmp, "sets"), band_path=os.path.join(tmp, "band.json"))
                ls = als.load_gz(out).find("LiveSet")
                tracks = [t.find("Name/EffectiveName").get("Value") for t in ls.find("Tracks") if t.tag in ("AudioTrack", "MidiTrack")]
                scenes = [s.find("Name").get("Value") for s in ls.find("Scenes")]
                song = LU.load_song(d)
                names, by_part = LE.expected_tracks(song, band, lid)
                self.assertEqual(names, tracks, lid)
                self.assertEqual(LE.expected_scenes(song), scenes)
                for pid, ns in by_part.items():
                    self.assertTrue(set(ns) <= set(tracks), (lid, pid, ns))
            self.assertEqual(by_part["vox"], ["Vocal (live: Matt)", "Vox (backing)"])
            self.assertEqual(by_part["bass"], ["⚠ Bass"])
            self.assertEqual(LE.expected_scenes(song), ["Verse (bar 1)", "Chorus (bar 3)", "STOP"])

    def test_mix_plan_shared_tracks(self):
        tracks = ["Vocal (live: Matt)", "Pad", "Vox (backing)", "Drums (live: d)", "Click"]
        by_part = {"vox": ["Vocal (live: Matt)", "Vox (backing)"], "bv": ["Vocal (live: Matt)"], "pad": ["Pad"],
                   "kick": ["Drums (live: d)"], "snare": ["Drums (live: d)"], "ghost": ["Gone"]}
        plan, unmapped = LE.mix_plan(tracks, by_part, solo=["pad"], mute=["kick"])
        got = {t["name"]: (t["index"], t["mute"], t["solo"]) for t in plan}
        self.assertEqual(got, {"Vocal (live: Matt)": (0, False, False), "Pad": (1, False, True), "Vox (backing)": (2, False, False),
                               "Drums (live: d)": (3, True, False)})
        self.assertNotIn("Click", got)
        self.assertEqual(unmapped, ["ghost"])
        self.assertEqual(sorted(next(t for t in plan if t["index"] == 3)["parts"]), ["kick", "snare"])


class Client(unittest.TestCase):
    def test_ports_from_env_and_busy(self):
        a, b = free_port(), free_port()
        with mock.patch.dict(os.environ, {"LLS_OSC_PORT": str(a), "LLS_OSC_REPLY_PORT": str(b)}):
            self.assertEqual(live_osc.ports(), (a, b))
            c = live_osc.Live(timeout=0.2)
            try:
                self.assertEqual((c.port, c.reply_port), (a, b))
                with self.assertRaises(live_osc.LiveBusy) as cm:
                    live_osc.Live(timeout=0.2)
                self.assertIn(str(b), str(cm.exception))
            finally:
                c.close()
        self.assertEqual(live_osc.ports(), (11000, 11001))

    def test_multi_and_stale_replies(self):
        reply = free_port()
        fake = FakeLive(reply, scenes=["A (bar 1)", "STOP"], tracks=["Pad", "Click"])
        c = live_osc.Live(timeout=0.5, port=fake.port, reply_port=reply)
        try:
            fake.reply("/live/song/get/tempo", 1.0)   # a stale answer sitting in the socket
            time.sleep(0.05)
            self.assertEqual(c.tempo(), 100.0)
            r = c.multi([("/live/song/get/tempo",), ("/live/song/get/track_names",), ("/live/track/get/playing_slot_index", 1)])
            self.assertEqual(r, [(100.0,), ("Pad", "Click"), (1, -1)])
            self.assertEqual(c.scene_names(), ["A (bar 1)", "STOP"])
        finally:
            c.close(); fake.close()

    def test_not_connected(self):
        link = LE.LiveLink(factory=lambda: live_osc.Live(timeout=0.15, port=free_port(), reply_port=free_port()))
        t = time.time()
        self.assertEqual(link.snapshot(), {"connected": False})
        self.assertLess(time.time() - t, 1.0)
        link.close()

    def test_process_link_uses_env_ports(self):
        reply = free_port()
        fake = FakeLive(reply, scenes=["STOP"], tracks=["Click"])
        try:
            with mock.patch.dict(os.environ, {"LLS_OSC_PORT": str(fake.port), "LLS_OSC_REPLY_PORT": str(reply)}):
                LE.reset()
                self.assertTrue(LE.link().snapshot()["connected"])
        finally:
            LE.reset(); fake.close()


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._lu = sys.modules.get("lls.lineup")
        sys.modules["lls.lineup"] = LU          # other tests stub it; these need the real one
        cls.tmp = tempfile.mkdtemp(prefix="lls-live-")
        cls.song_dir = setup_song(cls.tmp)
        cls.reply = free_port()
        song = LU.load_song(cls.song_dir)
        band = LU.load_band(os.path.join(cls.tmp, "band.json"))
        cls.tracks_live, cls.by_part = LE.expected_tracks(song, band, "live")
        cls.fake = FakeLive(cls.reply, scenes=LE.expected_scenes(song), tracks=cls.tracks_live)
        cls.link = LE.LiveLink(factory=lambda: live_osc.Live(timeout=0.4, port=cls.fake.port, reply_port=cls.reply))
        cls.srv = server.make_server(cls.tmp, 0, quiet=True, open_cmd=["true"], live_link=cls.link, live_open_timeout=1.0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close(); cls.link.close(); cls.fake.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        if cls._lu is None: sys.modules.pop("lls.lineup", None)
        else: sys.modules["lls.lineup"] = cls._lu

    def setUp(self):
        self.fake.scenes = LE.expected_scenes(LU.load_song(self.song_dir)); self.fake.tracks = list(self.tracks_live)
        self.fake.playing, self.fake.slot = False, -1
        self.link.names_at = 0     # re-read names every test

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp: return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_status_loaded_set_and_part_tracks(self):
        st, r = self.req("GET", "/api/live/status?slug=tiny-song&lineup=live")
        self.assertEqual(st, 200)
        self.assertTrue(r["connected"]); self.assertEqual(r["tempo"], 100.0); self.assertFalse(r["playing"])
        self.assertEqual(r["loaded"], {"slug": "tiny-song", "title": "Tiny Song", "lineup": "live"})
        self.assertTrue(r["song"]["scenes_match"]); self.assertTrue(r["song"]["lineup_match"])
        self.assertEqual(r["song"]["parts"]["vox"], ["Vocal (live: Matt)", "Vox (backing)"])
        st, r = self.req("GET", "/api/live/status?slug=tiny-song&lineup=default")
        self.assertTrue(r["song"]["scenes_match"]); self.assertFalse(r["song"]["lineup_match"])

    def test_fire_position_and_stop(self):
        st, r = self.req("POST", "/api/live/fire", {"slug": "tiny-song", "section_id": "s2"})
        self.assertEqual((st, r["scene"]), (200, 1))
        self.assertIn(("/live/scene/fire", (1,)), self.fake.log)
        _, r = self.req("GET", "/api/live/status?slug=tiny-song&lineup=live")
        self.assertTrue(r["playing"]); self.assertEqual(r["scene"], 1)
        self.assertAlmostEqual(r["offset"], 2.5)      # fake clip: playing_position = loop_start + 2.5
        st, r = self.req("POST", "/api/live/loop", {"slug": "tiny-song", "section_id": "s1", "on": True})
        self.assertEqual((st, r["scene"], r["loop"]["section_id"]), (200, 0, "s1"))
        st, _ = self.req("POST", "/api/live/stop", {})
        self.assertEqual(st, 200)
        self.assertIn(("/live/song/stop_all_clips", ()), self.fake.log)
        _, r = self.req("GET", "/api/live/status")
        self.assertFalse(r["playing"]); self.assertIsNone(r["scene"]); self.assertIsNone(r["loop"])

    def test_fire_refuses_other_set(self):
        self.fake.scenes = ["Something else (bar 1)", "STOP"]
        st, r = self.req("POST", "/api/live/fire", {"slug": "tiny-song", "section_id": "s1"})
        self.assertEqual(st, 409); self.assertIn("different set", r["error"])
        st, r = self.req("POST", "/api/live/fire", {"scene_index": 1})
        self.assertEqual(st, 200)
        st, r = self.req("POST", "/api/live/fire", {"scene_index": 9})
        self.assertEqual(st, 400)
        _, r = self.req("GET", "/api/live/status?slug=tiny-song&lineup=live")
        self.assertIsNone(r["loaded"]); self.assertFalse(r["song"]["scenes_match"])

    def test_mix_maps_parts_to_tracks(self):
        st, r = self.req("POST", "/api/live/mix", {"slug": "tiny-song", "lineup_id": "live", "solo": ["vox"], "mute": ["pad"]})
        self.assertEqual(st, 200)
        idx = {n: i for i, n in enumerate(self.tracks_live)}
        self.assertEqual(self.fake.solo.get(idx["Vocal (live: Matt)"]), 1)
        self.assertEqual(self.fake.solo.get(idx["Vox (backing)"]), 1)
        self.assertEqual(self.fake.mute.get(idx["Pad"]), 1)
        self.assertNotIn(idx["Click"], self.fake.solo)
        st, r = self.req("POST", "/api/live/mix", {"tracks": {"Click": {"mute": True}}})
        self.assertEqual((st, self.fake.mute.get(idx["Click"])), (200, 1))

    def test_open_builds_and_waits(self):
        st, r = self.req("POST", "/api/live/open", {"slug": "tiny-song", "lineup_id": "live"})
        self.assertEqual((st, r["state"], r.get("already")), (200, "loaded", True))
        self.fake.tracks = ["Other"]                  # Live still shows another set: build, open, wait, give up
        st, r = self.req("POST", "/api/live/open", {"slug": "tiny-song", "lineup_id": "live"})
        self.assertEqual((st, r["state"]), (202, "waiting"))
        self.assertIn("save dialog", r["message"])
        self.assertTrue(os.path.isfile(r["als"])); self.assertTrue(r["built"])
        threading.Timer(0.3, lambda: setattr(self.fake, "tracks", list(self.tracks_live))).start()
        st, r = self.req("POST", "/api/live/open", {"slug": "tiny-song", "lineup_id": "live", "force": True})
        self.assertEqual((st, r["state"], r["built"]), (200, "loaded", False))

    def test_not_connected_and_busy(self):
        dead = LE.LiveLink(factory=lambda: live_osc.Live(timeout=0.15, port=free_port(), reply_port=free_port()))
        with mock.patch.object(self.srv.RequestHandlerClass, "live_link", dead):
            _, r = self.req("GET", "/api/live/status?slug=tiny-song&lineup=live")
            self.assertEqual(r, {"connected": False})
            st, r = self.req("POST", "/api/live/stop", {})
            self.assertEqual(st, 503); self.assertIn("AbletonOSC", r["error"])
        dead.close()
        hog = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); hog.bind(("127.0.0.1", 0))
        busy = LE.LiveLink(factory=lambda: live_osc.Live(timeout=0.15, port=free_port(), reply_port=hog.getsockname()[1]))
        try:
            with mock.patch.object(self.srv.RequestHandlerClass, "live_link", busy):
                st, r = self.req("GET", "/api/live/status")
                self.assertEqual(st, 200); self.assertTrue(r["busy"]); self.assertIn("already in use", r["error"])
                st, r = self.req("POST", "/api/live/fire", {"scene_index": 0})
                self.assertEqual(st, 409)
        finally:
            hog.close()


if __name__ == "__main__":
    unittest.main()
