"""Local web UI for editing songs, lineups and the band, and building Live sets.

    python -m lls ui [--port 8765] [--root .]

Binds 127.0.0.1 only. Stdlib only (numpy is used for audio activity if it's installed).

API (all JSON):
  GET  /api/songs                         -> [{slug, title, tempo, sections, parts, lineups}]
  GET  /api/songs/<slug>                  -> song.json
  PUT  /api/songs/<slug>                  <- song.json  (shape-checked, atomic write, keeps song.json.bak)
  GET  /api/songs/<slug>/activity         -> per-part, per-bar activity (MIDI notes / audio stem level)
  POST /api/songs/<slug>/validate         <- {lineup_id, song?, band?} -> {issues: [...]}
  POST /api/songs/<slug>/suggest          <- {performers: [ids], song?, band?} -> {lineup: {...}}
  POST /api/songs/<slug>/build            <- {lineup_id} -> {als, report}
  GET  /api/band                          -> band.json
  PUT  /api/band                          <- band.json
  POST /api/open-in-live                  <- {als}  (opens a built set in Live; explicit click only)
validate/suggest/build need lls.lineup; without it they answer 501.
"""
import importlib, inspect, json, os, re, shutil, subprocess, sys, tempfile, threading, traceback, wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TREATMENTS = {"live-vocal", "backing-audio", "audio-loop", "midi-clip", "live-midi", "live-looped",
              "live-drums", "live-guitar", "click", "dropped"}
RIG_KINDS = {"keys", "drums", "guitar", "bass", "vocal", "grid"}
LIVE_APP = "Ableton Live 12 Beta"
MAX_BODY = 8 * 1024 * 1024
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json"}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message); self.status = status; self.message = message


# ---------- shape checks (light: catch UI bugs before they hit disk) ----------

def _num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_song(s):
    if not isinstance(s, dict): raise ApiError(400, "song must be an object")
    for k, t in (("title", str), ("sections", list), ("parts", list)):
        if not isinstance(s.get(k), t): raise ApiError(400, f"song.{k} missing or not a {t.__name__}")
    if not _num(s.get("tempo")) or s["tempo"] <= 0: raise ApiError(400, "song.tempo must be a positive number")
    ids, prev_end = set(), None
    for i, sec in enumerate(s["sections"]):
        if not isinstance(sec, dict) or not isinstance(sec.get("id"), str) or not isinstance(sec.get("name"), str):
            raise ApiError(400, f"sections[{i}] needs string id and name")
        if not (_num(sec.get("start")) and _num(sec.get("end")) and sec["start"] < sec["end"]):
            raise ApiError(400, f"section {sec['id']}: start must be < end")
        if sec["id"] in ids: raise ApiError(400, f"duplicate section id {sec['id']}")
        if prev_end is not None and sec["start"] < prev_end - 1e-6:
            raise ApiError(400, f"section {sec['id']} overlaps the previous one")
        ids.add(sec["id"]); prev_end = sec["end"]
    pids = set()
    for i, p in enumerate(s["parts"]):
        if not isinstance(p, dict) or not isinstance(p.get("id"), str): raise ApiError(400, f"parts[{i}] needs a string id")
        if p["id"] in pids: raise ApiError(400, f"duplicate part id {p['id']}")
        if p.get("default_treatment") not in TREATMENTS:
            raise ApiError(400, f"part {p['id']}: unknown default_treatment {p.get('default_treatment')!r}")
        pids.add(p["id"])
    lineups = s.get("lineups", {})
    if not isinstance(lineups, dict): raise ApiError(400, "song.lineups must be an object")
    for lid, lu in lineups.items():
        if not SLUG.match(lid) or not isinstance(lu, dict): raise ApiError(400, f"bad lineup {lid!r}")
        for pid, a in (lu.get("assignments") or {}).items():
            if not isinstance(a, dict): raise ApiError(400, f"lineup {lid}: assignment for {pid} must be an object")
            if a.get("treatment") is not None and a["treatment"] not in TREATMENTS:
                raise ApiError(400, f"lineup {lid}: {pid} has unknown treatment {a['treatment']!r}")
            for sid, t in (a.get("sections") or {}).items():
                if t not in TREATMENTS: raise ApiError(400, f"lineup {lid}: {pid}/{sid} unknown treatment {t!r}")


def check_band(b):
    if not isinstance(b, dict) or not isinstance(b.get("performers"), list): raise ApiError(400, "band needs a performers list")
    if not isinstance(b.get("interface", {}), dict): raise ApiError(400, "band.interface must be an object")
    pids, rids = set(), set()
    for p in b["performers"]:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not SLUG.match(p["id"]):
            raise ApiError(400, f"performer id must be a kebab-case slug: {p.get('id') if isinstance(p, dict) else p!r}")
        if p["id"] in pids: raise ApiError(400, f"duplicate performer id {p['id']}")
        pids.add(p["id"])
        for r in p.get("rigs", []):
            if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not SLUG.match(r["id"]):
                raise ApiError(400, f"{p['id']}: rig id must be a kebab-case slug")
            if r["id"] in rids: raise ApiError(400, f"duplicate rig id {r['id']}")
            if r.get("kind") not in RIG_KINDS: raise ApiError(400, f"rig {r['id']}: kind must be one of {sorted(RIG_KINDS)}")
            ch = r.get("midi_channel")
            if ch is not None and not (isinstance(ch, int) and 1 <= ch <= 16): raise ApiError(400, f"rig {r['id']}: midi_channel 1-16 or null")
            rids.add(r["id"])


# ---------- files ----------

def read_json(path):
    with open(path, encoding="utf-8") as f: return json.load(f)


def write_json_atomic(path, data):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False); f.write("\n")
        if os.path.exists(path): shutil.copy2(path, path + ".bak")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp): os.unlink(tmp)
        raise


def lineup_module():
    try:
        return importlib.import_module("lls.lineup")
    except ModuleNotFoundError as e:
        if e.name in ("lls.lineup", "lls"): return None
        raise


def _jsonable(x):
    if isinstance(x, tuple): return [_jsonable(v) for v in x]
    if isinstance(x, list): return [_jsonable(v) for v in x]
    if isinstance(x, dict): return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (str, int, float, bool)) or x is None: return x
    return str(x)


# ---------- activity (what plays in each bar) ----------

_act_cache, _act_lock = {}, threading.Lock()


def _wav_levels(path, tempo, bpb, nbars):
    """Peak level per bar of a WAV file, or None if it can't be read."""
    try:
        with wave.open(path) as w:
            ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (OSError, wave.Error, EOFError):
        return None
    try:
        import numpy as np
    except ImportError:
        np = None
    fpb = bpb * 60.0 / tempo * sr  # frames per bar
    out = []
    if np is not None and sw in (2, 3, 4):
        if sw == 3:
            b = np.frombuffer(raw, np.uint8)[: (len(raw) // 3) * 3].reshape(-1, 3).astype(np.int32)
            a = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16); a = np.where(a & 0x800000, a - 0x1000000, a) / 8388608.0
        else:
            a = np.frombuffer(raw, "<i2" if sw == 2 else "<i4") / (32768.0 if sw == 2 else 2147483648.0)
        a = np.abs(a[: (len(a) // ch) * ch].reshape(-1, ch)).max(axis=1)
        for i in range(nbars):
            seg = a[int(i * fpb):int((i + 1) * fpb)]
            out.append(float(seg.max()) if seg.size else 0.0)
        return out
    if sw != 2: return None
    import array
    a = array.array("h"); a.frombytes(raw[: (len(raw) // 2) * 2])
    step = max(1, ch * 64)  # stride sampling keeps the pure-Python path quick
    for i in range(nbars):
        lo, hi = int(i * fpb) * ch, int((i + 1) * fpb) * ch
        seg = a[lo:hi:step]
        out.append(max((abs(v) for v in seg), default=0) / 32768.0)
    return out


def song_activity(song_dir, song):
    tempo = song["tempo"]; ts = song.get("time_signature") or [4, 4]
    bpb = ts[0] * 4 / ts[1]
    length = song.get("length_beats") or max([s["end"] for s in song["sections"]] + [bpb])
    nbars = int(-(-length // bpb))
    parts = {}
    for p in song["parts"]:
        src, path = None, None
        if p.get("kind") == "midi" and p.get("midi"): src, path = "midi", os.path.join(song_dir, p["midi"])
        else:
            for k in ("stem", "raw_stem"):
                if p.get(k) and os.path.exists(os.path.join(song_dir, p[k])): src, path = "audio", os.path.join(song_dir, p[k]); break
        if not path or not os.path.exists(path):
            parts[p["id"]] = {"source": "unknown", "bars": None}; continue
        key = (path, os.path.getmtime(path), tempo, bpb, nbars)
        with _act_lock: hit = _act_cache.get(key)
        if hit is None:
            if src == "midi":
                cov = [0.0] * nbars
                try:
                    notes = read_json(path).get("notes", [])
                except (OSError, ValueError):
                    notes = []
                for n in notes:
                    st, du = float(n[1]), max(float(n[2]), 0.01)
                    b0, b1 = int(st // bpb), int((st + du - 1e-6) // bpb)
                    for b in range(max(b0, 0), min(b1, nbars - 1) + 1):
                        cov[b] += min(st + du, (b + 1) * bpb) - max(st, b * bpb)
                peak = max(cov) or 1.0
                hit = [round(c / peak, 3) if c > 0 else 0 for c in cov]
            else:
                lv = _wav_levels(path, tempo, bpb, nbars)
                if lv is None: hit = None
                else:
                    peak = max(lv) or 1.0
                    hit = [round(v / peak, 3) if v > 1e-4 else 0 for v in lv]
            with _act_lock: _act_cache[key] = hit
        parts[p["id"]] = {"source": src if hit is not None else "unknown", "bars": hit}
    return {"bars": nbars, "beats_per_bar": bpb, "parts": parts}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "lls-ui/1"
    root = "."                      # repo root (songs/, band.json, out/)
    open_cmd = ["open", "-a", LIVE_APP]
    quiet = False

    def log_message(self, fmt, *args):
        if not self.quiet: sys.stderr.write("[ui] " + fmt % args + "\n")

    # paths
    @property
    def songs_dir(self): return os.path.join(self.root, "songs")
    @property
    def band_path(self): return os.path.join(self.root, "band.json")
    @property
    def out_root(self): return os.path.join(self.root, "out", "sets")

    def song_dir(self, slug):
        if not SLUG.match(slug): raise ApiError(404, f"bad song slug {slug!r}")
        d = os.path.join(self.songs_dir, slug)
        if not os.path.isfile(os.path.join(d, "song.json")): raise ApiError(404, f"no song {slug!r}")
        return d

    # responses
    def send_json(self, status, obj):
        body = json.dumps(_jsonable(obj), ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: raise ApiError(413, "request too large")
        raw = self.rfile.read(n) if n else b""
        if not raw: return {}
        try:
            return json.loads(raw)
        except ValueError as e:
            raise ApiError(400, f"invalid JSON: {e}")

    def local_only(self):
        # Refuse requests whose Host isn't loopback (DNS-rebinding guard for a local tool).
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"): raise ApiError(403, "local requests only")

    def dispatch(self, method):
        path = urlparse(self.path).path
        try:
            self.local_only()
            if path.startswith("/api/"):
                return self.api(method, [p for p in path[5:].split("/") if p])
            if method != "GET": raise ApiError(405, "method not allowed")
            return self.static(path)
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001 — report, don't kill the server
            traceback.print_exc()
            self.send_json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self): self.dispatch("GET")
    def do_PUT(self): self.dispatch("PUT")
    def do_POST(self): self.dispatch("POST")

    def static(self, path):
        rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        if rel.startswith("static/"): rel = rel[7:]
        full = os.path.realpath(os.path.join(STATIC, rel))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            raise ApiError(404, "not found")
        with open(full, "rb") as f: data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers(); self.wfile.write(data)

    # ---- API ----
    def api(self, method, seg):
        if method == "GET" and seg == ["songs"]: return self.send_json(200, self.list_songs())
        if seg[:1] == ["songs"] and len(seg) >= 2:
            d = self.song_dir(seg[1])
            sp = os.path.join(d, "song.json")
            if len(seg) == 2 and method == "GET": return self.send_json(200, read_json(sp))
            if len(seg) == 2 and method == "PUT":
                song = self.body(); check_song(song)
                try:  # protect hand-edited sections from being renamed/re-split by a later re-ingest
                    prev = json.load(open(sp))
                    key = lambda ss: [(x.get("name"), x.get("start"), x.get("end")) for x in ss]
                    if key(prev.get("sections", [])) != key(song.get("sections", [])): song["sections_edited"] = True
                except (OSError, ValueError):
                    pass
                write_json_atomic(sp, song)
                return self.send_json(200, {"ok": True})
            if len(seg) == 3 and method == "GET" and seg[2] == "activity":
                return self.send_json(200, song_activity(d, read_json(sp)))
            if len(seg) == 3 and method == "POST" and seg[2] in ("validate", "suggest", "build"):
                return getattr(self, "api_" + seg[2])(d, sp, self.body())
        if seg == ["band"] and method == "GET":
            return self.send_json(200, read_json(self.band_path) if os.path.exists(self.band_path)
                                  else {"schema": 1, "interface": {}, "performers": []})
        if seg == ["band"] and method == "PUT":
            band = self.body(); check_band(band); write_json_atomic(self.band_path, band)
            return self.send_json(200, {"ok": True})
        if seg == ["open-in-live"] and method == "POST": return self.api_open(self.body())
        raise ApiError(404, f"no route {method} /api/{'/'.join(seg)}")

    def list_songs(self):
        out = []
        if not os.path.isdir(self.songs_dir): return out
        for slug in sorted(os.listdir(self.songs_dir)):
            sp = os.path.join(self.songs_dir, slug, "song.json")
            if not (SLUG.match(slug) and os.path.isfile(sp)): continue
            try:
                s = read_json(sp)
                out.append({"slug": slug, "title": s.get("title", slug), "tempo": s.get("tempo"),
                            "sections": len(s.get("sections", [])), "parts": len(s.get("parts", [])),
                            "lineups": list((s.get("lineups") or {}).keys())})
            except (OSError, ValueError) as e:
                out.append({"slug": slug, "title": slug, "error": str(e)})
        return out

    def _need_lineup(self):
        m = lineup_module()
        if m is None:
            raise ApiError(501, "lls.lineup is not installed yet (the lineup engine is being written); "
                                "validate/suggest/build need it")
        return m

    def _band(self, body):
        band = body.get("band")
        if band is not None: check_band(band); return band
        m = lineup_module()
        if m is not None and hasattr(m, "load_band") and os.path.exists(self.band_path): return m.load_band(self.band_path)
        return read_json(self.band_path) if os.path.exists(self.band_path) else {"schema": 1, "performers": []}

    def _song(self, sp, body):
        song = body.get("song")
        if song is not None: check_song(song); return song
        return read_json(sp)

    def api_validate(self, d, sp, body):
        m = self._need_lineup()
        song, band = self._song(sp, body), self._band(body)
        lid = body.get("lineup_id") or "default"
        if lid not in (song.get("lineups") or {}): raise ApiError(400, f"no lineup {lid!r} in song")
        issues = m.validate(song, band, lid)
        return self.send_json(200, {"lineup_id": lid, "issues": issues})

    def api_suggest(self, d, sp, body):
        m = self._need_lineup()
        perf = body.get("performers")
        if not isinstance(perf, list) or not all(isinstance(p, str) for p in perf):
            raise ApiError(400, "performers must be a list of performer ids")
        lineup = m.suggest(self._song(sp, body), self._band(body), perf)
        return self.send_json(200, {"lineup": lineup})

    def api_build(self, d, sp, body):
        self._need_lineup()
        lid = body.get("lineup_id") or "default"
        song = read_json(sp)
        if lid not in (song.get("lineups") or {}): raise ApiError(400, f"no lineup {lid!r} in song (save first?)")
        from lls import build as build_mod
        fn = build_mod.build
        kw = {"out_root": self.out_root}
        try:
            if "band_path" in inspect.signature(fn).parameters and os.path.exists(self.band_path): kw["band_path"] = self.band_path
        except (TypeError, ValueError):
            pass
        res = fn(d, lid, **kw)
        als, report = (res if isinstance(res, tuple) and len(res) == 2 else (None, res))
        if isinstance(res, dict): als, report = res.get("als"), res.get("report", res)
        acc = None
        if als:
            p = os.path.join(os.path.dirname(os.path.dirname(als)), "accounted-for.md")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f: acc = f.read()
        return self.send_json(200, {"als": als, "report": report, "accounted_for": acc})

    def api_open(self, body):
        als = body.get("als")
        if not isinstance(als, str) or not als.endswith(".als"): raise ApiError(400, "als must be a path to a .als file")
        full = os.path.realpath(als)
        root = os.path.realpath(self.out_root)
        if not full.startswith(root + os.sep): raise ApiError(403, "only sets built under out/sets can be opened")
        if not os.path.isfile(full): raise ApiError(404, "set not found (build it first)")
        subprocess.Popen(list(self.open_cmd) + [full], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self.send_json(200, {"ok": True, "opened": full})


def make_server(root=".", port=8765, host="127.0.0.1", quiet=False, open_cmd=None):
    handler = type("LlsHandler", (Handler,), {"root": os.path.abspath(root), "quiet": quiet,
                                              **({"open_cmd": open_cmd} if open_cmd else {})})
    return ThreadingHTTPServer((host, port), handler)


def main(argv):
    port, root = 8765, "."
    it = iter(argv)
    for a in it:
        if a == "--port": port = int(next(it))
        elif a == "--root": root = next(it)
        else: raise SystemExit(f"unknown option {a}\nusage: python -m lls ui [--port 8765] [--root DIR]")
    srv = make_server(root, port)
    print(f"lls ui → http://127.0.0.1:{srv.server_address[1]}/   (root {os.path.abspath(root)}; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
