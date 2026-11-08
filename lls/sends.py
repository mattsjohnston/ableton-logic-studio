"""Approximate Logic's shared aux reverbs in Live: return tracks + per-track send levels. See docs/sends.md.

Exported Logic stems carry each track's inserts but NOT the aux sends, so the backing is drier than the record. Send
levels are NOT decoded from the Logic project (the AuCO strip record holds only 0/1 flag words after the fader/pan/IO
fields; the aux strips themselves, their bus inputs and names are readable, the per-strip send amounts are not), so
levels come from heuristics by role (below). Returns are built from the Live-saved Reverb on the template's "A Reverb"
return (the Reverb device and its parameter ranges are Live's own); only Manual values are changed, clamped to range.

Send Manual is linear gain 0.000316 (-70 dB, off) .. 1 (0 dB) (template/demo: Min 0.00031622799, Max 1).
ID rules: Sends/TrackSendHolder Id and SendsPre/SendPreBool Id are list-local (0..n-1); the AutomationTarget /
ModulationTarget inside each holder are global pointee IDs and are renumbered from als.Ids.
"""
import copy
import xml.etree.ElementTree as ET

from . import als

SEND_OFF = 0.0003162277571  # -70 dB, Live's minimum


def db_lin(db):
    return SEND_OFF if db is None else max(SEND_OFF, 10 ** (db / 20))


# Logic aux -> Live Reverb parameters (Manual values; DecayTime/PreDelay ms). Character, not a convolution match.
REVERBS = [
    {"key": "plate-big", "name": "Big Plate (Logic Bus 1, 4.4 s)", "color": 14,
     "params": {"DecayTime": 4400, "PreDelay": 12, "RoomSize": 60, "ShelfHiGain": 0.75, "MixReflect": 0.5, "MixDiffuse": 1.4, "DiffuseDelay": 0.2}},
    {"key": "plate-vocal", "name": "Vocal Plate (Logic Bus 2, 1.1 s)", "color": 9,
     "params": {"DecayTime": 1100, "PreDelay": 10, "RoomSize": 30, "ShelfHiGain": 0.8, "MixReflect": 0.4, "MixDiffuse": 1.5, "DiffuseDelay": 0.2}},
    {"key": "hall", "name": "Hall (Logic Bus 3, 3.9 s)", "color": 25,
     "params": {"DecayTime": 3900, "PreDelay": 28, "RoomSize": 150, "ShelfHiGain": 0.55, "MixReflect": 1.0, "MixDiffuse": 1.2, "DiffuseDelay": 0.3}},
    {"key": "room", "name": "Room (Logic Bus 4, 0.4 s)", "color": 12,
     "params": {"DecayTime": 400, "PreDelay": 2, "RoomSize": 12, "ShelfHiGain": 0.6, "MixReflect": 1.6, "MixDiffuse": 0.8, "DiffuseDelay": 0.1}},
]

# Heuristic send levels in dB by role: {reverb key: dB}. Anything not listed is off.
ROLE_SENDS = {
    "vocal": {"plate-vocal": -12}, "pad": {"hall": -15}, "lead": {"hall": -15}, "keys": {"hall": -15}, "fx": {"hall": -15},
    "drums": {"room": -18}, "bass": {}, None: {"hall": -18},
}
LIVE_VOCAL_SENDS = {"plate-vocal": -12}


def sends_for_role(role, live_audio_vocal=False):
    if live_audio_vocal: return dict(LIVE_VOCAL_SENDS)
    return dict(ROLE_SENDS.get(role, ROLE_SENDS[None]))


def _set_param(dev, name, value):
    p = dev.find(name)
    if p is None or p.find("Manual") is None: return
    rg = p.find("MidiControllerRange")
    if rg is not None:
        value = min(max(value, float(rg[0].get("Value"))), float(rg[1].get("Value")))
    p.find("Manual").set("Value", repr(float(value)))


def make_reverb_return(proto, spec, track_id, ids):
    """Clone the Live-saved Reverb return `proto` into a new return track tuned per `spec`."""
    t = copy.deepcopy(proto)
    t.set("Id", str(track_id)); ids.renumber(t)
    als.setv(t, "Name/EffectiveName", spec["name"]); als.setv(t, "Name/UserName", spec["name"]); als.setv(t, "Color", spec["color"])
    dev = t.find("DeviceChain/DeviceChain/Devices/Reverb")
    als.setv(dev, "UserName", spec["name"])
    lp = dev.find("LastPresetRef/Value")
    if lp is not None: als.clear(lp)  # edited away from the saved preset
    for k, v in spec["params"].items(): _set_param(dev, k, v)
    return t


def set_returns(ls, ids, specs, next_tid):
    """Replace the template's reverb return with one per spec (before "B Delay"); every track and SendsPre get one
    send per return. Returns (names of returns in order, next free track id)."""
    tracks = ls.find("Tracks")
    rets = [t for t in tracks if t.tag == "ReturnTrack"]
    reverb = next((t for t in rets if t.find("DeviceChain/DeviceChain/Devices/Reverb") is not None), None)
    keep = [t for t in rets if t is not reverb]
    new = []
    if reverb is not None:
        for s in specs:
            new.append(make_reverb_return(reverb, s, next_tid, ids)); next_tid += 1
        tracks.remove(reverb)
    final = new + keep  # reverbs first, then the template's remaining returns (B Delay)
    for t in rets:
        if t is not reverb: tracks.remove(t)
    for t in final: tracks.append(t)
    _resize_sends(ls, len(final), ids)
    return [t.find("Name/EffectiveName").get("Value") for t in final], next_tid


def _resize_sends(ls, n, ids):
    """Every track (audio, MIDI and return) gets exactly n TrackSendHolders; returns' are disabled like the template's."""
    for t in ls.find("Tracks"):
        sends = t.find("DeviceChain/Mixer/Sends")
        if sends is None: continue
        proto = copy.deepcopy(sends[0]); enabled = "false" if t.tag == "ReturnTrack" else "true"
        als.clear(sends)
        for i in range(n):
            h = copy.deepcopy(proto); h.set("Id", str(i)); ids.renumber(h)
            h.find("EnabledByUser").set("Value", enabled)
            als.setv(h, "Send/Manual", SEND_OFF)
            sends.append(h)
    sp = ls.find("SendsPre")
    als.clear(sp)
    for i in range(n):
        ET.SubElement(sp, "SendPreBool", Id=str(i), Value="false")


def apply_sends(track, return_keys, levels_db):
    """levels_db: {reverb key: dB}; return_keys: list of keys per return position (others, e.g. Delay, stay off)."""
    holders = list(track.find("DeviceChain/Mixer/Sends"))
    for key, db in levels_db.items():
        if key in return_keys:
            als.setv(holders[return_keys.index(key)], "Send/Manual", db_lin(db))
