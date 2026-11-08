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
  GET  /api/songs/<slug>/stem/<part|_mix>[?pcm16=1]  -> WAV with HTTP Range; pcm16 = 16-bit copy cached in stems/preview/
  GET  /api/songs/<slug>/midi/<part>      -> {notes: [[pitch, start_beat, dur_beats, vel], ...]}  (browser synth preview)
  GET  /api/songs/<slug>/cues.wav[?h=HASH]-> spoken section cues (lls.build.cues_wav), 16-bit mono, HTTP Range; cached as
                                             stems/preview/cues-<hash>.wav (hash: tempo, sections, voice settings). With ?h=
                                             it only serves an already-rendered file.
  POST /api/songs/<slug>/cues.wav         <- {song?} (unsaved edits) -> {hash, url, cues: [[section_id, start_s, end_s]]}
  POST /api/pick                          <- {kind: logicx|folder} -> {path} | {cancelled: true}  (native macOS dialog)
  POST /api/import                        <- {logicx?, slug, export_dir?} -> {job}  (APFS clone -> ingest -> link-stems)
  GET  /api/jobs/<id>?since=N             -> {state, step, lines (from N), next, result, error}
  GET  /api/setlists                      -> [{slug, name, songs}]
  GET|PUT|DELETE /api/setlists/<slug>     <-> setlists/<slug>.json
  POST /api/setlists/<slug>/build         -> {job}   (lls.setlist.build_setlist into out/setlists/)
  GET  /api/setlists/<slug>/running-order -> the built running-order.html
  POST /api/reveal                        <- {path}  (open -R; only paths under the root)
Live engine (AbletonOSC, lls.live_engine; the OSC reply port is bound on first use, one process at a time):
  GET  /api/live/status?slug=&lineup=     -> {connected, tempo, playing, song_beat, scene, fired, offset, scenes, tracks,
                                              loaded: {slug, title, lineup} | null, song: {scenes_match, lineup_match,
                                              parts: {part: [Live track names]}}, error?}
  POST /api/live/open                     <- {slug, lineup_id, force?} builds if stale, opens in Live, waits (20 s) for the
                                              set to load -> {state: loaded | waiting, message, als, built}
  POST /api/live/fire                     <- {slug, section_id} | {scene_index}  (launches that scene)
  POST /api/live/loop                     <- {slug, section_id, on}  (on: launch it; Session clips loop by themselves)
  POST /api/live/stop                     -> stops all clips and the transport
  POST /api/live/mix                      <- {slug, lineup_id, solo: [part], mute: [part]} | {tracks: {name: {mute, solo}}}
                                              -> {tracks: [{index, name, parts, mute, solo}], unmapped: [part]}
validate/suggest/build need lls.lineup; without it they answer 501.
"""
import hashlib, importlib, inspect, itertools, json, os, re, shutil, subprocess, sys, tempfile, threading, time, traceback, wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TREATMENTS = {"live-vocal", "backing-audio", "audio-loop", "midi-clip", "live-midi", "live-looped",
              "live-drums", "live-guitar", "click", "dropped"}
RIG_KINDS = {"keys", "drums", "guitar", "bass", "vocal", "grid"}
LIVE_APP = "Ableton Live 12 Beta"
MAX_BODY = 8 * 1024 * 1024
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
        ".wav": "audio/wav", ".aif": "audio/aiff", ".aiff": "audio/aiff"}


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


# ---------- background jobs (import, setlist build) ----------

class _ThreadStdout:
    """sys.stdout proxy: writes from a job thread go to that job's log, everything else to the real stdout."""
    def __init__(self, real): self.real, self.local = real, threading.local()
    def write(self, s):
        job = getattr(self.local, "job", None)
        if job is None: return self.real.write(s)
        job.feed(s); return len(s)
    def flush(self):
        if getattr(self.local, "job", None) is None: self.real.flush()
    def __getattr__(self, k): return getattr(self.real, k)


_router_lock = threading.Lock()


def _router():
    with _router_lock:
        if not isinstance(sys.stdout, _ThreadStdout): sys.stdout = _ThreadStdout(sys.stdout)
        return sys.stdout


class Job:
    _ids = itertools.count(1)

    def __init__(self, kind, key, fn):
        self.id, self.kind, self.key, self.fn = f"j{next(self._ids)}", kind, key, fn
        self.state, self.step, self.lines, self.result, self.error = "running", "", [], None, None
        self.started, self.ended, self._part, self._lock = time.time(), None, "", threading.Lock()

    def feed(self, s):
        with self._lock:
            buf = (self._part + s).replace("\r", "\n").split("\n")
            self._part = buf.pop()
            self.lines += [l for l in buf if l.strip()]

    def log(self, line): self.feed(line + "\n")

    def set_step(self, step): self.step = step; self.log(f"▸ {step}")

    def run(self):
        out = _router(); out.local.job = self
        try:
            self.result = self.fn(self); self.state = "done"
        except Exception as e:  # noqa: BLE001 — reported to the UI
            self.error = f"{type(e).__name__}: {e}"; self.state = "error"
            self.feed("".join(traceback.format_exception(e))[-4000:])
        finally:
            if self._part: self.feed("\n")
            out.local.job = None; self.ended = time.time()

    def view(self, since=0):
        with self._lock: lines = self.lines[since:]; n = len(self.lines)
        return {"id": self.id, "kind": self.kind, "key": self.key, "state": self.state, "step": self.step,
                "lines": lines, "next": n, "result": self.result if self.state == "done" else None, "error": self.error,
                "elapsed": round((self.ended or time.time()) - self.started, 1)}


class Jobs:
    def __init__(self): self.all, self.lock = {}, threading.Lock()

    def start(self, kind, key, fn):
        with self.lock:
            if any(j.key == key and j.state == "running" for j in self.all.values()):
                raise ApiError(409, f"a {kind} job for {key} is already running")
            j = Job(kind, key, fn); self.all[j.id] = j
        threading.Thread(target=j.run, daemon=True, name=f"lls-{j.id}").start()
        return j

    def get(self, jid):
        j = self.all.get(jid)
        if j is None: raise ApiError(404, f"no job {jid!r}")
        return j


# ---------- import (copy -> ingest -> link-stems) ----------

def clone_logicx(src, dst, log=print):
    """APFS clone of the .logicx package (cp -cR falls back to a plain copy off APFS). Only reads `src`."""
    if os.path.exists(dst): shutil.rmtree(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    r = subprocess.run(["cp", "-cR", src, dst], capture_output=True, text=True)
    if r.returncode != 0:
        log(f"cp -cR failed ({r.stderr.strip()}); copying instead")
        if os.path.exists(dst): shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)


def _classify_link(what):
    if what == "full mix": return "mix"
    if "NEW part" in what: return "new"
    if what.startswith("⚠"): return "missing"
    return "matched"


def import_summary(song, link_report=None):
    parts = song.get("parts", [])
    return {"title": song.get("title"), "tempo": song.get("tempo"), "time_signature": song.get("time_signature"),
            "length_beats": song.get("length_beats"), "mix": song.get("mix"),
            "sections": [{"id": s["id"], "name": s["name"], "start": s["start"], "end": s["end"]} for s in song.get("sections", [])],
            "parts": [{"id": p["id"], "name": p.get("name", p["id"]), "kind": p.get("kind"),
                       "instrument": (p.get("instrument") or {}).get("type"), "preset": (p.get("instrument") or {}).get("preset"),
                       "default_treatment": p.get("default_treatment"), "stem": p.get("stem"), "raw_stem": p.get("raw_stem"),
                       "origin": p.get("origin")} for p in parts],
            "link": None if link_report is None else [{"file": f, "what": w, "status": _classify_link(w)} for f, w in link_report],
            "ignored_tracks": song.get("ignored_tracks", []), "warnings": song.get("warnings", [])}


# ---------- audio preview ----------

def pcm16_preview(song_dir, src, rel):
    """16-bit copy of a stem for browsers that can't decode the original. Cached in stems/preview/."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", rel.replace("/", "__"))
    dst = os.path.join(song_dir, "stems", "preview", os.path.splitext(name)[0] + ".wav")
    if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src): return dst
    return _write_pcm16(src, dst)


def _write_pcm16(src, dst, mono=False):
    """WAV (8/16/24/32-bit int) -> 16-bit WAV at dst (atomic). mono=True averages the channels."""
    import numpy as np
    with wave.open(src) as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 3:
        b = np.frombuffer(raw, np.uint8)[: (len(raw) // 3) * 3].reshape(-1, 3)
        a = (b[:, 0].astype(np.int32) << 8 | b[:, 1].astype(np.int32) << 16 | b[:, 2].astype(np.int32) << 24) >> 16
        pcm = a.astype("<i2")
    elif sw == 4: pcm = (np.frombuffer(raw, "<i4") >> 16).astype("<i2")
    elif sw == 2: pcm = np.frombuffer(raw, "<i2")
    elif sw == 1: pcm = ((np.frombuffer(raw, np.uint8).astype(np.int16) - 128) << 8).astype("<i2")
    else: raise ApiError(415, f"unsupported sample width {sw}")
    if mono and ch > 1:
        pcm = pcm[: (len(pcm) // ch) * ch].reshape(-1, ch).astype(np.int32).mean(axis=1).astype("<i2"); ch = 1
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())
    os.replace(tmp, dst)
    return dst


CUES_SR = 22050      # speech only: a quarter of the bytes of 44.1k stereo
CUES_KEEP = 4        # rendered cue files kept per song (undo/redo flips between recent versions)
_cue_locks, _cue_locks_lock = {}, threading.Lock()
_spoken, _spoken_lock = {}, threading.Lock()   # (speak fn, voice settings, sr, text) -> audio: an edit re-speaks only what changed


def _speak_all(B, texts, sr):
    """Speak the unique cue texts in parallel (`say` is ~1 s each) and memoise them. Returns a speak(text, sr) for cues_wav."""
    fn = B._speak
    key = lambda t: (id(fn), tuple(B.CUE_VOICES), B.CUE_RATE, sr, t)
    with _spoken_lock: todo = [t for t in dict.fromkeys(texts) if key(t) not in _spoken]
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(6, len(todo))) as ex:
            for t, audio in zip(todo, ex.map(lambda t: fn(t, sr), todo)):
                with _spoken_lock:
                    if len(_spoken) > 512: _spoken.clear()
                    _spoken[key(t)] = audio
    with _spoken_lock: got = {t: _spoken[key(t)] for t in texts}
    return lambda text, _sr: got[text]


def cues_hash(song):
    """What the rendered cues depend on: timing, section names/starts/cue text, and the voice settings."""
    from lls import build as B
    ts = song.get("time_signature") or [4, 4]
    key = {"v": 1, "tempo": song["tempo"], "ts": ts, "sr": CUES_SR,
           "len": song.get("length_beats") or max([s["end"] for s in song["sections"]] + [0]),
           "sections": [[s["name"], s["start"], B.cue_text(s)] for s in song["sections"]],
           "voices": list(B.CUE_VOICES), "rate": B.CUE_RATE, "gain": B.CUE_GAIN}
    return hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]


def cues_preview(song_dir, song):
    """Render (or reuse) stems/preview/cues-<hash>.wav for this song. Returns (path, hash, placed)."""
    from lls import build as B
    h = cues_hash(song)
    pdir = os.path.join(song_dir, "stems", "preview")
    dst = os.path.join(pdir, f"cues-{h}.wav")
    with _cue_locks_lock: lock = _cue_locks.setdefault(dst, threading.Lock())
    with lock:
        if os.path.isfile(dst): return dst, h, None
        s = dict(song, sample_rate=CUES_SR, time_signature=song.get("time_signature") or [4, 4])
        if not s.get("length_beats"): s["length_beats"] = max([x["end"] for x in song["sections"]] + [0])
        os.makedirs(pdir, exist_ok=True)
        tmp = os.path.join(pdir, f".cues-{h}-24.wav")
        try:
            bpb = s["time_signature"][0] * 4 / s["time_signature"][1]
            texts = [B.cue_text(x) for x in s["sections"] if x["start"] - bpb >= 0]   # as cues_wav: none before bar 2
            placed = B.cues_wav(tmp, s, speak=_speak_all(B, texts, CUES_SR))
            _write_pcm16(tmp, dst, mono=True)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        old = sorted((f for f in os.listdir(pdir) if f.startswith("cues-") and f.endswith(".wav") and f != os.path.basename(dst)),
                     key=lambda f: os.path.getmtime(os.path.join(pdir, f)), reverse=True)
        for f in old[CUES_KEEP - 1:]:
            try: os.unlink(os.path.join(pdir, f))
            except OSError: pass
        return dst, h, placed


def parse_range(header, size):
    """'bytes=a-b' -> (start, end) inclusive, None for no/ignored range; raises ApiError(416) when unsatisfiable."""
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header or "")
    if not m or (not m.group(1) and not m.group(2)): return None
    a, b = m.group(1), m.group(2)
    if a == "": start, end = max(0, size - int(b)), size - 1
    else: start, end = int(a), min(int(b), size - 1) if b else size - 1
    if start >= size or start > end: raise ApiError(416, f"range not satisfiable (size {size})")
    return start, end


_live_cache, _live_cache_lock = {}, threading.Lock()   # (song dir, song.json mtime, band.json mtime) -> expected Live names


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "lls-ui/1"
    root = "."                      # repo root (songs/, band.json, out/)
    open_cmd = ["open", "-a", LIVE_APP]
    reveal_cmd = ["open", "-R"]
    osascript = ["osascript"]
    jobs = None                     # Jobs(), one per server
    quiet = False
    live_link = None                # None -> lls.live_engine.link() (process-wide OSC client); tests inject one
    live_open_timeout = 20.0        # POST /api/live/open: seconds to wait for Live to load the set

    def log_message(self, fmt, *args):
        if not self.quiet: sys.stderr.write("[ui] " + fmt % args + "\n")

    # paths
    @property
    def songs_dir(self): return os.path.join(self.root, "songs")
    @property
    def band_path(self): return os.path.join(self.root, "band.json")
    @property
    def out_root(self): return os.path.join(self.root, "out", "sets")
    @property
    def setlists_dir(self): return os.path.join(self.root, "setlists")
    @property
    def setlists_out(self): return os.path.join(self.root, "out", "setlists")

    def setlist_path(self, slug, must_exist=True):
        if not SLUG.match(slug): raise ApiError(404, f"bad setlist slug {slug!r}")
        p = os.path.join(self.setlists_dir, slug + ".json")
        if must_exist and not os.path.isfile(p): raise ApiError(404, f"no setlist {slug!r}")
        return p

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
        u = urlparse(self.path)
        path, self.query = u.path, {k: v[-1] for k, v in parse_qs(u.query).items()}
        try:
            self.local_only()
            if path.startswith("/api/"):
                return self.api(method, [unquote(p) for p in path[5:].split("/") if p])
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
    def do_DELETE(self): self.dispatch("DELETE")
    def do_HEAD(self): self.dispatch("HEAD")

    def send_file(self, full, ctype=None, ranged=False):
        size = os.path.getsize(full)
        rng = None
        if ranged:
            try:
                rng = parse_range(self.headers.get("Range"), size)
            except ApiError:
                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0"); self.end_headers(); return
        start, end = rng or (0, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype or MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(end - start + 1 if size else 0))
        if ranged: self.send_header("Accept-Ranges", "bytes")
        if rng: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Last-Modified", self.date_time_string(int(os.path.getmtime(full))))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command == "HEAD" or not size: return
        try:
            with open(full, "rb") as f:
                f.seek(start); left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk: break
                    self.wfile.write(chunk); left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser aborted (seek / reload)

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
            if len(seg) == 4 and method in ("GET", "HEAD") and seg[2] == "stem":
                return self.api_stem(d, read_json(sp), seg[3])
            if len(seg) == 4 and method == "GET" and seg[2] == "midi":
                return self.api_midi(d, read_json(sp), seg[3])
            if len(seg) == 3 and seg[2] == "cues.wav" and method in ("GET", "HEAD", "POST"):
                return self.api_cues(d, sp, method)
        if seg == ["jobs"] and method == "GET":
            return self.send_json(200, [j.view(len(j.lines)) for j in self.jobs.all.values()])
        if seg[:1] == ["jobs"] and len(seg) == 2 and method == "GET":
            try: since = max(0, int(self.query.get("since", 0)))
            except ValueError: since = 0
            return self.send_json(200, self.jobs.get(seg[1]).view(since))
        if seg == ["import"] and method == "POST": return self.api_import(self.body())
        if seg == ["pick"] and method == "POST": return self.api_pick(self.body())
        if seg == ["reveal"] and method == "POST": return self.api_reveal(self.body())
        if seg == ["setlists"] and method == "GET": return self.send_json(200, self.list_setlists())
        if seg[:1] == ["setlists"] and len(seg) >= 2:
            return self.api_setlist(method, seg[1], seg[2:])
        if seg == ["band"] and method == "GET":
            return self.send_json(200, read_json(self.band_path) if os.path.exists(self.band_path)
                                  else {"schema": 1, "interface": {}, "performers": []})
        if seg == ["band"] and method == "PUT":
            band = self.body(); check_band(band); write_json_atomic(self.band_path, band)
            return self.send_json(200, {"ok": True})
        if seg == ["open-in-live"] and method == "POST": return self.api_open(self.body())
        if seg[:1] == ["live"] and len(seg) == 2: return self.api_live(method, seg[1])
        raise ApiError(404, f"no route {method} /api/{'/'.join(seg)}")

    def list_songs(self):
        out = []
        if not os.path.isdir(self.songs_dir): return out
        for slug in sorted(os.listdir(self.songs_dir)):
            sp = os.path.join(self.songs_dir, slug, "song.json")
            if not (SLUG.match(slug) and os.path.isfile(sp)): continue
            try:
                s = read_json(sp)
                lus = s.get("lineups") or {}
                out.append({"slug": slug, "title": s.get("title", slug), "tempo": s.get("tempo"),
                            "sections": len(s.get("sections", [])), "parts": len(s.get("parts", [])),
                            "lineups": list(lus.keys()),
                            "lineup_names": {k: (v.get("name") if isinstance(v, dict) else None) or k for k, v in lus.items()}})
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
        als, report, acc = self._build(d, lid)
        return self.send_json(200, {"als": als, "report": report, "accounted_for": acc})

    def _build(self, d, lid):
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
        return als, report, acc

    def api_open(self, body):
        als = body.get("als")
        if not isinstance(als, str) or not als.endswith(".als"): raise ApiError(400, "als must be a path to a .als file")
        full = os.path.realpath(als)
        root = os.path.realpath(self.out_root)
        if not full.startswith(root + os.sep): raise ApiError(403, "only sets built under out/sets can be opened")
        if not os.path.isfile(full): raise ApiError(404, "set not found (build it first)")
        subprocess.Popen(list(self.open_cmd) + [full], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self.send_json(200, {"ok": True, "opened": full})

    # ---- Live engine (AbletonOSC; lls.live_engine) ----
    def _link(self):
        from lls import live_engine as LE
        return self.live_link or LE.link()

    def _snap(self, names_ttl=None):
        from lls import live_engine as LE
        try:
            return self._link().snapshot(names_ttl)
        except LE.LiveBusy as e:
            return {"connected": False, "busy": True, "error": e.strerror or str(e)}

    def _snap_connected(self, names_ttl=None):
        snap = self._snap(names_ttl)
        if snap.get("busy"): raise ApiError(409, snap["error"])
        if not snap.get("connected"): raise ApiError(503, "Live is not answering: is it running with AbletonOSC selected as a Control Surface?")
        return snap

    def _live_song(self, slug):
        """Expected scene names and, per lineup, track names of this song's built sets (cached on file mtimes)."""
        from lls import live_engine as LE
        LU = self._need_lineup()
        d = self.song_dir(slug); sp = os.path.join(d, "song.json")
        bm = os.path.getmtime(self.band_path) if os.path.exists(self.band_path) else 0
        key = (d, os.path.getmtime(sp), bm)
        with _live_cache_lock: hit = _live_cache.get(key)
        if hit is None:
            song = LU.load_song(d); band = self._band({})
            tracks = {}
            for lid in song.get("lineups") or {}:
                try: tracks[lid] = LE.expected_tracks(song, band, lid)
                except Exception as e:  # noqa: BLE001 — a broken lineup shouldn't hide the others
                    tracks[lid] = ([], {}); sys.stderr.write(f"[ui] live: lineup {lid}: {e}\n")
            hit = {"slug": slug, "dir": d, "song": song, "scenes": LE.expected_scenes(song), "tracks": tracks}
            with _live_cache_lock:
                if len(_live_cache) > 64: _live_cache.clear()
                _live_cache[key] = hit
        return hit

    def _live_loaded(self, snap):
        """Which built set is open in Live: the song whose scene names match, and its lineup whose track names match."""
        from lls import live_engine as LE
        if not snap.get("connected"): return None
        for s in self.list_songs():
            if s.get("error"): continue
            try: info = self._live_song(s["slug"])
            except ApiError: return None
            if info["scenes"] != snap["scenes"]: continue
            lid = next((l for l, (names, _) in info["tracks"].items() if LE.same_names(names, snap["tracks"])), None)
            return {"slug": s["slug"], "title": info["song"].get("title"), "lineup": lid}
        return None

    def _als_path(self, song, lid):
        t = song["title"]
        return os.path.join(self.out_root, t, lid, f"{t} Project", f"{t}.als")

    def api_live(self, method, what):
        from lls import live_engine as LE
        if what == "status" and method == "GET":
            snap = self._snap()
            slug, lid = self.query.get("slug"), self.query.get("lineup")
            if snap.get("connected"):
                snap["loaded"] = self._live_loaded(snap)
                if slug:
                    info = self._live_song(slug)
                    names, by_part = info["tracks"].get(lid, (None, {}))
                    have = set(snap["tracks"] or [])
                    snap["song"] = {"slug": slug, "lineup": lid, "scenes_match": info["scenes"] == snap["scenes"],
                                    "lineup_match": LE.same_names(names, snap["tracks"]),
                                    "parts": {pid: [n for n in ns if n in have] for pid, ns in by_part.items()}}
            return self.send_json(200, snap)
        if method != "POST": raise ApiError(405, "method not allowed")
        body = self.body()
        if what == "stop":
            self._snap_connected(); self._link().stop()
            return self.send_json(200, {"ok": True})
        if what in ("fire", "loop"):
            snap = self._snap_connected()
            if body.get("section_id") is not None:
                info = self._live_song(body.get("slug") or "")
                ids = [s["id"] for s in info["song"]["sections"]]
                if body["section_id"] not in ids: raise ApiError(400, f"no section {body['section_id']!r}")
                if info["scenes"] != snap["scenes"]:
                    raise ApiError(409, "Live has a different set open: open this song in Live first")
                idx = ids.index(body["section_id"])
            elif what == "fire" and isinstance(body.get("scene_index"), int) and not isinstance(body["scene_index"], bool):
                idx = body["scene_index"]
                if not 0 <= idx < len(snap["scenes"] or []): raise ApiError(400, f"scene_index out of range (Live has {len(snap['scenes'] or [])} scenes)")
            else:
                raise ApiError(400, "section_id (with slug) or scene_index required")
            link = self._link()
            if what == "loop" and not body.get("on", True):
                link.loop = None
                return self.send_json(200, {"ok": True, "loop": None})
            link.fire(idx)
            if what == "loop": link.loop = {"slug": body.get("slug"), "section_id": body.get("section_id"), "scene": idx}
            elif link.loop and link.loop["scene"] != idx: link.loop = None
            return self.send_json(200, {"ok": True, "scene": idx, "loop": link.loop})
        if what == "mix":
            snap = self._snap_connected()
            if isinstance(body.get("tracks"), dict):
                want = body["tracks"]
                plan = [{"index": i, "name": n, "parts": [], "mute": bool((want[n] or {}).get("mute")), "solo": bool((want[n] or {}).get("solo"))}
                        for i, n in enumerate(snap["tracks"]) if n in want]
                unmapped = [n for n in want if n not in snap["tracks"]]
            else:
                info = self._live_song(body.get("slug") or "")
                lid = body.get("lineup_id") or "default"
                if lid not in info["tracks"]: raise ApiError(400, f"no lineup {lid!r} in song")
                solo, mute = body.get("solo") or [], body.get("mute") or []
                if not all(isinstance(x, str) for x in list(solo) + list(mute)): raise ApiError(400, "solo/mute must be lists of part ids")
                plan, unmapped = LE.mix_plan(snap["tracks"], info["tracks"][lid][1], solo, mute)
            self._link().set_mix(plan)
            return self.send_json(200, {"tracks": plan, "unmapped": unmapped})
        if what == "open":
            info = self._live_song(body.get("slug") or "")
            song, lid = info["song"], body.get("lineup_id") or "default"
            if lid not in info["tracks"]: raise ApiError(400, f"no lineup {lid!r} in song (save first?)")
            ok = lambda s: s.get("connected") and s["scenes"] == info["scenes"] and LE.same_names(info["tracks"][lid][0], s["tracks"])
            snap = self._snap(0)
            if snap.get("busy"): raise ApiError(409, snap["error"])
            als = self._als_path(song, lid)
            if ok(snap) and not body.get("force"):
                return self.send_json(200, {"state": "loaded", "already": True, "als": als, "built": False,
                                            "message": "This set is already open in Live"})
            newest = max([os.path.getmtime(os.path.join(info["dir"], "song.json"))]
                         + ([os.path.getmtime(self.band_path)] if os.path.exists(self.band_path) else []))
            built = not os.path.isfile(als) or os.path.getmtime(als) < newest
            if built: als = self._build(info["dir"], lid)[0] or als
            if not os.path.isfile(als): raise ApiError(500, f"build did not produce {als}")
            subprocess.Popen(list(self.open_cmd) + [als], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            end = time.time() + self.live_open_timeout
            while time.time() < end:
                time.sleep(min(0.5, self.live_open_timeout / 10))
                if ok(self._snap(0)):
                    return self.send_json(200, {"state": "loaded", "als": als, "built": built, "message": "Open in Live"})
            return self.send_json(202, {"state": "waiting", "als": als, "built": built,
                                        "message": "Waiting for Live (check for a save dialog)"})
        raise ApiError(404, f"no route {method} /api/live/{what}")

    # ---- preview audio ----
    def api_stem(self, d, song, pid):
        if pid == "_mix": rel = song.get("mix")
        else:
            part = next((p for p in song.get("parts", []) if p.get("id") == pid), None)
            if part is None: raise ApiError(404, f"no part {pid!r}")
            rel = part.get("stem") or part.get("raw_stem")
        if not isinstance(rel, str) or not rel: raise ApiError(404, f"{pid}: no stem")
        rel = os.path.normpath(rel)
        if os.path.isabs(rel) or rel.startswith(".."): raise ApiError(403, "stem path must stay inside the song folder")
        full = os.path.join(d, rel)
        if os.path.splitext(full)[1].lower() not in (".wav", ".aif", ".aiff"): raise ApiError(415, "not an audio file")
        if not os.path.isfile(full): raise ApiError(404, f"{pid}: stem file missing ({rel}); run link-stems")
        if self.query.get("pcm16") and full.lower().endswith(".wav"): full = pcm16_preview(d, full, rel)
        return self.send_file(full, ranged=True)

    def api_midi(self, d, song, pid):
        part = next((p for p in song.get("parts", []) if p.get("id") == pid), None)
        if part is None: raise ApiError(404, f"no part {pid!r}")
        rel = part.get("midi")
        if not isinstance(rel, str) or not rel: raise ApiError(404, f"{pid}: no MIDI")
        rel = os.path.normpath(rel)
        if os.path.isabs(rel) or rel.startswith(".."): raise ApiError(403, "midi path must stay inside the song folder")
        full = os.path.join(d, rel)
        if not os.path.isfile(full): raise ApiError(404, f"{pid}: MIDI file missing ({rel})")
        notes = read_json(full).get("notes", [])
        return self.send_json(200, {"part": pid, "notes": notes})

    def api_cues(self, d, sp, method):
        if method == "POST":
            body = self.body()
            song = body.get("song") or read_json(sp)
            if body.get("song") is not None: check_song(song)
            if not song.get("sections"): raise ApiError(400, "song has no sections")
            path, h, placed = cues_preview(d, song)
            return self.send_json(200, {"hash": h, "url": f"/api/songs/{os.path.basename(d)}/cues.wav?h={h}",
                                        "cues": None if placed is None else [[song["sections"][i]["id"], a, b] for i, a, b in placed]})
        h = self.query.get("h")
        if h is not None:
            if not re.fullmatch(r"[0-9a-f]{12}", h): raise ApiError(400, "bad cue hash")
            path = os.path.join(d, "stems", "preview", f"cues-{h}.wav")
            if not os.path.isfile(path): raise ApiError(404, "cues not rendered (POST first)")
        else:
            song = read_json(sp)
            if not song.get("sections"): raise ApiError(400, "song has no sections")
            path = cues_preview(d, song)[0]
        return self.send_file(path, "audio/wav", ranged=True)

    # ---- native pickers / Finder ----
    def _osascript(self, script):
        r = subprocess.run(list(self.osascript) + ["-e", script], capture_output=True, text=True, timeout=600)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def api_pick(self, body):
        kind = body.get("kind")
        prompts = {"logicx": "Choose a Logic project (.logicx)", "folder": "Choose the folder with Logic's exported stems"}
        if kind not in prompts: raise ApiError(400, "kind must be logicx or folder")
        if kind == "folder":
            tries = [f'POSIX path of (choose folder with prompt "{prompts[kind]}")']
        else:  # .logicx is a package: typed choose file first, then untyped choose file / choose folder
            tries = [f'POSIX path of (choose file of type {{"com.apple.logic-project", "public.folder"}} with prompt "{prompts[kind]}")',
                     f'POSIX path of (choose file with prompt "{prompts[kind]}")',
                     f'POSIX path of (choose folder with prompt "{prompts[kind]}")']
        err = ""
        for script in tries:
            code, out, err = self._osascript(script)
            if code == 0 and out:
                path = out.rstrip("/") if out != "/" else out
                if kind == "logicx" and not path.lower().endswith(".logicx"):
                    raise ApiError(400, f"not a Logic project (.logicx): {path}")
                return self.send_json(200, {"path": path})
            if "-128" in err or "User canceled" in err: return self.send_json(200, {"cancelled": True})
        raise ApiError(500, f"file dialog failed: {err or 'no path returned'}")

    def api_reveal(self, body):
        p = body.get("path")
        if not isinstance(p, str) or not p: raise ApiError(400, "path required")
        full = os.path.realpath(p if os.path.isabs(p) else os.path.join(self.root, p))
        if not (full == os.path.realpath(self.root) or full.startswith(os.path.realpath(self.root) + os.sep)):
            raise ApiError(403, "only paths inside the project can be revealed")
        if not os.path.exists(full): raise ApiError(404, "path not found")
        subprocess.Popen(list(self.reveal_cmd) + [full], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self.send_json(200, {"ok": True, "revealed": full})

    # ---- import ----
    def api_import(self, body):
        slug, logicx, export_dir = body.get("slug"), body.get("logicx") or None, body.get("export_dir") or None
        reimport = bool(body.get("reimport"))
        if not isinstance(slug, str) or not SLUG.match(slug): raise ApiError(400, "slug must be kebab-case (a-z, 0-9, -)")
        song_dir = os.path.join(self.songs_dir, slug)
        exists = os.path.isfile(os.path.join(song_dir, "song.json"))
        if exists and not reimport: raise ApiError(409, f"song {slug!r} already exists; use Re-import on it instead")
        if logicx is not None:
            if not isinstance(logicx, str): raise ApiError(400, "logicx must be a path")
            logicx = os.path.abspath(os.path.expanduser(logicx.rstrip("/")))
            if not logicx.lower().endswith(".logicx"): raise ApiError(400, "the Logic project must be a .logicx")
            if not os.path.isdir(logicx): raise ApiError(400, f"Logic project not found: {logicx}")
        elif not exists: raise ApiError(400, "choose a Logic project (.logicx)")
        if export_dir is not None:
            if not isinstance(export_dir, str): raise ApiError(400, "export_dir must be a path")
            export_dir = os.path.abspath(os.path.expanduser(export_dir))
            if not os.path.isdir(export_dir): raise ApiError(400, f"export folder not found: {export_dir}")
        if logicx is None and export_dir is None: raise ApiError(400, "nothing to do: choose a Logic project and/or an export folder")
        copy = os.path.join(self.root, "samples", slug + ".logicx")

        def run(job):
            song = None
            if logicx:
                if os.path.realpath(logicx) == os.path.realpath(copy):
                    job.set_step("Using the existing working copy"); job.log(copy)
                else:
                    job.set_step("Cloning the project (APFS copy; the original is only read)")
                    job.log(f"{logicx}\n  → {copy}")
                    clone_logicx(logicx, copy, job.log)
                job.set_step("Ingesting the copy")
                song = importlib.import_module("lls.ingest").ingest(copy, song_dir)
                src = song.setdefault("source", {})
                if os.path.realpath(logicx) != os.path.realpath(copy):
                    src["original"] = logicx
                    # the parser falls back to the file name, which is now the slug: keep the project's real name
                    if song.get("title") == slug: song["title"] = os.path.splitext(os.path.basename(logicx))[0]
                src["logicx"] = copy
                if export_dir: src["export_dir"] = export_dir
                write_json_atomic(os.path.join(song_dir, "song.json"), song)
                job.log(f"{song['title']}: {len(song['parts'])} parts, {len(song['sections'])} sections")
            report = None
            if export_dir:
                job.set_step("Linking exported stems")
                report = importlib.import_module("lls.stems").link(song_dir, export_dir)
                for f, what in report: job.log(f"  {f}  {what}")
                if not logicx:
                    s = read_json(os.path.join(song_dir, "song.json"))
                    s.setdefault("source", {})["export_dir"] = export_dir
                    write_json_atomic(os.path.join(song_dir, "song.json"), s)
            job.set_step("Done")
            return {"slug": slug, "song_dir": song_dir, "copy": copy if logicx else None,
                    **import_summary(read_json(os.path.join(song_dir, "song.json")), report)}

        job = self.jobs.start("import", "song:" + slug, run)
        return self.send_json(202, {"job": job.id})

    # ---- setlists ----
    def list_setlists(self):
        out = []
        if not os.path.isdir(self.setlists_dir): return out
        for f in sorted(os.listdir(self.setlists_dir)):
            slug, ext = os.path.splitext(f)
            if ext != ".json" or not SLUG.match(slug): continue
            try:
                sl = read_json(os.path.join(self.setlists_dir, f))
                built = os.path.join(self.setlists_out, _safe_name(sl.get("name") or slug))
                out.append({"slug": slug, "name": sl.get("name") or slug, "songs": len(sl.get("songs") or []),
                            "built": os.path.isfile(os.path.join(built, "running-order.html"))})
            except (OSError, ValueError) as e:
                out.append({"slug": slug, "name": slug, "error": str(e)})
        return out

    def check_setlist(self, sl):
        if not isinstance(sl, dict): raise ApiError(400, "setlist must be an object")
        if not isinstance(sl.get("name"), str) or not sl["name"].strip(): raise ApiError(400, "setlist needs a name")
        if not isinstance(sl.get("songs"), list): raise ApiError(400, "setlist.songs must be a list")
        if "band" in sl and not isinstance(sl["band"], str): raise ApiError(400, "setlist.band must be a path string")
        for i, e in enumerate(sl["songs"]):
            if not isinstance(e, dict) or not isinstance(e.get("song"), str) or not SLUG.match(e["song"]):
                raise ApiError(400, f"songs[{i}] needs a song slug")
            if not os.path.isfile(os.path.join(self.songs_dir, e["song"], "song.json")):
                raise ApiError(400, f"songs[{i}]: no song {e['song']!r}")
            if "lineup" in e and not (isinstance(e["lineup"], str) and e["lineup"]): raise ApiError(400, f"songs[{i}]: bad lineup")
            if "notes" in e and not isinstance(e["notes"], str): raise ApiError(400, f"songs[{i}]: notes must be text")

    def built_setlist_dir(self, sl, slug):
        return os.path.join(self.setlists_out, _safe_name(sl.get("name") or slug))

    def api_setlist(self, method, slug, rest):
        if not rest and method == "GET": return self.send_json(200, read_json(self.setlist_path(slug)))
        if not rest and method == "PUT":
            sl = self.body(); self.check_setlist(sl)
            os.makedirs(self.setlists_dir, exist_ok=True)
            write_json_atomic(self.setlist_path(slug, must_exist=False), sl)
            return self.send_json(200, {"ok": True})
        if not rest and method == "DELETE":
            os.remove(self.setlist_path(slug))
            return self.send_json(200, {"ok": True})
        if rest == ["build"] and method == "POST":
            path = self.setlist_path(slug); sl = read_json(path)
            if not sl.get("songs"): raise ApiError(400, "add songs to the setlist first")
            root, out = self.root, self.setlists_out

            def run(job):
                job.set_step(f"Building {len(sl['songs'])} song(s): Live sets, running order, phone fallback")
                r = importlib.import_module("lls.setlist").build_setlist(path, out_root=out, root=root)
                for p in r.get("projects", []): job.log(f"  {p}")
                rep = os.path.join(r["dir"], "fallback", "fallback-report.md")
                fb_md = None
                if os.path.isfile(rep):
                    with open(rep, encoding="utf-8") as f: fb_md = f.read()
                job.set_step("Done")
                return {"dir": r["dir"], "projects": r.get("projects", []), "running_order": r.get("running_order"),
                        "running_order_html": f"/api/setlists/{slug}/running-order",
                        "fallback": r.get("fallback", []),
                        "fallback_report": fb_md}

            job = self.jobs.start("setlist", "setlist:" + slug, run)
            return self.send_json(202, {"job": job.id})
        if rest == ["running-order"] and method == "GET":
            sl = read_json(self.setlist_path(slug))
            f = os.path.join(self.built_setlist_dir(sl, slug), "running-order.html")
            if not os.path.isfile(f): raise ApiError(404, "not built yet: click Build show")
            return self.send_file(f, "text/html; charset=utf-8")
        raise ApiError(404, f"no route {method} /api/setlists/{slug}/{'/'.join(rest)}")


def _safe_name(s):
    return re.sub(r'[/\\:*?"<>|]+', "-", s).strip()  # same as lls.setlist.safe


def make_server(root=".", port=8765, host="127.0.0.1", quiet=False, open_cmd=None, reveal_cmd=None, osascript=None,
                live_link=None, live_open_timeout=None):
    handler = type("LlsHandler", (Handler,), {"root": os.path.abspath(root), "quiet": quiet, "jobs": Jobs(),
                                              **({"live_link": live_link} if live_link else {}),
                                              **({"live_open_timeout": live_open_timeout} if live_open_timeout else {}),
                                              **({"open_cmd": open_cmd} if open_cmd else {}),
                                              **({"reveal_cmd": reveal_cmd} if reveal_cmd else {}),
                                              **({"osascript": osascript} if osascript else {})})
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
