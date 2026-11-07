#!/usr/bin/env python3
"""Spike 2: generate a Session View .als from Live-saved prototypes (stdlib only).

Base set:  Live's own "8-Track Template.als" (schema 12.0_12402).
Clip prototypes: the Live 12 Suite demo song (session AudioClip / MidiClip).

ID rules (derived from the demo set, see docs/02-spike-als.md):
  * global pointee IDs: AutomationTarget, ModulationTarget, Pointee, *ModulationTarget,
    ControllerTargets.N -> unique across the file and < LiveSet/NextPointeeId
  * track IDs unique among tracks, scene IDs unique among scenes
  * every other Id is local to its parent list
"""
import copy, gzip, math, os, struct, sys, wave, xml.etree.ElementTree as ET

LIVE = os.environ.get("LIVE_APP", "/Applications/Ableton Live 12 Beta.app")
RES = f"{LIVE}/Contents/App-Resources/Core Library"
BASE = f"{RES}/Templates/8-Track Template.als"
PROTO = f"{RES}/Lessons/Demo Songs/Chuck Sutton - Patience (Live 12 Suite Demo).als"


def load(p):
    return ET.ElementTree(ET.fromstring(gzip.open(p).read()))


def is_global(e):
    t = e.tag
    return "Id" in e.attrib and (t.endswith("Target") or t.startswith("ControllerTargets.") or t == "Pointee")


class Ids:
    def __init__(self, root):
        self.next = max([int(e.get("Id")) for e in root.iter() if is_global(e)] + [0]) + 1

    def renumber(self, elem):
        for e in elem.iter():
            if is_global(e):
                e.set("Id", str(self.next)); self.next += 1


def setv(elem, path, value):
    elem.find(path).set("Value", str(value))


def make_wav(path, freq, seconds, sr=44100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = int(seconds * sr)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            env = 1.0 if (i % (sr // 2)) < sr // 4 else 0.3  # pulse so you hear the bar grid
            frames += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * freq * i / sr)))
        w.writeframes(bytes(frames))
    return n, sr


def audio_clip(proto, wav, project_dir, name, color, beats, tempo):
    c = copy.deepcopy(proto)
    frames, sr = wave.open(wav).getnframes(), wave.open(wav).getframerate()
    secs = frames / sr
    c.set("Time", "0")
    for p, v in [("CurrentStart", 0), ("CurrentEnd", beats), ("Loop/LoopStart", 0), ("Loop/LoopEnd", beats),
                 ("Loop/StartRelative", 0), ("Loop/LoopOn", "true"), ("Loop/OutMarker", beats),
                 ("Loop/HiddenLoopStart", 0), ("Loop/HiddenLoopEnd", beats), ("Name", name), ("Color", color),
                 ("LaunchQuantisation", 0), ("IsWarped", "true"), ("WarpMode", 0),
                 ("ScrollerTimePreserver/LeftTime", 0), ("ScrollerTimePreserver/RightTime", beats)]:
        setv(c, p, v)
    c.find("ScrollerTimePreserver/LeftTime")  # keep
    env = c.find("Envelopes/Envelopes"); [env.remove(x) for x in list(env)]  # drop clip envelopes (foreign pointee refs)
    fr = c.find("SampleRef/FileRef")
    setv(fr, "RelativePathType", 1)
    setv(fr, "RelativePath", os.path.relpath(wav, project_dir))
    setv(fr, "Path", os.path.abspath(wav))
    setv(fr, "Type", 1); setv(fr, "LivePackName", ""); setv(fr, "LivePackId", "")
    setv(fr, "OriginalFileSize", os.path.getsize(wav)); setv(fr, "OriginalCrc", 0)
    sref = c.find("SampleRef")
    setv(sref, "LastModDate", int(os.path.getmtime(wav)))
    setv(sref, "DefaultDuration", frames); setv(sref, "DefaultSampleRate", sr)
    wm = c.find("WarpMarkers"); [wm.remove(x) for x in list(wm)]
    ET.SubElement(wm, "WarpMarker", Id="0", SecTime="0", BeatTime="0")
    ET.SubElement(wm, "WarpMarker", Id="1", SecTime=repr(secs), BeatTime=repr(secs * tempo / 60))
    return c


def midi_clip(proto, notes, name, color, beats):
    c = copy.deepcopy(proto)
    for p, v in [("CurrentStart", 0), ("CurrentEnd", beats), ("Loop/LoopStart", 0), ("Loop/LoopEnd", beats),
                 ("Loop/StartRelative", 0), ("Loop/LoopOn", "true"), ("Loop/OutMarker", beats),
                 ("Loop/HiddenLoopStart", 0), ("Loop/HiddenLoopEnd", beats), ("Name", name), ("Color", color),
                 ("LaunchQuantisation", 0)]:
        setv(c, p, v)
    env = c.find("Envelopes/Envelopes"); [env.remove(x) for x in list(env)]
    n = c.find("Notes"); kt = n.find("KeyTracks"); [kt.remove(x) for x in list(kt)]
    ev = n.find("PerNoteEventStore/EventLists"); [ev.remove(x) for x in list(ev)]
    by_key = {}
    for i, (pitch, t, d, vel) in enumerate(notes, start=1):
        by_key.setdefault(pitch, []).append((t, d, vel, i))
    for k, (pitch, evs) in enumerate(sorted(by_key.items())):
        trk = ET.SubElement(kt, "KeyTrack", Id=str(k)); ns = ET.SubElement(trk, "Notes")
        for t, d, vel, nid in evs:
            ET.SubElement(ns, "MidiNoteEvent", Time=str(t), Duration=str(d), Velocity=str(vel),
                          OffVelocity="64", NoteId=str(nid))
        ET.SubElement(trk, "MidiKey", Value=str(pitch))
    nni = n.find("NoteIdGenerator/NextId")
    if nni is not None: nni.set("Value", str(len(notes) + 1))
    return c


def fill_slots(track, n_scenes, clips=None):
    """Resize every ClipSlotList in the track to n_scenes; put clips[i] in main sequencer slot i."""
    for csl in track.iter("ClipSlotList"):
        if csl.find("ClipSlot") is None:  # return tracks keep an empty list
            continue
        proto = copy.deepcopy(csl.find("ClipSlot"))
        v = proto.find("ClipSlot/Value"); [v.remove(x) for x in list(v)]
        [csl.remove(x) for x in list(csl)]
        for i in range(n_scenes):
            s = copy.deepcopy(proto); s.set("Id", str(i))
            hs = s.find("HasStop")
            if hs is not None: hs.set("Value", "true")
            csl.append(s)
    if clips:
        main = track.find("DeviceChain/MainSequencer/ClipSlotList")
        for i, clip in clips.items():
            main[i].find("ClipSlot/Value").append(clip)


def build(spec, out_als):
    project_dir = os.path.dirname(out_als)
    tree, demo = load(BASE), load(PROTO)
    root = tree.getroot(); ls = root.find("LiveSet")
    root.set("Creator", "Ableton Logic Studio spike")
    ids = Ids(root)
    a_clip = next(s.find("ClipSlot/Value/AudioClip") for s in demo.iter("ClipSlot") if s.find("ClipSlot/Value/AudioClip") is not None)
    m_clip = next(s.find("ClipSlot/Value/MidiClip") for s in demo.iter("ClipSlot") if s.find("ClipSlot/Value/MidiClip") is not None)
    m_track_proto = next(demo.getroot().iter("MidiTrack"))

    tracks = ls.find("Tracks")
    a_proto = copy.deepcopy(tracks.find("AudioTrack"))
    for t in list(tracks):
        if t.tag in ("AudioTrack", "MidiTrack"): tracks.remove(t)

    # MIDI track prototype from the demo: strip its devices so it's an empty MIDI track
    mp = copy.deepcopy(m_track_proto)
    devs = mp.find("DeviceChain/DeviceChain/Devices"); [devs.remove(x) for x in list(devs)]
    for ae in mp.iter("AutomationEnvelopes"):
        e = ae.find("Envelopes")
        if e is not None: [e.remove(x) for x in list(e)]
    for tag in ("ArrangerAutomation",):
        for aa in mp.iter(tag):
            ev = aa.find("Events")
            if ev is not None: [ev.remove(x) for x in list(ev)]
    mp.find("TrackGroupId").set("Value", "-1")

    scenes = spec["scenes"]; n = len(scenes)
    tempo = spec["tempo"]; beats = spec["clip_beats"]
    other_ids = [int(t.get("Id")) for t in tracks]  # return tracks keep theirs
    next_track_id = max(other_ids + [0]) + 1
    insert_at = 0
    for ti, tspec in enumerate(spec["tracks"]):
        t = copy.deepcopy(a_proto if tspec["kind"] == "audio" else mp)
        t.set("Id", str(next_track_id)); next_track_id += 1
        ids.renumber(t)
        setv(t, "Name/EffectiveName", tspec["name"]); setv(t, "Name/UserName", tspec["name"])
        setv(t, "Color", tspec["color"])
        clips = {}
        for si, sc in enumerate(scenes):
            item = tspec["clips"].get(sc["name"])
            if item is None: continue
            if tspec["kind"] == "audio":
                clips[si] = audio_clip(a_clip, item, project_dir, f'{tspec["name"]} {sc["name"]}', tspec["color"], beats, tempo)
            else:
                clips[si] = midi_clip(m_clip, item, f'{tspec["name"]} {sc["name"]}', tspec["color"], beats)
        fill_slots(t, n, clips)
        tracks.insert(insert_at, t); insert_at += 1
    for t in tracks:
        if t.tag == "ReturnTrack": fill_slots(t, n)

    # scenes
    sc_parent = ls.find("Scenes"); sproto = copy.deepcopy(sc_parent.find("Scene"))
    [sc_parent.remove(x) for x in list(sc_parent)]
    for i, sc in enumerate(scenes):
        s = copy.deepcopy(sproto); s.set("Id", str(i))
        setv(s, "Name", sc["name"]); setv(s, "Color", sc.get("color", -1))
        sc_parent.append(s)

    # tempo, 1-bar global quantize
    tnode = ls.find("MainTrack/DeviceChain/Mixer/Tempo"); setv(tnode, "Manual", tempo)
    tid = tnode.find("AutomationTarget").get("Id")
    for env in ls.find("MainTrack").iter("AutomationEnvelope"):
        if env.find("EnvelopeTarget/PointeeId").get("Value") == tid:
            for fe in env.iter("FloatEvent"): fe.set("Value", str(tempo))
    setv(ls, "GlobalQuantisation", 4)
    setv(ls, "NextPointeeId", ids.next + 100)

    os.makedirs(project_dir, exist_ok=True)
    data = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
    with gzip.open(out_als, "wb") as f: f.write(data)
    return root


def validate(root):
    seen, problems = {}, []
    nxt = int(root.find("LiveSet/NextPointeeId").get("Value"))
    for e in root.iter():
        if is_global(e):
            i = int(e.get("Id"))
            if i in seen: problems.append(f"dup global id {i} ({e.tag} / {seen[i]})")
            if i >= nxt: problems.append(f"id {i} >= NextPointeeId")
            seen[i] = e.tag
    tids = [t.get("Id") for t in root.find("LiveSet/Tracks")]
    if len(tids) != len(set(tids)): problems.append("dup track ids")
    n = len(root.find("LiveSet/Scenes"))
    for t in root.find("LiveSet/Tracks"):
        for csl in t.iter("ClipSlotList"):
            if len(csl) and len(csl) != n: problems.append(f"track {t.get('Id')} has {len(csl)} slots, {n} scenes")
    return problems


if __name__ == "__main__":
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "out/spike2/Spike Set Project/Spike Set.als")
    proj = os.path.dirname(out); tempo = 120; beats = 16  # 4 bars
    secs = beats * 60 / tempo
    smp = lambda nm: os.path.join(proj, "Samples", nm)
    for nm, f in [("bass_intro.wav", 55), ("bass_verse.wav", 65.4), ("bass_chorus.wav", 73.4),
                  ("pad_verse.wav", 220), ("pad_chorus.wav", 293.7)]:
        make_wav(smp(nm), f, secs)
    lead = [(72, b, 0.5, 100) for b in range(0, 16, 2)] + [(76, b + 1, 0.5, 90) for b in range(0, 16, 2)]
    spec = {"tempo": tempo, "clip_beats": beats,
            "scenes": [{"name": "Intro", "color": 14}, {"name": "Verse", "color": 20},
                       {"name": "Chorus", "color": 3}, {"name": "STOP", "color": 0}],
            "tracks": [
                {"kind": "audio", "name": "Bass (stem)", "color": 5,
                 "clips": {"Intro": smp("bass_intro.wav"), "Verse": smp("bass_verse.wav"), "Chorus": smp("bass_chorus.wav")}},
                {"kind": "audio", "name": "Pad (stem)", "color": 25,
                 "clips": {"Verse": smp("pad_verse.wav"), "Chorus": smp("pad_chorus.wav")}},
                {"kind": "midi", "name": "Lead (MIDI)", "color": 9, "clips": {"Chorus": lead}},
            ]}
    root = build(spec, out)
    probs = validate(root)
    print(out); print("validation:", "OK" if not probs else probs[:10])
