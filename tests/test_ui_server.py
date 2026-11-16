"""UI server API tests. Runs the real server on a random port against a temp copy of songs/ + band.json.
A stub lls.lineup is injected through sys.modules (the real one is written separately).
Run: python -m unittest tests.test_ui_server"""
import io, json, os, shutil, sys, tempfile, threading, time, types, unittest, urllib.error, urllib.request, wave
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lls.ui import server  # noqa: E402
import lls.ingest, lls.setlist, lls.stems  # noqa: E402,F401  (so mock.patch can target them)
from tests.synth import make_song  # noqa: E402

SLUG = "thoughts-for-the-night"
from tests.paths import SAMPLE, HAVE_SAMPLE  # noqa: E402


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
        for p in ("/static/app.js", "/static/app.css", "/static/preview.js"):
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

    # --- helpers for the workflow endpoints
    def get_raw(self, path, headers=None):
        r = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp: return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def wait_job(self, jid, timeout=600):
        t0, lines, since = time.time(), [], 0
        while time.time() - t0 < timeout:
            st, j = self.req("GET", f"/api/jobs/{jid}?since={since}")
            self.assertEqual(st, 200)
            lines += j["lines"]; since = j["next"]
            if j["state"] != "running": j["all_lines"] = lines; return j
            time.sleep(0.05)
        self.fail(f"job {jid} timed out")

    def handler_attr(self, **kw):
        h = self.srv.RequestHandlerClass
        old = {k: getattr(h, k) for k in kw}
        for k, v in kw.items(): setattr(h, k, v)
        self.addCleanup(lambda: [setattr(h, k, v) for k, v in old.items()])

    # --- stems (preview playback)
    def test_stem_range_requests(self):
        make_song(self.tmp, "range-song", "Range Song")
        st, h, full = self.get_raw("/api/songs/range-song/stem/pad")
        self.assertEqual(st, 200); self.assertEqual(h["Accept-Ranges"], "bytes"); self.assertEqual(h["Content-Type"], "audio/wav")
        self.assertEqual(full[:4], b"RIFF"); self.assertEqual(int(h["Content-Length"]), len(full))
        st, h, part = self.get_raw("/api/songs/range-song/stem/pad", {"Range": "bytes=0-99"})
        self.assertEqual(st, 206); self.assertEqual(part, full[:100]); self.assertEqual(h["Content-Range"], f"bytes 0-99/{len(full)}")
        st, h, part = self.get_raw("/api/songs/range-song/stem/pad", {"Range": "bytes=1000-"})
        self.assertEqual((st, part), (206, full[1000:]))
        st, h, part = self.get_raw("/api/songs/range-song/stem/pad", {"Range": "bytes=-16"})
        self.assertEqual((st, part), (206, full[-16:]))
        st, h, _ = self.get_raw("/api/songs/range-song/stem/pad", {"Range": f"bytes={len(full) + 10}-"})
        self.assertEqual(st, 416); self.assertEqual(h["Content-Range"], f"bytes */{len(full)}")
        # 24-bit source -> 16-bit preview, cached under stems/preview/
        st, h, pcm = self.get_raw("/api/songs/range-song/stem/pad?pcm16=1")
        self.assertEqual(st, 200)
        with wave.open(io.BytesIO(pcm)) as w, wave.open(io.BytesIO(full)) as w0:
            self.assertEqual((w.getsampwidth(), w.getnframes(), w.getframerate()), (2, w0.getnframes(), w0.getframerate()))
            a = np.frombuffer(w.readframes(400), "<i2")
            self.assertAlmostEqual(float(np.abs(a).max()) / 32768, 0.5, places=2)
        self.assertTrue(os.listdir(os.path.join(self.tmp, "songs", "range-song", "stems", "preview")))
        self.assertEqual(self.get_raw("/api/songs/range-song/stem/bass")[0], 404)   # MIDI-only, no stem
        self.assertEqual(self.get_raw("/api/songs/range-song/stem/_mix")[0], 404)   # no mix bounce
        self.assertEqual(self.get_raw("/api/songs/range-song/stem/nope")[0], 404)
        sp = os.path.join(self.tmp, "songs", "range-song", "song.json")
        s = server.read_json(sp); s["parts"][0]["stem"] = "../../band.json"; server.write_json_atomic(sp, s)
        self.assertEqual(self.get_raw("/api/songs/range-song/stem/pad")[0], 403)

    # --- spoken cues for the preview (lls.build.cues_wav, `say` faked)
    def test_cues_endpoint_caches_and_invalidates(self):
        import lls.build
        d, song = make_song(self.tmp, "cue-song", "Cue Song")
        spoken = []

        def fake_speak(text, sr):
            spoken.append((text, sr))
            t = np.arange(int(0.2 * sr)) / sr
            x = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
            return np.stack([x, x], axis=1)

        with mock.patch.object(lls.build, "_speak", fake_speak):
            # GET without a hash renders from song.json on disk
            st, h, full = self.get_raw("/api/songs/cue-song/cues.wav")
            self.assertEqual(st, 200); self.assertEqual(h["Content-Type"], "audio/wav"); self.assertEqual(h["Accept-Ranges"], "bytes")
            self.assertEqual(spoken, [("Chorus", server.CUES_SR)])   # first section: no cue (count-in)
            with wave.open(io.BytesIO(full)) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, server.CUES_SR))
                a = np.frombuffer(w.readframes(w.getnframes()), "<i2")
            k = int(4 * 0.5 * server.CUES_SR)   # one bar (4 beats at 120 bpm) before the Chorus at beat 8
            self.assertEqual(int(np.abs(a[:k - 10]).max()), 0); self.assertGreater(int(np.abs(a[k:k + 2000]).max()), 1000)
            pdir = os.path.join(d, "stems", "preview")
            self.assertEqual(len([f for f in os.listdir(pdir) if f.startswith("cues-")]), 1)
            # Range on the cached file; no re-render
            st, h, part = self.get_raw("/api/songs/cue-song/cues.wav", {"Range": "bytes=0-43"})
            self.assertEqual((st, part), (206, full[:44])); self.assertEqual(h["Content-Range"], f"bytes 0-43/{len(full)}")
            self.assertEqual(len(spoken), 1)
            # POST the same song -> same hash, still cached
            st, r = self.req("POST", "/api/songs/cue-song/cues.wav", {})
            self.assertEqual(st, 200); self.assertEqual(len(spoken), 1); h1 = r["hash"]
            self.assertEqual(r["url"], f"/api/songs/cue-song/cues.wav?h={h1}")
            st, _, again = self.get_raw(r["url"])
            self.assertEqual((st, again), (200, full))
            # unsaved edit (cue text) -> new hash, re-rendered with the new text
            edited = json.loads(json.dumps(song)); edited["sections"][1]["cue"] = "Big chorus"
            st, r2 = self.req("POST", "/api/songs/cue-song/cues.wav", {"song": edited})
            self.assertEqual(st, 200); self.assertNotEqual(r2["hash"], h1); self.assertEqual(spoken[-1][0], "Big chorus")
            self.assertEqual(r2["cues"][0][0], "s2")
            st, h, part = self.get_raw(r2["url"], {"Range": "bytes=-10"})
            self.assertEqual(st, 206)
            # tempo and section moves invalidate too; renames do
            for change in (lambda s: s.__setitem__("tempo", 100.0), lambda s: s["sections"][1].__setitem__("name", "Hook")):
                e = json.loads(json.dumps(song)); change(e)
                self.assertNotIn(server.cues_hash(e), (h1, r2["hash"]))
            # voice settings are in the hash
            with mock.patch.object(lls.build, "CUE_RATE", 180):
                self.assertNotEqual(server.cues_hash(song), h1)
            # old renders are pruned to CUES_KEEP
            for i in range(server.CUES_KEEP + 2):
                e = json.loads(json.dumps(song)); e["sections"][1]["cue"] = f"take {i}"
                self.assertEqual(self.req("POST", "/api/songs/cue-song/cues.wav", {"song": e})[0], 200)
            self.assertEqual(len([f for f in os.listdir(pdir) if f.startswith("cues-")]), server.CUES_KEEP)
            self.assertFalse([f for f in os.listdir(pdir) if f.startswith(".cues-")])   # no temp files left
        self.assertEqual(self.get_raw(f"/api/songs/cue-song/cues.wav?h={'0' * 12}")[0], 404)
        self.assertEqual(self.get_raw("/api/songs/cue-song/cues.wav?h=../../x")[0], 400)
        bad = json.loads(json.dumps(song)); bad["tempo"] = -1
        self.assertEqual(self.req("POST", "/api/songs/cue-song/cues.wav", {"song": bad})[0], 400)

    def test_midi_notes_endpoint(self):
        make_song(self.tmp, "midi-song", "Midi Song")
        st, r = self.req("GET", "/api/songs/midi-song/midi/bass")
        self.assertEqual(st, 200); self.assertEqual(r["notes"], [[60, 0.0, 1.0, 100], [64, 8.0, 1.0, 100]])
        self.assertEqual(self.req("GET", "/api/songs/midi-song/midi/pad")[0], 404)    # audio part: no MIDI
        self.assertEqual(self.req("GET", "/api/songs/midi-song/midi/nope")[0], 404)

    # --- import job
    def test_import_job_with_fake_ingest(self):
        src_dir = tempfile.mkdtemp(prefix="lls-orig-"); self.addCleanup(shutil.rmtree, src_dir, True)
        orig = os.path.join(src_dir, "My New Song.logicx")
        os.makedirs(os.path.join(orig, "Alternatives", "000")); open(os.path.join(orig, "Alternatives", "000", "ProjectData"), "wb").write(b"x" * 64)
        exports = os.path.join(src_dir, "exports"); os.makedirs(exports)
        before = sorted((p, os.path.getmtime(os.path.join(dp, p))) for dp, _, fs in os.walk(orig) for p in fs)
        seen = {}

        def fake_ingest(logicx, song_dir):
            seen["logicx"] = logicx
            print(" rendering raw stem: Pad (2 regions)")
            _, song = make_song(os.path.dirname(os.path.dirname(song_dir)), os.path.basename(song_dir), "my-new-song")
            song["ignored_tracks"] = [{"name": "Hat", "status": "MISSING", "reason": "Apple Loop"}]
            server.write_json_atomic(os.path.join(song_dir, "song.json"), song)
            return song

        def fake_link(song_dir, export_dir):
            print("linking…")
            return [("Pad.wav", "→ part 'Pad'"), ("Reverb.wav", "→ NEW part 'reverb' (backing-audio): no Logic track matched; check it"),
                    ("Bass", "⚠ no exported stem: Logic built-in"), ("My New Song.wav", "full mix")]

        with mock.patch("lls.ingest.ingest", fake_ingest), mock.patch("lls.stems.link", fake_link):
            st, r = self.req("POST", "/api/import", {"logicx": orig, "slug": "my-new-song", "export_dir": exports})
            self.assertEqual(st, 202)
            j = self.wait_job(r["job"])
        self.assertEqual(j["state"], "done", j)
        copy = os.path.join(self.tmp, "samples", "my-new-song.logicx")
        self.assertEqual(seen["logicx"], copy)  # ingest only ever sees the copy
        self.assertTrue(os.path.isfile(os.path.join(copy, "Alternatives", "000", "ProjectData")))
        self.assertEqual(before, sorted((p, os.path.getmtime(os.path.join(dp, p))) for dp, _, fs in os.walk(orig) for p in fs))
        self.assertIn(" rendering raw stem: Pad (2 regions)", j["all_lines"]); self.assertIn("linking…", j["all_lines"])
        res = j["result"]
        self.assertEqual(res["title"], "My New Song")  # parser fell back to the copy's file name: restored
        self.assertEqual([x["status"] for x in res["link"]], ["matched", "new", "missing", "mix"])
        self.assertEqual(res["ignored_tracks"][0]["status"], "MISSING")
        self.assertEqual(len(res["sections"]), 2)
        song = server.read_json(os.path.join(self.tmp, "songs", "my-new-song", "song.json"))
        self.assertEqual(song["source"]["original"], orig); self.assertEqual(song["source"]["logicx"], copy)
        # existing slug needs reimport; bad inputs are refused before any job starts
        self.assertEqual(self.req("POST", "/api/import", {"logicx": orig, "slug": "my-new-song"})[0], 409)
        self.assertEqual(self.req("POST", "/api/import", {"logicx": exports, "slug": "x-song"})[0], 400)
        self.assertEqual(self.req("POST", "/api/import", {"logicx": orig, "slug": "Bad Slug"})[0], 400)
        self.assertEqual(self.req("POST", "/api/import", {"slug": "my-new-song", "reimport": True})[0], 400)
        # link-stems only (no Logic project) on an existing song
        with mock.patch("lls.stems.link", fake_link):
            st, r = self.req("POST", "/api/import", {"slug": "my-new-song", "export_dir": exports, "reimport": True})
            j = self.wait_job(r["job"])
        self.assertEqual(j["state"], "done"); self.assertIsNone(j["result"]["copy"])

    def test_import_job_error_is_reported(self):
        src_dir = tempfile.mkdtemp(prefix="lls-orig-"); self.addCleanup(shutil.rmtree, src_dir, True)
        orig = os.path.join(src_dir, "Broken.logicx"); os.makedirs(orig)

        def boom(logicx, song_dir): print("reading…"); raise ValueError("not a Logic project")
        with mock.patch("lls.ingest.ingest", boom):
            j = self.wait_job(self.req("POST", "/api/import", {"logicx": orig, "slug": "broken"})[1]["job"])
        self.assertEqual(j["state"], "error"); self.assertIn("not a Logic project", j["error"]); self.assertIn("reading…", j["all_lines"])

    @unittest.skipUnless(HAVE_SAMPLE, "sample project not present (samples/ or $LLS_SAMPLE_PROJECT)")
    def test_import_real_sample_into_temp_root(self):
        st, r = self.req("POST", "/api/import", {"logicx": SAMPLE, "slug": "thoughts-copy"})
        j = self.wait_job(r["job"])
        self.assertEqual(j["state"], "done", j.get("error"))
        self.assertEqual(j["result"]["title"], "Thoughts For The Night")
        self.assertGreater(len(j["result"]["parts"]), 10)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "samples", "thoughts-copy.logicx")))

    # --- pickers / reveal
    def test_pick_and_reveal(self):
        fake = lambda out, code=0: [sys.executable, "-c", f"import sys; print({out!r}); sys.exit({code})"]
        self.handler_attr(osascript=fake("/Users/x/Song.logicx/"))
        self.assertEqual(self.req("POST", "/api/pick", {"kind": "logicx"}), (200, {"path": "/Users/x/Song.logicx"}))
        self.handler_attr(osascript=fake("/Users/x/Exports/"))
        self.assertEqual(self.req("POST", "/api/pick", {"kind": "folder"}), (200, {"path": "/Users/x/Exports"}))
        self.assertEqual(self.req("POST", "/api/pick", {"kind": "logicx"})[0], 400)  # not a .logicx
        self.handler_attr(osascript=[sys.executable, "-c", "import sys; sys.stderr.write('User canceled. (-128)'); sys.exit(1)"])
        self.assertEqual(self.req("POST", "/api/pick", {"kind": "folder"}), (200, {"cancelled": True}))
        self.assertEqual(self.req("POST", "/api/pick", {"kind": "nope"})[0], 400)
        self.handler_attr(reveal_cmd=["true"])
        self.assertEqual(self.req("POST", "/api/reveal", {"path": "/etc"})[0], 403)
        self.assertEqual(self.req("POST", "/api/reveal", {"path": "songs/nope"})[0], 404)
        self.assertEqual(self.req("POST", "/api/reveal", {"path": os.path.join(self.tmp, "band.json")})[0], 200)

    # --- setlists
    def test_setlist_crud_and_build(self):
        sl = {"name": "Friday: Club", "band": "band.json", "songs": [{"song": SLUG, "lineup": "default", "notes": "Opener"}]}
        self.assertEqual(self.req("PUT", "/api/setlists/friday", sl), (200, {"ok": True}))
        st, lst = self.req("GET", "/api/setlists")
        self.assertEqual(st, 200)
        self.assertEqual(next(x for x in lst if x["slug"] == "friday"), {"slug": "friday", "name": "Friday: Club", "songs": 1, "built": False})
        self.assertEqual(self.req("GET", "/api/setlists/friday")[1]["songs"][0]["notes"], "Opener")
        self.assertEqual(self.req("PUT", "/api/setlists/friday", {**sl, "songs": [{"song": "no-such-song"}]})[0], 400)
        self.assertEqual(self.req("PUT", "/api/setlists/friday", {**sl, "name": ""})[0], 400)
        self.assertEqual(self.req("PUT", "/api/setlists/Bad%20Slug", sl)[0], 404)
        self.assertEqual(self.req("GET", "/api/setlists/friday/running-order")[0], 404)
        calls = []

        def stub_build(path, out_root, root):
            calls.append((path, out_root, root)); print("building stub")
            base = os.path.join(out_root, "Friday- Club"); os.makedirs(os.path.join(base, "fallback"), exist_ok=True)
            open(os.path.join(base, "running-order.html"), "w").write("<html><h1>Friday: Club</h1></html>")
            open(os.path.join(base, "fallback", "fallback-report.md"), "w").write("# Phone fallback\n")
            return {"dir": base, "projects": [os.path.join(base, "01 Song Project")], "running_order": os.path.join(base, "running-order.md"),
                    "fallback": [{"song": "Song", "files": [], "included": [{"part": "pad", "name": "Pad", "sections": 2}], "missing": []}]}

        with mock.patch("lls.setlist.build_setlist", stub_build):
            st, r = self.req("POST", "/api/setlists/friday/build")
            self.assertEqual(st, 202)
            j = self.wait_job(r["job"])
        self.assertEqual(j["state"], "done", j)
        self.assertEqual(calls[0], (os.path.join(self.tmp, "setlists", "friday.json"), os.path.join(self.tmp, "out", "setlists"), self.tmp))
        self.assertEqual(j["result"]["fallback_report"], "# Phone fallback\n")
        self.assertIn("building stub", j["all_lines"])
        st, h, body = self.get_raw(j["result"]["running_order_html"])
        self.assertEqual(st, 200); self.assertIn(b"Friday: Club", body); self.assertIn("text/html", h["Content-Type"])
        self.assertTrue(next(x for x in self.req("GET", "/api/setlists")[1] if x["slug"] == "friday")["built"])
        self.handler_attr(reveal_cmd=["true"])
        self.assertEqual(self.req("POST", "/api/reveal", {"path": j["result"]["dir"]})[0], 200)
        self.assertEqual(self.req("DELETE", "/api/setlists/friday"), (200, {"ok": True}))
        self.assertEqual(self.req("GET", "/api/setlists/friday")[0], 404)
        self.assertEqual(self.req("GET", "/api/jobs/j999999")[0], 404)


if __name__ == "__main__":
    unittest.main()
