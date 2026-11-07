#!/usr/bin/env python3
"""Spike 7: map every plugin slot in a Logic ProjectData to its track / channel strip (read-only).

Chain of evidence (all certain, no ordering heuristics):
  Envi track object --(name + strip no. field, logic_project_data._mixer)--> AuCO strip (kind, number)
  AuCO object header: id1 = pool owner (AuCn), id2 = strip object id
  AuCU object (plugin slot; tag b'UCuA' on disk) header: id1 = pool owner, id2 = the owning AuCO's id2,
      u32 at +18 = slot number (it sits where AuCO has its 0xFFFFFFFF sentinel, so scan_objects skips AuCU).
  AuCU body: preset name (+50, 32 bytes), plugin display name (+156, 12 bytes, truncated),
      component codes mfr/type/sub at +168/+172/+176 as byte-reversed fourccs.
  Slot 3 of an instrument strip (kind 0x43) = the instrument; slots > 3 = inserts. (Audio strips: slot 3.. = inserts.)
Usage: map_plugins.py <project.logicx>"""
import glob, os, plistlib, re, struct, sys
from collections import defaultdict
from logic2ableton import logic_project_data as L

def u(d, o, n=4): return int.from_bytes(d[o:o + n], "little")
def cstr(b): return b.split(b"\0")[0].decode("mac_roman", "replace")
def fcc(b): return b[::-1].decode("latin1") if any(b) else ""

# Component codes we can name. Logic-internal plugins use mfr 'EMAG'-style Emagic codes; see KNOWN.
KNOWN = {("CLEM", "", "PRRT"): "Quick Sampler (Q-Sampler)"}

def load(logicx):
    pd = sorted(glob.glob(os.path.join(logicx, "Alternatives", "*", "ProjectData")))[0]
    return open(pd, "rb").read()

def aupreset_at(d, start, end):
    s = d.find(b"<?xml", start, end)
    if s < 0: return None
    e = d.find(b"</plist>", s, end) + 8
    try: p = plistlib.loads(d[s:e])
    except Exception: return None
    cs = p.get("Controller State", b"").decode("latin1")
    m = re.search(r"\d+ OriginalPresetName (\d+) ", cs)
    return (cs[m.end():m.end() + int(m.group(1))] if m else None) or p.get("name")

def plugin_slots(d, limit):
    out = defaultdict(list)
    for m in re.finditer(b"UCuA", d):
        s = m.start()
        if s >= limit or u(d, s + 6) != 14 or u(d, s + 4, 2) != 2: continue
        size = u(d, s + 28); end = s + 32 + size + 16   # the aupreset ends a byte or two past the nominal size
        slot = u(d, s + 18)
        rec = dict(slot=slot, off=s, preset=cstr(d[s + 50:s + 82]), name=cstr(d[s + 156:s + 168]),
                   codes=(fcc(d[s + 168:s + 172]), fcc(d[s + 172:s + 176]), fcc(d[s + 176:s + 180])))
        rec["aupreset"] = aupreset_at(d, s, end) if b"<?xml" in d[s:end] else None
        if rec["name"]: out[u(d, s + 14)].append(rec)
    return out

def main(logicx):
    d = load(logicx)
    arr = L.decode_project_data(d)
    objs = L.scan_objects(d)
    songs = [o.offset for o in objs if o.tag == "Song"]
    limit = songs[1] if len(songs) > 1 else len(d)
    mx = arr.mixer
    pool = max({o.id1 for o in objs if o.tag == "AuCO"}, key=lambda i: sum(o.id1 == i for o in objs if o.tag == "AuCO"))
    id2_key = {o.id2: (u(d, o.offset + 40, 2), u(d, o.offset + 42, 2)) for o in objs if o.tag == "AuCO" and o.id1 == pool}
    key_id2 = {v: k for k, v in id2_key.items()}
    slots = plugin_slots(d, limit)
    midi_tracks = {n for n in (arr.track_name(p.track_id) for p in arr.regions if p.kind == "midi")}
    audio_tracks = {n for n in (arr.track_name(p.track_id) for p in arr.regions if p.kind == "audio")}
    rows = []
    for strip in sorted(mx.strips.values(), key=lambda s: s.index):
        if strip.track_id is None or strip.kind == 0x4C: continue
        recs = sorted(slots.get(key_id2.get((strip.kind, strip.number)), []), key=lambda r: r["slot"])
        plug = [r for r in recs if r["slot"] <= 12 and r["slot"] >= 3 and not r["name"].startswith("bplist")]
        rows.append((strip, plug))
    def fmt(r):
        nm = KNOWN.get(r["codes"], r["name"])
        pr = r["aupreset"] or (r["preset"] if r["preset"] not in ("#Custom#", "") else "")
        return f"{nm}" + (f' [{pr}]' if pr else "")
    for strip, plug in rows:
        kind = "MIDI/instr" if strip.kind == 0x43 else ("audio" if strip.kind == 0x40 else hex(strip.kind))
        if strip.kind == 0x43:
            inst = [r for r in plug if r["slot"] == 3]
            ins = [r for r in plug if r["slot"] > 3]
            head = fmt(inst[0]) if inst else "(none found)"
            print(f"{strip.name}  <{strip.label}, {kind}>\n   instrument: {head}")
            if inst: print(f"      codes mfr/type/sub={inst[0]['codes']}")
        else:
            ins = plug
            print(f"{strip.name}  <{strip.label}, {kind}>")
        print("   inserts:", " | ".join(f"s{r['slot']}:{fmt(r)}" for r in ins) or "-")
    print("\nNotes: Q-Sampler = Quick Sampler (the 8 drum tracks match MetaData QuicksamplerFiles); Drummer regions are named 'Modern 80s' in ProjectData but no Drummer/Drum Machine Designer plugin slot exists.")

if __name__ == "__main__":
    main(sys.argv[1])
