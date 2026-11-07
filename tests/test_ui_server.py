"""UI server API tests. Runs the real server on a random port against a temp copy of songs/ + band.json.
A stub lls.lineup is injected through sys.modules (the real one is written separately).
Run: python -m unittest tests.test_ui_server"""
import json, os, shutil, sys, tempfile, threading, types, unittest, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lls.ui import server  # noqa: E402

SLUG = "thoughts-for-the-night"


def stub_lineup():
    m = types.ModuleType("lls.lineup")
    m.calls = []
    m.load_band = lambda path: server.read_json(path)

    def validate(song, band, lineup_id):
        m.calls.append(("validate", lineup_id))
        out = []
        for pid, a in song["lineups"][lineup_id].get("assignments", {}).items():
            if a.get("treatment", "").startswith("live-") and not a.get("performer"):
                out.append({"level": "error", "part": pid, "section": None, "message": "live treatment with no performer"})
        return out

    def suggest(song, band, performer_ids):
        m.calls.append(("suggest", tuple(performer_ids)))
        return {"name": "Stub", "performers": list(performer_ids),
                "assignments": {"bass-line": {"treatment": "live-midi", "performer": performer_ids[0], "rig": "matt-keys"}}}

    m.validate, m.suggest = validate, suggest
    m.resolve = lambda song, band, lineup_id: {}
    return m


class UiServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lls-ui-")
        shutil.copytree(os.path.join(ROOT, "songs"), os.path.join(cls.tmp, "songs"),
                        ignore=shutil.ignore_patterns("stems", "*.wav"))
        shutil.copy(os.path.join(ROOT, "band.json"), os.path.join(cls.tmp, "band.json"))
        cls.opened = []
        cls.srv = server.make_server(cls.tmp, 0, quiet=True, open_cmd=["true"])
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self._saved = sys.modules.get("lls.lineup")

    def tearDown(self):
        if self._saved is None: sys.modules.pop("lls.lineup", None)
        else: sys.modules["lls.lineup"] = self._saved

    def req(self, method, path, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                payload = resp.read()
                return resp.status, (payload if raw else json.loads(payload))
        except urllib.error.HTTPError as e:
            payload = e.read()
            return e.code, (payload if raw else json.loads(payload or b"{}"))

    def no_lineup(self):
        sys.modules["lls.lineup"] = None  # makes `import lls.lineup` raise ModuleNotFoundError

    # --- static
    def test_serves_index_and_assets(self):
        st, body = self.req("GET", "/", raw=True)
        self.assertEqual(st, 200); self.assertIn(b"<title>Live Band Sets</title>", body)
        for p in ("/static/app.js", "/static/app.css"):
            self.assertEqual(self.req("GET", p, raw=True)[0], 200)
        self.assertEqual(self.req("GET", "/static/../server.py", raw=True)[0], 404)

    # --- songs
    def test_list_and_get(self):
        st, songs = self.req("GET", "/api/songs")
        self.assertEqual(st, 200)
        s = next(x for x in songs if x["slug"] == SLUG)
        self.assertEqual(s["title"], "Thoughts For The Night"); self.assertGreater(s["parts"], 10)
        st, song = self.req("GET", f"/api/songs/{SLUG}")
        self.assertEqual(st, 200); self.assertIn("sections", song)
        self.assertEqual(self.req("GET", "/api/songs/nope")[0], 404)
        self.assertEqual(self.req("GET", "/api/songs/..%2Fx")[0], 404)

    def test_activity(self):
        st, act = self.req("GET", f"/api/songs/{SLUG}/activity")
        self.assertEqual(st, 200)
        self.assertEqual(act["beats_per_bar"], 4)
        bass = act["parts"]["bass-line"]
        self.assertEqual(bass["source"], "midi"); self.assertEqual(len(bass["bars"]), act["bars"])
        self.assertTrue(any(bass["bars"])); self.assertFalse(bass["bars"][0])  # bass enters after the intro
        self.assertEqual(act["parts"]["phone-filter-verse"]["source"], "unknown")  # no stems in the temp copy

    def test_put_song_atomic_with_backup(self):
        _, song = self.req("GET", f"/api/songs/{SLUG}")
        song["sections"][0]["name"] = "Intro"
        song["lineups"]["duo"] = {"name": "Duo", "performers": ["matt"],
                                  "assignments": {"bass-line": {"treatment": "live-midi", "performer": "matt", "rig": "matt-keys", "sections": {"s7": "midi-clip"}}}}
        st, r = self.req("PUT", f"/api/songs/{SLUG}", song)
        self.assertEqual((st, r), (200, {"ok": True}))
        sp = os.path.join(self.tmp, "songs", SLUG, "song.json")
        self.assertEqual(server.read_json(sp)["sections"][0]["name"], "Intro")
        self.assertTrue(os.path.exists(sp + ".bak"))
        self.assertFalse([f for f in os.listdir(os.path.dirname(sp)) if f.startswith(".tmp-")])

    def test_put_song_rejects_bad_shape(self):
        _, song = self.req("GET", f"/api/songs/{SLUG}")
        sp = os.path.join(self.tmp, "songs", SLUG, "song.json")
        with open(sp) as f: before = f.read()
        bad = json.loads(json.dumps(song)); bad["sections"][1]["start"] = bad["sections"][1]["end"]
        self.assertEqual(self.req("PUT", f"/api/songs/{SLUG}", bad)[0], 400)
        bad = json.loads(json.dumps(song)); bad["lineups"] = {"x": {"assignments": {"bass-line": {"treatment": "nope"}}}}
        st, r = self.req("PUT", f"/api/songs/{SLUG}", bad)
        self.assertEqual(st, 400); self.assertIn("unknown treatment", r["error"])
        self.assertEqual(self.req("PUT", f"/api/songs/{SLUG}", [1, 2])[0], 400)
        with open(sp) as f: self.assertEqual(f.read(), before)

    # --- band
    def test_band_roundtrip_and_validation(self):
        st, band = self.req("GET", "/api/band")
        self.assertEqual(st, 200); self.assertTrue(band["performers"])
        band["interface"]["name"] = "Volt 476"
        self.assertEqual(self.req("PUT", "/api/band", band)[0], 200)
        self.assertEqual(self.req("GET", "/api/band")[1]["interface"]["name"], "Volt 476")
        band["performers"][0]["rigs"][0]["kind"] = "theremin"
        self.assertEqual(self.req("PUT", "/api/band", band)[0], 400)

    # --- lineup engine
    def test_validate_and_suggest_with_stub(self):
        stub = stub_lineup(); sys.modules["lls.lineup"] = stub
        _, song = self.req("GET", f"/api/songs/{SLUG}")
        song.setdefault("lineups", {})["solo"] = {"name": "Solo", "performers": [],
                                                  "assignments": {"bass-line": {"treatment": "live-midi"}}}
        st, r = self.req("POST", f"/api/songs/{SLUG}/validate", {"lineup_id": "solo", "song": song})
        self.assertEqual(st, 200)
        self.assertEqual(r["issues"][0]["part"], "bass-line"); self.assertEqual(r["issues"][0]["level"], "error")
        self.assertEqual(self.req("POST", f"/api/songs/{SLUG}/validate", {"lineup_id": "missing", "song": song})[0], 400)
        st, r = self.req("POST", f"/api/songs/{SLUG}/suggest", {"performers": ["matt", "drummer"]})
        self.assertEqual(st, 200); self.assertEqual(r["lineup"]["performers"], ["matt", "drummer"])
        self.assertIn(("suggest", ("matt", "drummer")), stub.calls)
        self.assertEqual(self.req("POST", f"/api/songs/{SLUG}/suggest", {"performers": "matt"})[0], 400)

    def test_501_without_lineup_engine(self):
        self.no_lineup()
        for ep, body in (("validate", {"lineup_id": "default"}), ("suggest", {"performers": ["matt"]}),
                         ("build", {"lineup_id": "default"})):
            st, r = self.req("POST", f"/api/songs/{SLUG}/{ep}", body)
            self.assertEqual(st, 501, ep); self.assertIn("lls.lineup", r["error"])

    def test_open_in_live_guards(self):
        self.assertEqual(self.req("POST", "/api/open-in-live", {"als": "/etc/passwd"})[0], 400)
        self.assertEqual(self.req("POST", "/api/open-in-live", {"als": "/tmp/x.als"})[0], 403)
        als = os.path.join(self.tmp, "out", "sets", "S", "default", "S Project", "S.als")
        self.assertEqual(self.req("POST", "/api/open-in-live", {"als": als})[0], 404)
        os.makedirs(os.path.dirname(als)); open(als, "w").close()
        self.assertEqual(self.req("POST", "/api/open-in-live", {"als": als})[0], 200)  # open_cmd is `true` here

    def test_rejects_foreign_host(self):
        r = urllib.request.Request(self.base + "/api/songs", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(r, timeout=5)
        self.assertEqual(cm.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
