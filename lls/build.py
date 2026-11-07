"""song folder + lineup -> self-contained Live project (one set per song, Session View).

Per part, the lineup's per-section treatments (lineup.resolve) decide the tracks:
  backing-audio / audio-loop sections -> audio track with clips in those sections only
  midi-clip sections                  -> MIDI track (instrument) with clips in those sections
  live-midi / live-looped sections    -> the same MIDI track, input from the rig's port/channel
  live-vocal / live-guitar sections   -> audio track, input from the rig's interface input (shared with backing clips)
  live-drums                          -> ONE Drum Rack track per performer+rig (lls.drumrack.make_drum_track)
Monitoring: In when a track is purely live; Auto + armed when it mixes live sections with clip sections (a playing clip
then has the track, the input is heard in empty/stopped sections) or when several tracks share one rig (sound switching,
docs/model.md MVP: empty placeholder clips block the input in sections where that track is not live).
"""
import copy, json, os, shutil
from collections import Counter
import numpy as np

from . import als, lineup as LU
from .ingest import write_wav, _read_wav

CLICK_OUT = 3  # interface outputs 3/4 = in-ears (until the output plan is known)
COLORS = {"backing-audio": 14, "audio-loop": 14, "midi-clip": 5, "click": 0, "needs-stem": 1,
          "live-vocal": 9, "live-guitar": 3, "live-midi": 25, "live-looped": 25, "live-drums": 12, "placeholder": 58}
SCENE_COLORS = [14, 20, 3, 9, 25, 16, 6, 12]
AUDIO_T, MIDI_T, KEYS_T, VOX_T = {"backing-audio", "audio-loop"}, {"midi-clip"}, {"live-midi", "live-looped"}, {"live-vocal", "live-guitar"}
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def treatment(song, lineup, part):
    """Phase-1 helper kept for callers: the part's treatment in the first section."""
    return next(iter(LU.resolve(song, {"performers": []}, lineup)[part["id"]].values()))["treatment"]


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


def _find_band(band_path):
    for p in [band_path, os.path.join(os.getcwd(), "band.json"), os.path.join(REPO, "band.json")]:
        if p and os.path.exists(p): return LU.load_band(p)
    if band_path: raise FileNotFoundError(band_path)
    return {"schema": 1, "performers": []}


def _summary(ts):
    c = Counter(ts)
    return next(iter(c)) if len(c) == 1 else "mixed: " + ", ".join(f"{t}×{n}" for t, n in c.most_common())


def _validation_lines(problems, secs):
    order = {s["id"]: i for i, s in enumerate(secs)}
    groups = {}
    for p in problems:
        groups.setdefault((p["level"], p["part"], p["message"]), []).append(p["section"])
    lines = ["", "## Validation", ""]
    if not groups: return lines + ["No errors or warnings."]
    for (level, part, msg), ss in sorted(groups.items(), key=lambda g: (g[0][0] != "error", g[0][1] or "", g[0][2])):
        ss = sorted((s for s in ss if s), key=order.get)
        where = f" [{', '.join(ss)}]" if ss and len(ss) < len(secs) else (" [all sections]" if ss else "")
        lines.append(f"- **{level}** {part or '(lineup)'}: {msg}{where}")
    return lines


def _drum_maker():
    try:
        from . import drumrack
        return getattr(drumrack, "make_drum_track", None)
    except ImportError:
        return None


def build(song_dir, lineup="default", out_root="out/sets", band_path=None):
    song = LU.load_song(song_dir)
    band = _find_band(band_path)
    res = LU.resolve(song, band, lineup)
    problems = LU.validate(song, band, lineup)
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
    live_tracks = []  # (track, performer, rig, live section indices, clip section indices, kind)
    drum_groups = {}  # (performer, rig) -> [part]
    audio_groups = {}  # (performer, rig) -> [part id]  (live-vocal / live-guitar)

    def add_track(proto, name, color, clips):
        nonlocal next_tid
        t = copy.deepcopy(proto); t.set("Id", str(next_tid)); next_tid += 1
        ids.renumber(t)
        als.setv(t, "Name/EffectiveName", name); als.setv(t, "Name/UserName", name); als.setv(t, "Color", color)
        als.fill_slots(t, n, clips)
        built.append(t); return t

    stem_cache = {}

    def stem_clips(part, name, idx, color):
        rel = part.get("stem") or part.get("raw_stem")
        src = os.path.join(song_dir, rel)
        if src not in stem_cache:
            dst = os.path.join(smp, os.path.basename(src)); shutil.copy2(src, dst)
            stem_cache[src] = (dst,) + _read_wav(dst)
        dst, audio, fsr = stem_cache[src]
        return {i: als.audio_clip(P.audio_clip, dst, proj, f"{name} · {secs[i]['name']}", color, secs[i]["start"], secs[i]["end"],
                                  tempo, loop=True)
                for i in idx if active(audio, fsr, tempo, secs[i]["start"], secs[i]["end"])}

    def midi_clips(part, idx, color):
        notes = json.load(open(os.path.join(song_dir, part["midi"])))["notes"]
        clips = {}
        for i in idx:
            s = secs[i]
            ns = [(p, st - s["start"], d, v) for p, st, d, v in notes if s["start"] <= st < s["end"]]
            if ns: clips[i] = als.midi_clip(P.midi_clip, ns, f"{part['name']} · {s['name']}", color, s["end"] - s["start"])
        return clips

    def live_rig(per, idx):
        rigs = Counter((per[secs[i]["id"]]["performer"], per[secs[i]["id"]]["rig"]) for i in idx)
        (who, rig_id), _ = rigs.most_common(1)[0]
        return who, rig_id, LU._rig(band, who, rig_id) or {}, len(rigs) > 1

    for part in song["parts"]:
        per = res[part["id"]]
        ts = [per[s["id"]]["treatment"] for s in secs]
        by = lambda group: [i for i, t in enumerate(ts) if t in group]
        A, M, L, V = by(AUDIO_T), by(MIDI_T), by(KEYS_T), by(VOX_T)
        D = by({"live-drums"})
        inst, name, notes = part.get("instrument") or {}, part["name"], []
        stem_ok = LU.has_stem(song, part)
        if not any((A, M, L, V, D)):
            report.append((name, _summary(ts), "no track")); continue

        if part["kind"] == "audio" or V:
            # live audio: ONE input track per performer+rig (a mic monitored on 5 tracks would sound 5 times);
            # backing sections of the same part keep their own clip track
            if V:
                who, rig_id, _, multi = live_rig(per, V)
                audio_groups.setdefault((who, rig_id), []).append(part["id"])
                notes.append(f"sung/played live on {who}/{rig_id} in {len(V)} sections")
                if multi: notes.append("⚠ several rigs across sections: grouped with the most-used one")
            if A and not stem_ok:
                notes.append(f"NO STEM for {len(A)} backing sections — export it from Logic"); A = []
            if A:
                clips = stem_clips(part, name, A, COLORS["backing-audio"])
                add_track(a_proto, name + (" (backing)" if V else ""), COLORS["backing-audio"], clips)
                notes.append(f"{len(clips)} section clips from {'exported' if part.get('stem') else 'RAW (no FX)'} stem")
            report.append((name, _summary(ts), "; ".join(notes) or "nothing to play")); continue

        # MIDI part
        if A:
            if stem_ok:
                clips = stem_clips(part, name, A, COLORS["backing-audio"])
                add_track(a_proto, name + (" (stem)" if (M or L or D) else ""), COLORS["backing-audio"], clips)
                notes.append(f"{len(clips)} audio clips from {'exported' if part.get('stem') else 'RAW (no FX)'} stem")
            else:
                M = sorted(M + A)  # Logic built-in with no exported stem yet: fall back to MIDI so nothing is lost
                notes.append(f"NO STEM: {len(A)} backing sections fall back to MIDI clips")
        if M or L:
            color = COLORS["live-midi" if L else "midi-clip"]
            clips = midi_clips(part, M, color)
            if inst.get("type") == "analoglab":
                t = add_track(P.au_track, name, color, clips)
                als.set_au_state(t, open(os.path.join(song_dir, inst["state"]), "rb").read(), inst["preset"])
                notes.append(f"{len(clips)} MIDI clips → Analog Lab V (AU) '{inst['preset']}'")
            else:
                t = add_track(P.midi_track, "⚠ " + name, COLORS["needs-stem"], clips)
                notes.append(f"{len(clips)} MIDI clips, NO INSTRUMENT ({inst.get('name')}: Logic-only) — export its stem")
            if L:
                who, rig_id, rig, multi = live_rig(per, L)
                als.route_midi_input(t, rig.get("midi_port"), rig.get("midi_channel"))
                als.set_monitoring(t, als.MONITOR_AUTO if clips else als.MONITOR_IN, armed=bool(clips))
                sounding = [i for i in L if LU.intervals(song, part, secs[i])]
                live_tracks.append((t, who, rig_id, sounding, list(clips), "midi"))
                notes.append(f"live from {who}/{rig_id} ({rig.get('midi_port') or 'All Ins'} ch {rig.get('midi_channel') or 'all'}) "
                             f"in {len(L)} sections" + (", Auto + armed" if clips else ", monitoring In"))
                if any(ts[i] == "live-looped" for i in L): notes.append("live-looped: empty slots left for looping")
                if multi: notes.append("⚠ several rigs across sections: routed to the most-used one")
            else:
                als.set_monitoring(t, als.MONITOR_AUTO, armed=False)
        if D:
            who, rig_id, _, _ = live_rig(per, D)
            drum_groups.setdefault((who, rig_id), []).append(part)
            notes.append(f"live-drums group {who}/{rig_id} ({len(D)} sections)")
        report.append((name, _summary(ts), "; ".join(notes)))

    n_parts_tracks = len(built)
    for (who, rig_id), pids in audio_groups.items():
        rig = LU._rig(band, who, rig_id) or {}
        perf = next((p for p in band.get("performers", []) if p["id"] == who), {})
        label = "Vocal" if rig.get("kind") == "vocal" else ("Bass" if rig.get("kind") == "bass" else "Guitar")
        t = add_track(a_proto, f"{label} (live: {perf.get('name', who)})", COLORS["live-vocal" if label == "Vocal" else "live-guitar"], {})
        als.route_audio_input(t, rig.get("audio_input"))
        als.set_monitoring(t, als.MONITOR_IN, armed=False)
        report.append((f"{label} input ({who}/{rig_id})", "live-vocal" if label == "Vocal" else "live-guitar",
                       f"interface input {rig.get('audio_input')}, monitoring In; covers {', '.join(pids)}"))
    built[:] = built[n_parts_tracks:] + built[:n_parts_tracks]  # live inputs first

    make_drum_track = _drum_maker()
    for (who, rig_id), dparts in drum_groups.items():
        rig = LU._rig(band, who, rig_id) or {}
        pids = [p["id"] for p in dparts]
        if make_drum_track:
            proto = make_drum_track(P, pids, song, song_dir)
            if isinstance(proto, tuple): proto = proto[0]
            note = f"Drum Rack from {len(pids)} parts"
        else:
            proto = P.midi_track
            note = f"⚠ lls.drumrack missing: plain MIDI track, NO DRUM RACK for {len(pids)} parts"
        t = add_track(proto, f"Drums (live: {who})", COLORS["live-drums"], {})
        als.route_midi_input(t, rig.get("midi_port"), rig.get("midi_channel"))
        als.set_monitoring(t, als.MONITOR_IN, armed=False)
        report.append((f"Drums ({who}/{rig_id})", "live-drums",
                       f"{note}: {', '.join(pids)}; input {rig.get('midi_port') or 'All Ins'} ch {rig.get('midi_channel') or 'all'}, monitoring In"))

    # sound switching (docs/model.md MVP): several instrument tracks on one MIDI rig -> Auto + armed, and an empty
    # placeholder clip in every section where that track is not sounding live, so its input is blocked there.
    shared = Counter((w, r) for _, w, r, _, _, k in live_tracks if k == "midi")
    for t, who, rig_id, live_idx, clip_idx, kind in live_tracks:
        if kind != "midi" or shared[(who, rig_id)] < 2: continue
        als.set_monitoring(t, als.MONITOR_AUTO, armed=True)
        slots = t.find("DeviceChain/MainSequencer/ClipSlotList")
        k = 0
        for i, s in enumerate(secs):
            if i in live_idx or i in clip_idx: continue
            slots[i].find("ClipSlot/Value").append(als.midi_clip(P.midi_clip, [], "(not live)", COLORS["placeholder"], s["end"] - s["start"]))
            k += 1
        both = [secs[i]["id"] for i in live_idx
                if sum(i in o[3] for o in live_tracks if o[0] is not t and o[1:3] == (who, rig_id)) > 0]
        report.append((t.find("Name/EffectiveName").get("Value"), "sound-switch",
                       f"shares {who}/{rig_id} with {shared[(who, rig_id)] - 1} other track(s): Auto + armed, {k} empty placeholder "
                       "clips block the input where it is not live (UNVERIFIED in Live)"
                       + (f"; ⚠ also live with another sound on this rig in {', '.join(both)} — both sound there" if both else "")))

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

    bad = als.validate(root)
    if bad: raise RuntimeError(f"invalid set: {bad[:5]}")
    als.write(root, out_als)

    lu = song.get("lineups", {}).get(lineup, {})
    lines = [f"# {title} · lineup '{lineup}'" + (f" — {lu['name']}" if lu.get("name") else ""),
             f"tempo {tempo} · {len(secs)} sections + STOP · {out_als}",
             f"performers: {', '.join(lu.get('performers') or []) or '(none)'}", ""]
    lines += [f"- {name}: **{tr}** — {note}" for name, tr, note in report]
    lines += [f"- {i['name']}: **{i['status']}** — {i['reason']}" for i in song.get("ignored_tracks", [])]
    lines += _validation_lines(problems, secs)
    open(os.path.join(os.path.dirname(proj), "accounted-for.md"), "w").write("\n".join(lines) + "\n")
    return out_als, report
