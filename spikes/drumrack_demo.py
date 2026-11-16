"""Spike: Drum Rack (6 Quick Sampler samples on GM notes) + a Simpler track, as a Session View test set.

Run from repo root:  python spikes/drumrack_demo.py
Writes out/drumrack-demo/Drum Demo Project/Drum Demo.als. Scene 1: 2-bar GM beat on the rack. Scene 2: Kick 1's
original notes (4 bars) on a Simpler. Scene 3: STOP.
"""
import copy, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lls import als, drumrack as dr

MAIN = os.environ.get("LLS_MAIN", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LOGICX = f"{MAIN}/samples/Thoughts For The Night.logicx"
SONG = "songs/thoughts-for-the-night"
OUT = "out/drumrack-demo/Drum Demo Project/Drum Demo.als"
DRUMS = ["kick-1-flashback", "kick-2-flashback", "tom-low-flashback", "crash-hollywood-haze",
         "shaker-1-flashback", "fx-2-studio-cuts", "snare-2-deep-end", "hi-hat-1-lowtech-funk"]  # last 2: not installed
SCENES = ["Drum Rack GM beat", "Simpler Kick 1", "STOP"]


def beat(gm):
    """2 bars of 4/4, [(pitch, start, dur, vel)]; only pads whose sample is installed."""
    k1, k2, tom, crash, shk, fx = (gm[i] for i in ["kick-1-flashback", "kick-2-flashback", "tom-low-flashback",
                                                  "crash-hollywood-haze", "shaker-1-flashback", "fx-2-studio-cuts"])
    n = [(crash, 0, 1, 100)]
    n += [(k1, b, 0.25, 110) for b in (0, 2.5, 4, 6.5)]
    n += [(k2, b, 0.25, 105) for b in (2, 6)]
    n += [(shk, i * 0.5, 0.25, 70 if i % 2 else 95) for i in range(16)]
    n += [(tom, b, 0.25, 90 + int(b * 4) % 3 * 10) for b in (7, 7.25, 7.5)]
    n.append((fx, 7.75, 0.25, 100))
    return n


def build(out=OUT, song_dir=SONG, logicx=LOGICX):
    """Build the demo set; returns (path, gm_notes, warnings)."""
    song = json.load(open(f"{song_dir}/song.json"))
    song["quicksampler_files"] = dr.quicksampler_files(logicx)
    proj = os.path.abspath(os.path.dirname(out))
    root = als.load_gz(als.BASE); ls = root.find("LiveSet")
    root.set("Creator", "Ableton Logic Studio")
    ids, P = als.Ids(root), als.Protos()
    tracks = ls.find("Tracks")
    for t in list(tracks):
        if t.tag in ("AudioTrack", "MidiTrack"): tracks.remove(t)
    n_scenes, built, info = len(SCENES), [], []
    tid = max([int(t.get("Id")) for t in tracks] + [0]) + 1

    def add(t, clips):
        nonlocal tid
        t.set("Id", str(tid)); tid += 1
        ids.renumber(t); als.fill_slots(t, n_scenes, clips); built.append(t)

    rack, gm = dr.make_drum_track(P, DRUMS, song, song_dir, project_dir=proj, name="Drum Rack (GM)", color=9, info=info)
    add(rack, {0: als.midi_clip(P.midi_clip, beat(gm), "GM beat", 9, 8)})

    kick = next(p for p in song["parts"] if p["id"] == "kick-1-flashback")
    kpitch = dr.main_pitch(song_dir, kick)
    notes = json.load(open(f"{song_dir}/{kick['midi']}"))["notes"]
    # the bar-aligned 4-bar window that holds the most kicks
    win = max(range(0, int(song["length_beats"]) - 16, 4), key=lambda s: sum(s <= n[1] < s + 16 for n in notes))
    ns = [(p, st - win, d, v) for p, st, d, v in notes if win <= st < win + 16]
    simp = dr.make_simpler_track(P, dr.sample_for_part(kick, song), proj, "Simpler: Kick 1", 14, root=kpitch, info=info)
    add(simp, {1: als.midi_clip(P.midi_clip, ns, f"Kick 1 original (from bar {win // 4 + 1})", 14, 16)})

    for i, t in enumerate(built): tracks.insert(i, t)
    for t in tracks:
        if t.tag == "ReturnTrack": als.fill_slots(t, n_scenes, {})
    scp = ls.find("Scenes"); sproto = copy.deepcopy(scp.find("Scene")); als.clear(scp)
    for i, nm in enumerate(SCENES):
        sc = copy.deepcopy(sproto); sc.set("Id", str(i)); als.setv(sc, "Name", nm)
        als.setv(sc, "Color", 0 if nm == "STOP" else 14 + i); als.setv(sc, "Tempo", song["tempo"])
        als.setv(sc, "IsTempoEnabled", nm != "STOP"); scp.append(sc)
    tn = ls.find("MainTrack/DeviceChain/Mixer/Tempo"); als.setv(tn, "Manual", song["tempo"])
    tp = tn.find("AutomationTarget").get("Id")
    for env in ls.find("MainTrack").iter("AutomationEnvelope"):
        if env.find("EnvelopeTarget/PointeeId").get("Value") == tp:
            for fe in env.iter("FloatEvent"): fe.set("Value", str(song["tempo"]))
    als.setv(ls, "GlobalQuantisation", 4)
    als.setv(ls, "NextPointeeId", ids.next + 100)
    problems = als.validate(root)
    if problems: raise SystemExit(f"invalid: {problems[:5]}")
    als.write(root, os.path.abspath(out))
    return os.path.abspath(out), gm, info


if __name__ == "__main__":
    path, gm, info = build()
    print("GM notes:", gm)
    for m in info: print("WARN", m)
    print("wrote", path)
