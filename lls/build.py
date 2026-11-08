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
import copy, json, os, shutil, subprocess, tempfile
from collections import Counter
import numpy as np

from . import als, chains, lineup as LU, sends as SD
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


CUE_VOICES = ("Samantha", "Alex")  # clear built-in macOS voices, tried in order; then the system default
CUE_RATE = 220  # words per minute
CUE_GAIN = 0.8


def _speak(text, sr):
    """Spoken text -> float32 stereo (n, 2) at sr, via macOS `say` + `afconvert`. Falls back to the default voice."""
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = os.path.join(tmp, "c.aiff"), os.path.join(tmp, "c.wav")
        for voice in CUE_VOICES + (None,):
            cmd = ["say"] + (["-v", voice] if voice else []) + ["-r", str(CUE_RATE), "-o", aiff, text]
            if subprocess.run(cmd, capture_output=True).returncode == 0 and os.path.exists(aiff): break
        else:
            raise RuntimeError(f"`say` failed for cue {text!r}")
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{sr}", "-c", "2", aiff, wav], check=True, capture_output=True)
        return _read_wav(wav)[0]


def cue_text(section):
    return section.get("cue") or section["name"]


def cues_wav(path, song, speak=None):
    """Full-length Cues.wav from bar 1 at song tempo: one bar before each section (not the first) a short spoken cue naming
    it, starting on that bar's downbeat and trimmed to fit inside the bar. Returns [(section index, start_sec, end_sec)]."""
    speak = speak or _speak
    tempo, sr, secs = song["tempo"], song["sample_rate"], song["sections"]
    bpb = song["time_signature"][0] * 4 / song["time_signature"][1]
    bar_n = int(bpb * 60 / tempo * sr)
    buf = np.zeros((int(song["length_beats"] * 60 / tempo * sr) + sr, 2), np.float32)
    spoken, placed = {}, []
    for i, s in enumerate(secs):
        at = s["start"] - bpb
        if at < 0: continue  # first section: the click count-in covers it
        text = cue_text(s)
        if text not in spoken: spoken[text] = np.asarray(speak(text, sr), np.float32) * CUE_GAIN
        a = spoken[text][:bar_n].copy()
        f = min(len(a), int(0.02 * sr))
        if f: a[-f:] *= np.linspace(1, 0, f)[:, None]
        k = int(at * 60 / tempo * sr)
        buf[k:k + len(a)] += a
        placed.append((i, k / sr, (k + len(a)) / sr))
    write_wav(path, buf, sr)
    return placed


def follow_next(scene, beats):
    """Scene follow action 'launch next scene after `beats` beats'.
    Evidence (Live-saved sets under the Core Library, e.g. 'Live 11 Intro Empty.als', 'Live 11 Suite Empty.als',
    'Live 11 Standard Empty.als', 'Live 11 Lite Empty.als'): every enabled scene FollowAction there has FollowActionA=4,
    FollowChanceA=100, FollowActionB=0, FollowChanceB=0, IsLinked=false, LoopIterations=1, FollowTime in {32, 60, 64, 68}.
    4 = Next matches the Live Object Model follow-action order (0 None, 1 Stop, 2 Again, 3 Previous, 4 Next, 5 First,
    6 Last, 7 Any, 8 Other, 9 Jump). FollowTime values like 60 and 68 are not bar multiples, so the unit is beats.
    UNVERIFIED in a running Live: the enum and unit are well supported by those saves, but no on-disk set pairs a scene
    follow time with a known scene length, so confirm by opening the built set."""
    for p, v in [("FollowAction/FollowTime", beats), ("FollowAction/IsLinked", False), ("FollowAction/LoopIterations", 1),
                 ("FollowAction/FollowActionA", 4), ("FollowAction/FollowActionB", 0), ("FollowAction/FollowChanceA", 100),
                 ("FollowAction/FollowChanceB", 0), ("FollowAction/JumpIndexA", 0), ("FollowAction/JumpIndexB", 0),
                 ("FollowAction/FollowActionEnabled", True)]:
        als.setv(scene, p, v)


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


def build(song_dir, lineup="default", out_root="out/sets", band_path=None, cues=None, auto_advance=None, versioned=True):
    """cues / auto_advance: None -> song.json "options" ({"cues": bool, "auto_advance": bool}, both default false)."""
    song = LU.load_song(song_dir)
    band = _find_band(band_path)
    opts = song.get("options") or {}
    cues = bool(opts.get("cues", False) if cues is None else cues)
    auto_advance = bool(opts.get("auto_advance", False) if auto_advance is None else auto_advance)
    returns = bool(opts.get("returns", True))  # Logic-style aux reverb returns + sends (lls.sends)
    click_out = int(((band.get("interface") or {}).get("outputs") or {}).get("click", [CLICK_OUT])[0])
    res = LU.resolve(song, band, lineup)
    problems = LU.validate(song, band, lineup)
    title, tempo, sr = song["title"], song["tempo"], song["sample_rate"]
    bpb = song["time_signature"][0] * 4 / song["time_signature"][1]
    proj = os.path.abspath(os.path.join(out_root, title, lineup, f"{title} Project"))
    if os.path.exists(proj): shutil.rmtree(proj)
    smp = os.path.join(proj, "Samples"); os.makedirs(smp)
    # Each rehearsal build gets its own filename: if Live still has an older build of this set open with unsaved changes,
    # its "Save changes?" prompt can only ever write to the OLD file, never over the new build. Setlist (show) builds use
    # the plain name (versioned=False).
    import time as _time
    out_als = os.path.join(proj, f"{title} {_time.strftime('%m%d-%H%M%S')}.als" if versioned else f"{title}.als")

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
    send_plan = []  # (track, {reverb key: dB}) — heuristic sends by role, applied once the returns exist
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
                st = add_track(a_proto, name + (" (backing)" if V else ""), COLORS["backing-audio"], clips)
                send_plan.append((st, SD.sends_for_role(LU.role_hint(part))))
                notes.append(f"{len(clips)} section clips from {'exported' if part.get('stem') else 'RAW (no FX)'} stem")
            report.append((name, _summary(ts), "; ".join(notes) or "nothing to play")); continue

        # MIDI part
        if A:
            if stem_ok:
                clips = stem_clips(part, name, A, COLORS["backing-audio"])
                st = add_track(a_proto, name + (" (stem)" if (M or L or D) else ""), COLORS["backing-audio"], clips)
                send_plan.append((st, SD.sends_for_role(LU.role_hint(part))))
                notes.append(f"{len(clips)} audio clips from {'exported' if part.get('stem') else 'RAW (no FX)'} stem")
            elif LU.drum_standin_ok(part) and not LU.playable(part):
                # drum part whose stem is missing/silent: MIDI through a Simpler (printed > factory > Core Library stand-in)
                from . import drumrack
                path, src = drumrack.resolve_sample(part, song, song_dir)
                clips = midi_clips(part, A, COLORS["midi-clip"])
                tinfo = []
                proto = drumrack.make_simpler_track(P, path, proj, name, COLORS["midi-clip"],
                                                    root=drumrack.main_pitch(song_dir, part), info=tinfo)
                t = add_track(proto, name, COLORS["midi-clip"], clips)
                als.set_monitoring(t, als.MONITOR_AUTO, armed=False)
                label = {"printed": "printed one-shot", "factory": "Logic factory sample"}.get(src)
                if src == "standin":
                    notes.append(f"{len(clips)} MIDI clips → Simpler with {os.path.basename(path)} — ⚠ stand-in sample (Logic sample not installed)")
                    problems.append({"level": "warn", "part": part["id"], "section": None, "code": "standin-sample",
                                     "message": f"⚠ stand-in sample (Logic sample not installed): {os.path.basename(path)}"})
                elif label:
                    notes.append(f"{len(clips)} MIDI clips → Simpler with {label} (stem missing/silent)")
                else:
                    notes.append(f"⚠ {len(clips)} MIDI clips, no sample available ({'; '.join(tinfo)})")
                A = []
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
            send_plan.append((t, SD.sends_for_role(LU.role_hint(part))))
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
        send_plan.append((t, SD.sends_for_role("vocal", live_audio_vocal=True) if label == "Vocal" else {}))
        als.set_monitoring(t, als.MONITOR_IN, armed=False)
        als.route_main_output(t)
        fx = rig.get("fx", "default")
        if fx not in chains.FX_MODES: raise ValueError(f"rig {rig_id}: fx must be one of {chains.FX_MODES}, got {fx!r}")
        devs = [] if fx == "none" else {"Vocal": chains.vocal_chain, "Bass": chains.bass_chain}.get(label, chains.guitar_chain)(P)
        chains.insert_chain(t, devs, ids)  # replaces the template's generic channel strip
        fxnote = f"{label.lower()} chain: {chains.describe(devs)}" if devs else "fx: none (no devices)"
        report.append((f"{label} input ({who}/{rig_id})", "live-vocal" if label == "Vocal" else "live-guitar",
                       f"interface input {rig.get('audio_input')}, monitoring In, not armed, output Main; {fxnote}; covers {', '.join(pids)}"))
    built[:] = built[n_parts_tracks:] + built[:n_parts_tracks]  # live inputs first

    make_drum_track = _drum_maker()
    for (who, rig_id), dparts in drum_groups.items():
        rig = LU._rig(band, who, rig_id) or {}
        pids = [p["id"] for p in dparts]
        if make_drum_track:
            info = []
            proto = make_drum_track(P, pids, song, song_dir, project_dir=proj, info=info)
            if isinstance(proto, tuple): proto = proto[0]
            warn = [i for i in info if not i.startswith("pad source:")]
            note = f"Drum Rack from {len(pids)} parts" + (f" (⚠ {'; '.join(warn)})" if warn else "")
            note += " [" + "; ".join(i[len("pad source: "):] for i in info if i.startswith("pad source:")) + "]"
        else:
            proto = P.midi_track
            note = f"⚠ lls.drumrack missing: plain MIDI track, NO DRUM RACK for {len(pids)} parts"
        t = add_track(proto, f"Drums (live: {who})", COLORS["live-drums"], {})
        send_plan.append((t, SD.sends_for_role("drums")))
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
    als.route_output(ct, click_out)
    report.append(("Click (generated)", "click", f"routed to outputs {click_out}/{click_out + 1}"))
    if cues:
        cwp = os.path.join(smp, "Cues.wav"); placed = cues_wav(cwp, song)
        caudio, csr = _read_wav(cwp)
        clips = {i: als.audio_clip(P.audio_clip, cwp, proj, f"Cues · {s['name']}", COLORS["click"], s["start"], s["end"], tempo, loop=False)
                 for i, s in enumerate(secs) if active(caudio, csr, tempo, s["start"], s["end"])}
        cut = add_track(a_proto, "Cues", COLORS["click"], clips)
        als.route_output(cut, click_out)
        report.append(("Cues (generated)", "cues", f"{len(placed)} spoken cues one bar before each section, routed to outputs "
                       f"{click_out}/{click_out + 1} (separate track from the click)"))

    for i, t in enumerate(built): tracks.insert(i, t)
    if returns:
        ret_names, next_tid = SD.set_returns(ls, ids, SD.REVERBS, next_tid)
        keys = [r["key"] for r in SD.REVERBS]
        for t, levels in send_plan: SD.apply_sends(t, keys, levels)
        report.append(("Returns (Logic aux approximation)", "sends",
                       f"{', '.join(ret_names)}; send levels are HEURISTIC by role (not decoded from Logic), "
                       f"{sum(1 for _, l in send_plan if l)} tracks send"))
    for t in tracks:
        if t.tag == "ReturnTrack": als.fill_slots(t, n, {})

    scp = ls.find("Scenes"); sproto = copy.deepcopy(scp.find("Scene")); als.clear(scp)
    for i, s in enumerate(secs + [{"name": "STOP", "start": None}]):
        sc = copy.deepcopy(sproto); sc.set("Id", str(i))
        bar = f" (bar {int(s['start'] // bpb) + 1})" if s["start"] is not None else ""
        als.setv(sc, "Name", s["name"] + bar); als.setv(sc, "Color", 0 if s["name"] == "STOP" else SCENE_COLORS[i % len(SCENE_COLORS)])
        als.setv(sc, "Tempo", tempo); als.setv(sc, "IsTempoEnabled", s["name"] != "STOP")
        if auto_advance and s["name"] != "STOP" and not s.get("vamp"):
            follow_next(sc, s["end"] - s["start"])
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
