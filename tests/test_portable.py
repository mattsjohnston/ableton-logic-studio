"""Portability: Live app detection (lls.liveapp), song packages (lls.package), the setup doctor, and their UI routes.
Run: .venv/bin/python -m unittest tests.test_portable"""
import io, json, os, plistlib, shutil, tempfile, threading, unittest, urllib.error, urllib.request, zipfile
from contextlib import redirect_stdout
from unittest import mock

from lls import doctor, liveapp, package
from lls.__main__ import main as cli
from lls.ui import server
from tests.synth import make_song


def fake_live(root, name, version, schemas=("12.0_12402",), demos=("Suite",)):
    app = os.path.join(root, name + ".app")
    os.makedirs(os.path.join(app, "Contents", "App-Resources", "Schema"))
    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleShortVersionString": version, "CFBundleName": "Live"}, f)
    for s in schemas: open(os.path.join(app, "Contents", "App-Resources", "Schema", s + ".txt"), "w").close()
    ds = os.path.join(app, "Contents", "App-Resources", "Core Library", "Lessons", "Demo Songs")
    os.makedirs(ds)
    for d in demos: open(os.path.join(ds, f"Chuck Sutton - Patience (Live 12 {d} Demo).als"), "w").close()
    return app


class LiveDetection(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {}, clear=False); self.env.start(); os.environ.pop("LIVE_APP", None)
        liveapp._cached.clear()

    def tearDown(self):
        self.env.stop(); liveapp._cached.clear(); shutil.rmtree(self.d)

    def test_versions(self):
        v = liveapp.parse_version
        self.assertGreater(v("12.4.25"), v("12.4.25b1 (2026-10-28_e04f)"))
        self.assertGreater(v("12.4.25b1"), v("12.3.2"))
        self.assertGreater(v("12.10"), v("12.9.9"))
        self.assertEqual(liveapp.parse_schema("12.0_12402.txt"), (12, 0, 12402))

    def test_prefers_suite_then_newest(self):
        fake_live(self.d, "Ableton Live 12 Beta", "12.4.25b1 (2026)")
        fake_live(self.d, "Ableton Live 12 Standard", "12.5.0")
        fake_live(self.d, "Ableton Live 12 Suite", "12.3.1")
        fake_live(self.d, "Ableton Live 12 Suite 2", "12.4.0")   # a second Suite copy, newer
        a = liveapp.find_live(dirs=[self.d])
        self.assertEqual((a.name, a.version, a.edition), ("Ableton Live 12 Suite 2", "12.4.0", "Suite"))
        path, name, version = a  # unpacks as the documented triple
        self.assertTrue(path.endswith("Ableton Live 12 Suite 2.app"))

    def test_falls_back_to_any_live12_newest(self):
        fake_live(self.d, "Ableton Live 12 Standard", "12.1.5")
        fake_live(self.d, "Ableton Live 12 Beta", "12.4.25b1")
        fake_live(self.d, "Ableton Live 11 Suite", "11.3.4")
        a = liveapp.find_live(dirs=[self.d])
        self.assertEqual(a.name, "Ableton Live 12 Beta"); self.assertTrue(a.beta); self.assertIsNone(a.edition)

    def test_release_beats_beta_of_same_version(self):
        fake_live(self.d, "Ableton Live 12 Suite Beta", "12.4.0b3")
        fake_live(self.d, "Ableton Live 12 Suite", "12.4.0")
        self.assertEqual(liveapp.find_live(dirs=[self.d]).name, "Ableton Live 12 Suite")

    def test_env_override_and_errors(self):
        app = fake_live(self.d, "Somewhere Live", "12.2.0")
        os.environ["LIVE_APP"] = app
        self.assertEqual(liveapp.find_live(dirs=[self.d]).path, app)
        os.environ["LIVE_APP"] = os.path.join(self.d, "Nope.app")
        with self.assertRaises(liveapp.LiveNotFound) as cm: liveapp.find_live(dirs=[self.d])
        self.assertIn("Install Ableton Live 12 Suite", str(cm.exception))
        os.environ.pop("LIVE_APP")
        with self.assertRaises(liveapp.LiveNotFound) as cm: liveapp.find_live(dirs=[os.path.join(self.d, "empty")])
        self.assertIn("No Ableton Live 12 found", str(cm.exception))

    def test_schema_warning(self):
        old = liveapp.read_app(fake_live(self.d, "Ableton Live 12 Suite", "12.1.5", schemas=("12.0_12117", "11.0_11300")))
        self.assertEqual(old.schema(), "12.0_12117")
        self.assertFalse(old.schema_ok())
        self.assertIn("Update Live", old.schema_warning())
        new = liveapp.read_app(fake_live(self.d, "Ableton Live 12 Suite X", "12.4.0", schemas=("12.0_12402", "12.0_12049")))
        self.assertTrue(new.schema_ok()); self.assertIsNone(new.schema_warning())

    def test_demo_song_any_edition(self):
        a = liveapp.read_app(fake_live(self.d, "Ableton Live 12 Standard", "12.4.0", demos=("Lite", "Standard", "Intro")))
        self.assertIn("(Live 12 Standard Demo)", a.demo_song())
        b = liveapp.read_app(fake_live(self.d, "Ableton Live 12 Intro", "12.4.0", demos=()))
        self.assertIsNone(b.demo_song())

    def test_als_reports_missing_demo(self):
        from lls import als
        os.environ["LIVE_APP"] = fake_live(self.d, "Ableton Live 12 Intro", "12.4.0", demos=())
        with self.assertRaises(FileNotFoundError) as cm: als.demo_path()
        self.assertIn("Patience", str(cm.exception))


def _song(root, slug="tiny-song"):
    d, _ = make_song(root, slug, "Tiny Song")
    sp = os.path.join(d, "song.json")
    s = json.load(open(sp))
    s["source"] = {"logicx": "/Users/someone/Music/Tiny Song.logicx", "original": "/Volumes/x/Tiny Song.logicx/",
                   "export_dir": "/Users/someone/Desktop/exports", "parser": "x"}
    s["quicksampler_files"] = ["/Library/Application Support/Logic/EXS Factory Samples/k.aif"]
    s.setdefault("lineups", {})["duo"] = {"name": "Duo", "assignments": {}}
    json.dump(s, open(sp, "w"))
    for rel in ("midi/a.json", "instruments/x.aupreset", "stems/export/a.wav", "stems/raw/r.wav"):
        p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        if not os.path.exists(p):
            with open(p, "wb") as f: f.write(os.urandom(256))
    for rel in ("stems/preview/a.wav", "song.json.bak"):
        p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write("skip me")
    return d


class Packages(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.src = _song(os.path.join(self.d, "a"))
        self.dst_songs = os.path.join(self.d, "b", "songs")
        self.pkg = os.path.join(self.d, "tiny.llsong")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_round_trip(self):
        out, m = package.export(self.src, self.pkg)
        self.assertEqual(out, self.pkg)
        names = {e["path"] for e in m["files"]}
        self.assertIn("song.json", names); self.assertIn("stems/export/a.wav", names); self.assertIn("stems/raw/r.wav", names)
        self.assertIn("midi/a.json", names); self.assertIn("instruments/x.aupreset", names)
        self.assertFalse(any(n.startswith("stems/preview") or n.endswith(".bak") for n in names))
        self.assertEqual((m["format"], m["title"], m["slug"]), (package.FORMAT, "Tiny Song", "tiny-song"))
        for k in ("lls_version", "git_sha", "created_at"): self.assertIn(k, m)
        r = package.import_package(self.pkg, self.dst_songs)
        dst = r["song_dir"]
        self.assertEqual(os.path.basename(dst), "tiny-song")
        for rel in names - {"song.json"}:
            self.assertEqual(open(os.path.join(self.src, rel), "rb").read(), open(os.path.join(dst, rel), "rb").read(), rel)
        s = json.load(open(os.path.join(dst, "song.json")))
        self.assertEqual(s["source"]["logicx"], "Tiny Song.logicx")
        self.assertEqual(s["source"]["original"], "Tiny Song.logicx")
        self.assertEqual(s["source"]["export_dir"], "exports")
        self.assertEqual(s["quicksampler_files"], ["/Library/Application Support/Logic/EXS Factory Samples/k.aif"])
        self.assertIn("duo", s["lineups"])
        self.assertFalse(os.path.exists(os.path.join(dst, "stems", "preview")))
        self.assertEqual(sorted(os.listdir(self.dst_songs)), ["tiny-song"])  # no staging leftovers
        r2 = package.import_package(self.pkg, self.dst_songs, slug="other-name")
        self.assertTrue(os.path.isfile(os.path.join(r2["song_dir"], "song.json")))

    def _rewrite(self, fn):
        bad = os.path.join(self.d, "bad.llsong")
        with zipfile.ZipFile(self.pkg) as zi, zipfile.ZipFile(bad, "w") as zo:
            for i in zi.infolist():
                data = fn(i.filename, zi.read(i.filename))
                if data is not None: zo.writestr(i.filename, data)
        return bad

    def test_tampered_file_detected(self):
        package.export(self.src, self.pkg)
        bad = self._rewrite(lambda n, b: b[:-1] + bytes([b[-1] ^ 1]) if n == "stems/export/a.wav" else b)
        with self.assertRaises(package.PackageError) as cm: package.import_package(bad, self.dst_songs)
        self.assertIn("stems/export/a.wav", str(cm.exception))
        with self.assertRaises(package.PackageError): package.verify(bad)
        self.assertFalse(os.path.exists(os.path.join(self.dst_songs, "tiny-song")))
        self.assertEqual([x for x in os.listdir(self.dst_songs) if x.startswith(".import")], [])
        extra = self._rewrite(lambda n, b: b)
        with zipfile.ZipFile(extra, "a") as z: z.writestr("evil.txt", "x")
        with self.assertRaises(package.PackageError): package.import_package(extra, self.dst_songs)
        missing = self._rewrite(lambda n, b: None if n == "midi/a.json" else b)
        with self.assertRaises(package.PackageError): package.import_package(missing, self.dst_songs)

    def test_unsafe_paths_and_garbage(self):
        package.export(self.src, self.pkg)
        def evil(n, b):
            if n != "manifest.json": return b
            m = json.loads(b); m["files"].append({"path": "../escape.txt", "size": 1, "sha256": "0" * 64}); return json.dumps(m)
        with self.assertRaises(package.PackageError): package.import_package(self._rewrite(evil), self.dst_songs)
        junk = os.path.join(self.d, "junk.llsong"); open(junk, "w").write("not a zip")
        with self.assertRaises(package.PackageError): package.import_package(junk, self.dst_songs)
        def future(n, b):
            if n != "manifest.json": return b
            m = json.loads(b); m["format"] = 99; return json.dumps(m)
        with self.assertRaises(package.PackageError): package.read_manifest(self._rewrite(future))

    def test_overwrite_protection_keeps_lineups(self):
        package.export(self.src, self.pkg)
        package.import_package(self.pkg, self.dst_songs)
        sp = os.path.join(self.dst_songs, "tiny-song", "song.json")
        s = json.load(open(sp)); s["lineups"]["bandmate-only"] = {"name": "Mine", "assignments": {}}
        s["lineups"]["duo"] = {"name": "Duo (local edit)", "assignments": {}}
        json.dump(s, open(sp, "w"))
        with self.assertRaises(package.SongExists): package.import_package(self.pkg, self.dst_songs)
        self.assertIn("bandmate-only", json.load(open(sp))["lineups"])  # untouched
        r = package.import_package(self.pkg, self.dst_songs, force=True)
        self.assertTrue(r["replaced"]); self.assertEqual(r["kept_lineups"], ["bandmate-only"])
        s = json.load(open(sp))
        self.assertEqual(s["lineups"]["duo"]["name"], "Duo")  # the package wins for the same id
        self.assertIn("bandmate-only", s["lineups"])
        self.assertEqual(sorted(os.listdir(self.dst_songs)), ["tiny-song"])

    def test_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf): cli(["package", "export", self.src, self.pkg])
        self.assertIn("files", buf.getvalue())
        root = os.path.join(self.d, "b")
        with redirect_stdout(buf): cli(["package", "import", self.pkg, "--root", root, "--slug", "via-cli"])
        self.assertTrue(os.path.isfile(os.path.join(root, "songs", "via-cli", "song.json")))
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()) as err, self.assertRaises(SystemExit) as cm:
            cli(["package", "import", self.pkg, "--root", root, "--slug", "via-cli"])
        self.assertEqual(cm.exception.code, 1); self.assertIn("--force", err.getvalue())


class Doctor(unittest.TestCase):
    def test_report_shape(self):
        r = doctor.report()
        ids = {c["id"] for c in r["checks"]}
        for i in ("python", "pkg-numpy", "live", "analoglab", "logic", "abletonosc"): self.assertIn(i, ids)
        for c in r["checks"]:
            self.assertEqual(set(c), {"id", "label", "ok", "critical", "detail", "fix"})
            if c["ok"]: self.assertEqual(c["fix"], "")
        self.assertEqual(r["ok"], all(c["ok"] for c in r["checks"] if c["critical"]))

    def test_no_live(self):
        with mock.patch.dict(os.environ, {"LIVE_APP": "/nonexistent/Ableton Live 12 Suite.app"}):
            liveapp._cached.clear()
            try:
                r = doctor.report()
            finally:
                liveapp._cached.clear()
        live = next(c for c in r["checks"] if c["id"] == "live")
        self.assertFalse(live["ok"]); self.assertFalse(r["ok"]); self.assertIn("Install Ableton Live 12 Suite", live["fix"])
        with redirect_stdout(io.StringIO()) as out, mock.patch.dict(os.environ, {"LIVE_APP": "/nonexistent/X.app"}):
            liveapp._cached.clear()
            try:
                code = doctor.main()
            finally:
                liveapp._cached.clear()
        self.assertEqual(code, 1); self.assertIn("✗ Ableton Live 12", out.getvalue())


class UiRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="lls-pkg-ui-")
        cls.root = os.path.join(cls.tmp, "root"); cls.downloads = os.path.join(cls.tmp, "Downloads")
        _song(cls.root, "tiny-song")
        cls.revealed = os.path.join(cls.tmp, "revealed")
        cls.srv = server.make_server(cls.root, 0, quiet=True, open_cmd=["true"], downloads_dir=cls.downloads,
                                     reveal_cmd=["sh", "-c", f'echo "$0" > "{cls.revealed}"'])
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + "/api" + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp: return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait(self, jid):
        import time
        for _ in range(200):
            st, j = self.req("GET", f"/jobs/{jid}")
            if j["state"] != "running": return j
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_doctor(self):
        st, r = self.req("GET", "/doctor")
        self.assertEqual(st, 200); self.assertIn("checks", r); self.assertIn("ok", r)

    def test_export_then_import(self):
        st, r = self.req("POST", "/songs/tiny-song/package", {"reveal": True})
        self.assertEqual(st, 202)
        j = self.wait(r["job"]); self.assertEqual(j["state"], "done", j)
        path = j["result"]["path"]
        self.assertEqual(path, os.path.join(self.downloads, "tiny-song.llsong")); self.assertTrue(os.path.isfile(path))
        import time
        for _ in range(50):
            if os.path.exists(self.revealed): break
            time.sleep(0.05)
        self.assertEqual(open(self.revealed).read().strip(), path)
        st, r = self.req("POST", "/package/import", {"path": path})
        self.assertEqual(st, 409); self.assertTrue(r["exists"]); self.assertIn("already exists", r["error"])
        st, r = self.req("POST", "/package/import", {"path": path, "slug": "tiny-copy"})
        self.assertEqual(st, 202); j = self.wait(r["job"]); self.assertEqual(j["state"], "done", j)
        self.assertEqual(j["result"]["slug"], "tiny-copy")
        st, r = self.req("POST", "/package/import", {"path": path, "force": True})
        self.assertEqual(st, 202); j = self.wait(r["job"]); self.assertTrue(j["result"]["replaced"])
        self.assertEqual(self.req("POST", "/package/import", {"path": os.path.join(self.tmp, "nope.llsong")})[0], 404)


if __name__ == "__main__":
    unittest.main()
