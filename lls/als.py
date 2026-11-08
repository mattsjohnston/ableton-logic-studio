"""Low-level .als (gzipped XML) construction from Live-saved prototypes. See docs/02-spike-als.md.

ID rules: global pointee IDs (tags ending 'Target', 'ControllerTargets.N', 'Pointee') are unique file-wide and
< NextPointeeId; track IDs unique among tracks; scene IDs 0..n-1; everything else is list-local.
"""
import binascii, copy, gzip, os, wave, xml.etree.ElementTree as ET

LIVE = os.environ.get("LIVE_APP", "/Applications/Ableton Live 12 Beta.app")
CORE = f"{LIVE}/Contents/App-Resources/Core Library"
BASE = f"{CORE}/Templates/8-Track Template.als"
DEMO = f"{CORE}/Lessons/Demo Songs/Chuck Sutton - Patience (Live 12 Suite Demo).als"
HERE = os.path.dirname(__file__)
AU_TRACK = os.path.join(HERE, "templates", "analoglab_au_track.xml")


def load_gz(p):
    return ET.fromstring(gzip.open(p).read())


def is_global(e):
    t = e.tag
    return "Id" in e.attrib and (t.endswith("Target") or t.startswith("ControllerTargets.") or t == "Pointee")


def setv(elem, path, value):
    elem.find(path).set("Value", str(value).lower() if isinstance(value, bool) else str(value))


def clear(elem):
    for x in list(elem): elem.remove(x)


class Ids:
    def __init__(self, root):
        self.next = max([int(e.get("Id")) for e in root.iter() if is_global(e)] + [0]) + 1

    def renumber(self, elem):
        for e in elem.iter():
            if is_global(e):
                e.set("Id", str(self.next)); self.next += 1


class Protos:
    """Prototype elements copied from Live-saved sets."""
    def __init__(self):
        demo = load_gz(DEMO)
        slot = lambda kind: next(s.find(f"ClipSlot/Value/{kind}") for s in demo.iter("ClipSlot")
                                 if s.find(f"ClipSlot/Value/{kind}") is not None)
        self.audio_clip, self.midi_clip = slot("AudioClip"), slot("MidiClip")
        mt = copy.deepcopy(next(demo.iter("MidiTrack")))
        clear(mt.find("DeviceChain/DeviceChain/Devices"))
        self.midi_track = strip_track(mt)
        self.au_track = strip_track(ET.parse(AU_TRACK).getroot())


def strip_track(t):
    """Remove clips, automation and grouping from a prototype track."""
    t = copy.deepcopy(t)
    for ae in t.iter("AutomationEnvelopes"):
        e = ae.find("Envelopes")
        if e is not None: clear(e)
    for aa in t.iter("ArrangerAutomation"):
        ev = aa.find("Events")
        if ev is not None: clear(ev)
    for parent in list(t.iter()):  # every clip anywhere: session slots, arrangement, take lanes
        for c in list(parent):
            if c.tag in ("AudioClip", "MidiClip"): parent.remove(c)
    t.find("TrackGroupId").set("Value", "-1")
    return t


def _clip_common(c, name, color, start, end):
    for p, v in [("CurrentStart", start), ("CurrentEnd", end), ("Loop/LoopStart", start), ("Loop/LoopEnd", end),
                 ("Loop/StartRelative", 0), ("Loop/LoopOn", True), ("Loop/OutMarker", end),
                 ("Loop/HiddenLoopStart", start), ("Loop/HiddenLoopEnd", end), ("Name", name), ("Color", color),
                 ("LaunchQuantisation", 0), ("Legato", False)]:
        setv(c, p, v)
    clear(c.find("Envelopes/Envelopes"))
    c.set("Time", "0")


def audio_clip(proto, wav, project_dir, name, color, start, end, tempo, loop=True):
    """Session clip playing [start, end) beats of a stem that starts at bar 1, warped at song tempo."""
    c = copy.deepcopy(proto)
    with wave.open(wav) as w: frames, sr = w.getnframes(), w.getframerate()
    secs = frames / sr
    _clip_common(c, name, color, start, end)
    setv(c, "Loop/LoopOn", loop)
    setv(c, "IsWarped", True); setv(c, "WarpMode", 0)
    fr = c.find("SampleRef/FileRef")
    for p, v in [("RelativePathType", 1), ("RelativePath", os.path.relpath(wav, project_dir)), ("Path", os.path.abspath(wav)),
                 ("Type", 1), ("LivePackName", ""), ("LivePackId", ""), ("OriginalFileSize", os.path.getsize(wav)), ("OriginalCrc", 0)]:
        setv(fr, p, v)
    sref = c.find("SampleRef")
    setv(sref, "LastModDate", int(os.path.getmtime(wav))); setv(sref, "DefaultDuration", frames); setv(sref, "DefaultSampleRate", sr)
    wm = c.find("WarpMarkers"); clear(wm)
    ET.SubElement(wm, "WarpMarker", Id="0", SecTime="0", BeatTime="0")
    ET.SubElement(wm, "WarpMarker", Id="1", SecTime=repr(secs), BeatTime=repr(secs * tempo / 60))
    return c


def midi_clip(proto, notes, name, color, length):
    """notes: [(pitch, start, duration, velocity)] relative to clip start."""
    c = copy.deepcopy(proto)
    _clip_common(c, name, color, 0, length)
    n = c.find("Notes"); kt = n.find("KeyTracks"); clear(kt)
    clear(n.find("PerNoteEventStore/EventLists"))
    by_key = {}
    for i, (pitch, t, d, vel) in enumerate(notes, start=1):
        by_key.setdefault(pitch, []).append((t, d, vel, i))
    for k, (pitch, evs) in enumerate(sorted(by_key.items())):
        trk = ET.SubElement(kt, "KeyTrack", Id=str(k)); ns = ET.SubElement(trk, "Notes")
        for t, d, vel, nid in evs:
            ET.SubElement(ns, "MidiNoteEvent", Time=repr(float(t)), Duration=repr(float(d)), Velocity=str(vel),
                          OffVelocity="64", NoteId=str(nid))
        ET.SubElement(trk, "MidiKey", Value=str(pitch))
    nni = n.find("NoteIdGenerator/NextId")
    if nni is not None: nni.set("Value", str(len(notes) + 1))
    return c


def set_au_state(track, aupreset_bytes, name):
    pre = track.find(".//AuPluginDevice/PluginDesc/AuPluginInfo/Preset/AuPreset")
    hx = binascii.hexlify(aupreset_bytes).decode().upper()
    pre.find("Buffer").text = "\n" + "\n".join(hx[i:i + 80] for i in range(0, len(hx), 80)) + "\n"
    pre.find("Name").set("Value", name)


def route_output(track, first_channel):
    """Route a track to an interface stereo pair (e.g. 3 -> outputs 3/4)."""
    r = track.find("DeviceChain/AudioOutputRouting")
    for tag, v in (("Target", f"AudioOut/External/S{(first_channel - 1) // 2}"), ("UpperDisplayString", "Ext. Out"),
                   ("LowerDisplayString", f"{first_channel}/{first_channel + 1}")):
        r.find(tag).set("Value", v)


def fill_slots(track, n_scenes, clips):
    for csl in track.iter("ClipSlotList"):
        if csl.find("ClipSlot") is None: continue  # return tracks keep an empty list
        proto = copy.deepcopy(csl.find("ClipSlot")); clear(proto.find("ClipSlot/Value"))
        clear(csl)
        for i in range(n_scenes):
            s = copy.deepcopy(proto); s.set("Id", str(i))
            hs = s.find("HasStop")
            if hs is not None: hs.set("Value", "true")
            csl.append(s)
    main = track.find("DeviceChain/MainSequencer/ClipSlotList")
    for i, clip in clips.items():
        main[i].find("ClipSlot/Value").append(clip)


def validate(root):
    seen, problems = {}, []
    nxt = int(root.find("LiveSet/NextPointeeId").get("Value"))
    for e in root.iter():
        if is_global(e):
            i = int(e.get("Id"))
            if i in seen: problems.append(f"dup global id {i} ({e.tag}/{seen[i]})")
            if i >= nxt: problems.append(f"id {i} >= NextPointeeId")
            seen[i] = e.tag
    tids = [t.get("Id") for t in root.find("LiveSet/Tracks")]
    if len(tids) != len(set(tids)): problems.append("dup track ids")
    n = len(root.find("LiveSet/Scenes"))
    for t in root.find("LiveSet/Tracks"):
        for csl in t.iter("ClipSlotList"):
            if len(csl) and len(csl) != n: problems.append(f"track {t.get('Id')}: {len(csl)} slots vs {n} scenes")
    return problems


# ---- input routing / monitoring -------------------------------------------------------------------------------
# Encodings learned from Live-saved sets on this Mac (scan of Core Library + samples/*.als), not guessed except where noted:
#  * AudioInputRouting, mono interface input n: Target "AudioIn/External/M{n-1}", Upper "Ext. In", Lower "{n}"
#    (seen: M0/"1" in Core Library "Sample Reference.als", M1/"2" in "Live 11 Intro Empty.als", M2/"3" in
#    "Podcast & Radio.als"). Stereo pair l/l+1: "AudioIn/External/S{(l-1)//2}", Lower "l/l+1" (S0/"1/2", 190 hits).
#    No input: "AudioIn/None", "No Input", "".
#  * MidiInputRouting, all ports + all channels: Target "MidiIn/External.All/-1"; Live 12.4 writes Upper "All Ins",
#    Lower "All Channels" (Chuck Sutton - Patience, Live 12 Standard Demo); older saves "Ext: All Ins"/"".
#    A specific port: the Live 12.4.25 binary holds "MidiIn/External.Dev:" (and the literal
#    "MidiIn/External.Dev:Computer Keyboard/"), i.e. "MidiIn/External.Dev:<port name>/<channel>", and the display
#    prefix "Ch. ". ASSUMED (no saved example on disk): channel suffix is 0-based (ch 1 -> 0), by analogy with -1 = all,
#    and Upper = port name. Live re-derives display strings from Target on load.
#  * MonitoringEnum (DeviceChain/MainSequencer): 0 = In, 1 = Auto, 2 = Off (Live Object Model order). Saved defaults
#    agree: new MIDI/audio tracks save 1 (Default MIDI Track.als, Recording Audio.als); "Sample Reference.als"
#    playback tracks save 2. Schema 12.0_12402 only says RemoteableEnum.
#  * Arm: DeviceChain/MainSequencer/Recorder/IsArmed (schema: MidiRecorder/AudioRecorder IsArmed).
MONITOR_IN, MONITOR_AUTO, MONITOR_OFF = 0, 1, 2


def _set_routing(r, target, upper, lower):
    for tag, v in (("Target", target), ("UpperDisplayString", upper), ("LowerDisplayString", lower)):
        r.find(tag).set("Value", v)


def route_audio_input(track, audio_input):
    """audio_input: interface input number (mono) or [l, r] stereo pair (l odd), as in band.json."""
    r = track.find("DeviceChain/AudioInputRouting")
    if audio_input is None:
        _set_routing(r, "AudioIn/None", "No Input", ""); return
    if isinstance(audio_input, (list, tuple)):
        l = int(audio_input[0])
        if l % 2 == 0 or int(audio_input[1]) != l + 1:
            raise ValueError(f"stereo input must be an odd/even pair like [1, 2], got {audio_input}")
        _set_routing(r, f"AudioIn/External/S{(l - 1) // 2}", "Ext. In", f"{l}/{l + 1}")
    else:
        n = int(audio_input)
        _set_routing(r, f"AudioIn/External/M{n - 1}", "Ext. In", str(n))


def route_midi_input(track, port, channel):
    """port: Live's port name or None (all ins); channel: 1-16 or None (all)."""
    r = track.find("DeviceChain/MidiInputRouting")
    ch = -1 if channel is None else int(channel) - 1
    lower = "All Channels" if channel is None else f"Ch. {int(channel)}"
    if port is None:
        _set_routing(r, f"MidiIn/External.All/{ch}", "All Ins", lower)
    else:
        _set_routing(r, f"MidiIn/External.Dev:{port}/{ch}", port, lower)


def route_main_output(track):
    """Output to the Main/Master bus (the template's own encoding: Target AudioOut/Main, display "Master")."""
    _set_routing(track.find("DeviceChain/AudioOutputRouting"), "AudioOut/Main", "Master", "")


def set_monitoring(track, mode, armed=None):
    ms = track.find("DeviceChain/MainSequencer")
    ms.find("MonitoringEnum").set("Value", str(mode))
    if armed is not None:
        ms.find("Recorder/IsArmed").set("Value", "true" if armed else "false")


def write(root, out_als):
    os.makedirs(os.path.join(os.path.dirname(out_als), "Ableton Project Info"), exist_ok=True)
    with gzip.open(out_als, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8"))
