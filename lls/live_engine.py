"""Live engine for the UI: drive a built set in a running Ableton Live through AbletonOSC (lls.live_osc).

Pure helpers (no Live needed):
  expected_scenes(song)               scene names lls.build writes: "<section> (bar N)" per section, then "STOP"
  expected_tracks(song, band, lineup) track names lls.build writes, in order, plus {part id: [track names]}
  mix_plan(...)                       UI solo/mute per part -> mute/solo per Live track index
LiveLink: one OSC client per process (only one process can bind AbletonOSC's reply port), with a pipelined status
snapshot (AbletonOSC answers on Live's UI tick, ~100 ms, so everything is asked in one round).
"""
import threading, time
from collections import Counter

from . import lineup as LU
from .live_osc import Live, LiveBusy  # noqa: F401  (LiveBusy re-exported for the server)

AUDIO_T, MIDI_T, KEYS_T, VOX_T = {"backing-audio", "audio-loop"}, {"midi-clip"}, {"live-midi", "live-looped"}, {"live-vocal", "live-guitar"}


def _bpb(song):
    ts = song.get("time_signature") or [4, 4]
    return ts[0] * 4 / ts[1]


def expected_scenes(song):
    bpb = _bpb(song)
    return [f"{s['name']} (bar {int(s['start'] // bpb) + 1})" for s in song["sections"]] + ["STOP"]


def expected_tracks(song, band, lineup_id):
    """Mirror of lls.build's track naming (kept in step by tests/test_live_engine.py, which builds a set and compares).
    song should come from lineup.load_song (has_stem checks files under song["_dir"]).
    Returns (ordered track names, {part id: [track names that part plays on]})."""
    res = LU.resolve(song, band, lineup_id)
    secs = song["sections"]
    parts_tracks, by_part = [], {}
    audio_groups, drum_groups = {}, {}

    def live_rig(per, idx):
        (who, rig_id), _ = Counter((per[secs[i]["id"]]["performer"], per[secs[i]["id"]]["rig"]) for i in idx).most_common(1)[0]
        return who, rig_id

    for part in song["parts"]:
        per = res[part["id"]]
        ts = [per[s["id"]]["treatment"] for s in secs]
        by = lambda group: [i for i, t in enumerate(ts) if t in group]
        A, M, L, V, D = by(AUDIO_T), by(MIDI_T), by(KEYS_T), by(VOX_T), by({"live-drums"})
        mine = by_part.setdefault(part["id"], [])
        if not any((A, M, L, V, D)): continue
        name, stem_ok = part["name"], LU.has_stem(song, part)
        if part["kind"] == "audio" or V:
            if V: audio_groups.setdefault(live_rig(per, V), []).append(part["id"])
            if A and stem_ok:
                n = name + (" (backing)" if V else ""); parts_tracks.append(n); mine.append(n)
            continue
        if A:
            if stem_ok:
                n = name + (" (stem)" if (M or L or D) else ""); parts_tracks.append(n); mine.append(n)
            else:
                M = sorted(M + A)
        if M or L:
            n = name if (part.get("instrument") or {}).get("type") == "analoglab" else "⚠ " + name
            parts_tracks.append(n); mine.append(n)
        if D: drum_groups.setdefault(live_rig(per, D), []).append(part["id"])

    inputs = []
    for (who, rig_id), pids in audio_groups.items():
        rig = LU._rig(band, who, rig_id) or {}
        perf = next((p for p in band.get("performers", []) if p["id"] == who), {})
        label = "Vocal" if rig.get("kind") == "vocal" else ("Bass" if rig.get("kind") == "bass" else "Guitar")
        n = f"{label} (live: {perf.get('name', who)})"; inputs.append(n)
        for pid in pids: by_part[pid].insert(0, n)
    drums = []
    for (who, _), pids in drum_groups.items():
        n = f"Drums (live: {who})"; drums.append(n)
        for pid in pids: by_part[pid].append(n)
    extra = ["Click"] + (["Cues"] if (song.get("options") or {}).get("cues") else [])
    return inputs + parts_tracks + drums + extra, by_part


def same_names(a, b):
    return a is not None and b is not None and Counter(a) == Counter(b)


def mix_plan(live_tracks, by_part, solo=(), mute=()):
    """UI solo/mute (part ids) -> [{index, name, mute, solo, parts}] for every Live track some part maps to
    (a track shared by several parts, e.g. a vocal input or the drum rack, is muted/soloed if any of its parts is).
    Tracks no part maps to (Click, Cues) are left alone. Also returns the part ids with no track in Live."""
    solo, mute = set(solo), set(mute)
    owners = {}
    for pid, names in by_part.items():
        for n in names: owners.setdefault(n, []).append(pid)
    out, present = [], set()
    for i, n in enumerate(live_tracks):
        if n not in owners: continue
        present.add(n)
        ps = owners[n]
        out.append({"index": i, "name": n, "parts": ps, "mute": any(p in mute for p in ps), "solo": any(p in solo for p in ps)})
    unmapped = [pid for pid, names in by_part.items() if not any(n in present for n in names)]
    return out, unmapped


class LiveLink:
    """Process-wide AbletonOSC connection plus a small cache of the set's scene/track names."""
    NAMES_TTL = 2.0

    def __init__(self, timeout=0.4, factory=None):
        self.timeout, self.factory = timeout, factory or (lambda: Live(timeout=timeout))
        self.client, self.lock = None, threading.RLock()
        self.scenes = self.tracks = None; self.names_at = 0.0
        self.slot, self.loop_starts, self.loop = None, {}, None

    def _c(self):
        if self.client is None: self.client = self.factory()   # LiveBusy propagates to the caller
        return self.client

    def close(self):
        with self.lock:
            if self.client: self.client.close()
            self.client = None

    def click_index(self):
        return self.tracks.index("Click") if self.tracks and "Click" in self.tracks else None

    def snapshot(self, names_ttl=None):
        """{connected, tempo, playing, song_beat, scenes, tracks, scene, fired, offset}. scene = the scene whose Click
        clip is playing; offset = beats into that clip (its section). One OSC round when the names are cached."""
        ttl = self.NAMES_TTL if names_ttl is None else names_ttl
        with self.lock:
            c = self._c()
            need_names = self.tracks is None or time.time() - self.names_at >= ttl
            for _ in range(3):
                ci, slot = self.click_index(), self.slot
                qs = [("/live/song/get/tempo",), ("/live/song/get/is_playing",), ("/live/song/get/current_song_time",)]
                if need_names: qs += [("/live/song/get/scenes/name",), ("/live/song/get/track_names",)]
                if ci is not None:
                    qs += [("/live/track/get/playing_slot_index", ci), ("/live/track/get/fired_slot_index", ci)]
                    if slot is not None and slot >= 0: qs.append(("/live/clip/get/playing_position", ci, slot))
                r = c.multi(qs)
                got = dict(zip([q[0] for q in qs], r))
                if got["/live/song/get/tempo"] is None:
                    self.scenes = self.tracks = None; self.slot = None; self.loop_starts = {}
                    return {"connected": False}
                if need_names:
                    sc, tr = got.get("/live/song/get/scenes/name"), got.get("/live/song/get/track_names")
                    sc = [str(x) for x in sc] if sc is not None else c.scene_names()
                    tr = [str(x) for x in tr] if tr is not None else c.track_names()
                    if (sc, tr) != (self.scenes, self.tracks): self.loop_starts = {}
                    self.scenes, self.tracks, self.names_at = sc, tr, time.time()
                    need_names = False
                    if self.click_index() != ci:                      # new set / first call: ask where its Click clip is
                        self.slot = None
                        if self.click_index() is not None: continue
                        break
                ps = got.get("/live/track/get/playing_slot_index")
                cur = int(ps[-1]) if ps else None
                if cur is not None and cur >= 0 and cur != slot:
                    self.slot = cur; continue                         # slot changed: ask its position
                self.slot = cur
                break
            fs = got.get("/live/track/get/fired_slot_index")
            out = {"connected": True, "tempo": got["/live/song/get/tempo"][0],
                   "playing": bool((got["/live/song/get/is_playing"] or [False])[0]),
                   "song_beat": (got["/live/song/get/current_song_time"] or [None])[0],
                   "scenes": self.scenes, "tracks": self.tracks, "scene": None, "fired": None, "offset": None, "loop": self.loop}
            if fs and int(fs[-1]) >= 0: out["fired"] = int(fs[-1])
            if out["playing"] and self.slot is not None and self.slot >= 0:
                out["scene"] = self.slot
                pp = got.get("/live/clip/get/playing_position")
                if pp:
                    key = (self.click_index(), self.slot)
                    if key not in self.loop_starts: self.loop_starts[key] = c.clip_loop_start(*key)
                    ls = self.loop_starts[key]
                    if ls is not None: out["offset"] = pp[-1] - ls
            return out

    def fire(self, index):
        with self.lock: self._c().fire_scene(int(index))

    def stop(self):
        with self.lock: self.loop = None; self._c().stop_all()

    def set_mix(self, plan):
        with self.lock:
            c = self._c()
            for t in plan:
                c.set_track_mute(t["index"], t["mute"]); c.set_track_solo(t["index"], t["solo"])


_link, _link_lock = None, threading.Lock()


def link():
    global _link
    with _link_lock:
        if _link is None: _link = LiveLink()
        return _link


def reset():
    """Drop the process-wide link (tests; or after changing LLS_OSC_* ports)."""
    global _link
    with _link_lock:
        if _link is not None: _link.close()
        _link = None
