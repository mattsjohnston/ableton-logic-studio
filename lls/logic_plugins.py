"""Map every plugin slot in a Logic ProjectData to its track (read-only).

Chain of evidence (see spikes/map_plugins.py for the full derivation):
  Envi track --(logic_project_data._mixer)--> AuCO strip (kind, number)
  AuCU record (plugin slot, tag b'UCuA'): id2 = owning AuCO id2, u32 @+18 = slot number,
  preset name @+50 (32 B), display name @+156 (12 B), codes mfr/type/sub @+168/+172/+176 (reversed 4cc).
  Instrument strips (kind 0x43): slot 3 = instrument, >3 = inserts. AU state is an embedded .aupreset plist.
"""
import glob, os, plistlib, re
from collections import defaultdict
from logic2ableton import logic_project_data as L

KNOWN = {("CLEM", "", "PRRT"): "Quick Sampler"}
ANALOG_LAB = ("Artu", "aumu", "Alav")


def _u(d, o, n=4): return int.from_bytes(d[o:o + n], "little")
def _cstr(b): return b.split(b"\0")[0].decode("mac_roman", "replace")
def _fcc(b): return b[::-1].decode("latin1") if any(b) else ""


def project_data_path(logicx):
    return sorted(glob.glob(os.path.join(logicx, "Alternatives", "*", "ProjectData")))[0]


def _aupreset(d, start, end):
    s = d.find(b"<?xml", start, end)
    if s < 0: return None, None
    e = d.find(b"</plist>", s, end) + 8
    try: p = plistlib.loads(d[s:e])
    except Exception: return None, None
    cs = p.get("Controller State", b"").decode("latin1")
    m = re.search(r"\d+ OriginalPresetName (\d+) ", cs)
    name = (cs[m.end():m.end() + int(m.group(1))] if m else None) or p.get("name")
    return name, d[s:e]


def _slots(d, limit):
    out = defaultdict(list)
    for m in re.finditer(b"UCuA", d):
        s = m.start()
        if s >= limit or _u(d, s + 6) != 14 or _u(d, s + 4, 2) != 2: continue
        end = s + 32 + _u(d, s + 28) + 16  # aupreset can run a byte or two past nominal size
        rec = dict(slot=_u(d, s + 18), preset=_cstr(d[s + 50:s + 82]), name=_cstr(d[s + 156:s + 168]),
                   codes=(_fcc(d[s + 168:s + 172]), _fcc(d[s + 172:s + 176]), _fcc(d[s + 176:s + 180])))
        rec["state_name"], rec["aupreset"] = _aupreset(d, s, end) if b"<?xml" in d[s:end] else (None, None)
        if rec["name"]: out[_u(d, s + 14)].append(rec)
    return out


def track_plugins(logicx):
    """{track name: {"strip": kind, "instrument": rec|None, "inserts": [rec]}} ; rec has name/codes/preset/aupreset."""
    d = open(project_data_path(logicx), "rb").read()
    arr = L.decode_project_data(d)
    objs = L.scan_objects(d)
    songs = [o.offset for o in objs if o.tag == "Song"]
    limit = songs[1] if len(songs) > 1 else len(d)
    aucos = [o for o in objs if o.tag == "AuCO"]
    pool = max({o.id1 for o in aucos}, key=lambda i: sum(o.id1 == i for o in aucos))
    key_id2 = {(_u(d, o.offset + 40, 2), _u(d, o.offset + 42, 2)): o.id2 for o in aucos if o.id1 == pool}
    slots = _slots(d, limit)
    out = {}
    for strip in sorted(arr.mixer.strips.values(), key=lambda s: s.index):
        if strip.track_id is None or strip.kind == 0x4C: continue
        recs = sorted(slots.get(key_id2.get((strip.kind, strip.number)), []), key=lambda r: r["slot"])
        plug = [r for r in recs if 3 <= r["slot"] <= 12 and not r["name"].startswith("bplist")]
        for r in plug:
            r["display"] = KNOWN.get(r["codes"], r["name"].strip())
            if r["codes"] == ANALOG_LAB: r["display"] = "Analog Lab V"
        inst = [r for r in plug if r["slot"] == 3] if strip.kind == 0x43 else []
        out[strip.name] = {"strip": "instrument" if strip.kind == 0x43 else ("audio" if strip.kind == 0x40 else hex(strip.kind)),
                           "instrument": inst[0] if inst else None,
                           "inserts": [r for r in plug if r not in inst]}
    return out
