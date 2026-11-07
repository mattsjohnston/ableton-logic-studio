"""song folder + lineup -> self-contained Live project (one set per song, Session View)."""
import copy, json, math, os, shutil
import numpy as np

from . import als
from .ingest import write_wav, _read_wav

CLICK_OUT = 3  # interface outputs 3/4 = in-ears (until the output plan is known)
COLORS = {"backing-audio": 14, "midi-clip": 5, "click": 0, "needs-stem": 1}
SCENE_COLORS = [14, 20, 3, 9, 25, 16, 6, 12]


def treatment(song, lineup, part):
    t = song["lineups"].get(lineup, {}).get("treatments", {}).get(part["id"])
    return t or part["default_treatment"]


def click_wav(path, tempo, beats, sr, bpb):
    n = int(beats * 60 / tempo * sr) + sr
    buf = np.zeros((n, 2), np.float32)
    tick = np.arange(int(0.03 * sr)) / sr
    for b in range(int(beats)):
        f, amp = (1500, 0.6) if b % bpb == 0 else (1000, 0.4)
        s = np.sin(2 * np.pi * f * tick) * np.exp(-tick * 120) * amp
        i = int(b * 60 / tempo * sr)
        buf[i:i + len(s)] += s[:, None]
    write_wav(path, buf, sr)


def active(audio, sr, tempo, start, end, thresh=1e-4):
    a = audio[int(start * 60 / tempo * sr):int(end * 60 / tempo * sr)]
    return a.size and float(np.abs(a).max()) > thresh


def build(song_dir, lineup="default", out_root="out/sets"):
    song = json.load(open(os.path.join(song_dir, "song.json")))
    title, tempo, sr = song["title"], song["tempo"], song["sample_rate"]
    bpb = song["time_signature"][0] * 4 / song["time_signature"][1]
    proj = os.path.abspath(os.path.join(out_root, title, lineup, f"{title} Project"))
    if os.path.exists(proj): shutil.rmtree(proj)
    smp = os.path.join(proj, "Samples"); os.makedirs(smp)
    out_als = os.path.join(proj, f"{title}.als")

    root = als.load_gz(als.BASE); ls = root.find("LiveSet")
    root.set("Creator", "Ableton Logic Studio")
    ids, P = als.Ids(root), als.Protos()
    tracks = ls.find("Tracks")
    a_proto = als.strip_track(tracks.find("AudioTrack"))
    for t in list(tracks):
        if t.tag in ("AudioTrack", "MidiTrack"): tracks.remove(t)
    secs = song["sections"]; n = len(secs) + 1  # + STOP scene
    next_tid = max([int(t.get("Id")) for t in tracks] + [0]) + 1
    report, built = [], []

    def add_track(proto, name, color, clips):
        nonlocal next_tid
        t = copy.deepcopy(proto); t.set("Id", str(next_tid)); next_tid += 1
        ids.renumber(t)
        als.setv(t, "Name/EffectiveName", name); als.setv(t, "Name/UserName", name); als.setv(t, "Color", color)
        als.fill_slots(t, n, clips)
        built.append(t); return t

    def audio_part(name, wav_src, color):
        dst = os.path.join(smp, os.path.basename(wav_src)); shutil.copy2(wav_src, dst)
        audio, fsr = _read_wav(dst)
        clips = {i: als.audio_clip(P.audio_clip, dst, proj, f"{name} · {s['name']}", color, s["start"], s["end"], tempo)
                 for i, s in enumerate(secs) if active(audio, fsr, tempo, s["start"], s["end"])}
        return add_track(a_proto, name, color, clips), len(clips)

    for part in song["parts"]:
        tr = treatment(song, lineup, part)
        inst = part.get("instrument", {})
        if tr in ("live-vocal", "dropped"):
            report.append((part["name"], tr, "no playback track")); continue
        if tr == "backing-audio":
            stem = part.get("stem") or part.get("raw_stem")
            if stem:
                _, k = audio_part(part["name"], os.path.join(song_dir, stem), COLORS["backing-audio"])
                report.append((part["name"], tr, f"{k} section clips from {'exported' if part.get('stem') else 'RAW (no FX)'} stem"))
                continue
            tr = "needs-stem"  # Logic built-in with no exported stem yet: fall through to MIDI so nothing is lost
        if part["kind"] == "midi":
            notes = json.load(open(os.path.join(song_dir, part["midi"])))["notes"]
            clips = {}
            for i, s in enumerate(secs):
                ns = [(p, st - s["start"], d, v) for p, st, d, v in notes if s["start"] <= st < s["end"]]
                if ns: clips[i] = als.midi_clip(P.midi_clip, ns, f"{part['name']} · {s['name']}", COLORS.get(tr, 5), s["end"] - s["start"])
            if inst.get("type") == "analoglab":
                t = add_track(P.au_track, part["name"], COLORS["midi-clip"], clips)
                als.set_au_state(t, open(os.path.join(song_dir, inst["state"]), "rb").read(), inst["preset"])
                report.append((part["name"], tr, f"{len(clips)} MIDI clips → Analog Lab V (AU) '{inst['preset']}'"))
            else:
                add_track(P.midi_track, ("⚠ " if tr == "needs-stem" else "") + part["name"], COLORS["needs-stem"], clips)
                report.append((part["name"], tr, f"{len(clips)} MIDI clips, NO INSTRUMENT ({inst.get('name')}: Logic-only) — export its stem"))

    # click → in-ears
    cw = os.path.join(smp, "Click.wav"); click_wav(cw, tempo, song["length_beats"], sr, int(bpb))
    ct = add_track(a_proto, "Click", COLORS["click"],
                   {i: als.audio_clip(P.audio_clip, cw, proj, f"Click · {s['name']}", 0, s["start"], s["end"], tempo) for i, s in enumerate(secs)})
    als.route_output(ct, CLICK_OUT)
    report.append(("Click (generated)", "click", f"routed to outputs {CLICK_OUT}/{CLICK_OUT + 1}"))

    for i, t in enumerate(built): tracks.insert(i, t)
    for t in tracks:
        if t.tag == "ReturnTrack": als.fill_slots(t, n, {})

    scp = ls.find("Scenes"); sproto = copy.deepcopy(scp.find("Scene")); als.clear(scp)
    for i, s in enumerate(secs + [{"name": "STOP", "start": None}]):
        sc = copy.deepcopy(sproto); sc.set("Id", str(i))
        bar = f" (bar {int(s['start'] // bpb) + 1})" if s["start"] is not None else ""
        als.setv(sc, "Name", s["name"] + bar); als.setv(sc, "Color", 0 if s["name"] == "STOP" else SCENE_COLORS[i % len(SCENE_COLORS)])
        als.setv(sc, "Tempo", tempo); als.setv(sc, "IsTempoEnabled", s["name"] != "STOP")
        scp.append(sc)

    tn = ls.find("MainTrack/DeviceChain/Mixer/Tempo"); als.setv(tn, "Manual", tempo)
    tid = tn.find("AutomationTarget").get("Id")
    for env in ls.find("MainTrack").iter("AutomationEnvelope"):
        if env.find("EnvelopeTarget/PointeeId").get("Value") == tid:
            for fe in env.iter("FloatEvent"): fe.set("Value", str(tempo))
    als.setv(ls, "GlobalQuantisation", 4)  # 1 bar
    als.setv(ls, "NextPointeeId", ids.next + 100)

    problems = als.validate(root)
    if problems: raise RuntimeError(f"invalid set: {problems[:5]}")
    als.write(root, out_als)

    lines = [f"# {title} · lineup '{lineup}'", f"tempo {tempo} · {len(secs)} sections + STOP · {out_als}", ""]
    lines += [f"- {name}: **{tr}** — {note}" for name, tr, note in report]
    lines += [f"- {i['name']}: **{i['status']}** — {i['reason']}" for i in song.get("ignored_tracks", [])]
    open(os.path.join(os.path.dirname(proj), "accounted-for.md"), "w").write("\n".join(lines) + "\n")
    return out_als, report
