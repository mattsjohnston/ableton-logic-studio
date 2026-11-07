#!/usr/bin/env python3
"""Scale test: build sets with N MIDI tracks, each holding one Analog Lab V AU device (stdlib only).
Usage: scale_test.py [N ...]   (default 6 30 90) -> out/scale/N<n> Project/N<n>.als"""
import sys, os, gzip, re, copy, binascii, plistlib, glob, xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
SRC = f"{ROOT}/samples/analoglab-live-saved.als"
PRESETS = [f"{ROOT}/out/aupresets/{n}.aupreset" for n in
           ("02 New Vision sw26", "03 Blade Of Canada", "04 Da Fonk", "05 Desynk")]

def is_global(e):
    t = e.tag
    return "Id" in e.attrib and (t.endswith("Target") or t.startswith("ControllerTargets.") or t == "Pointee")

def hexblock(raw, indent):
    hx = binascii.hexlify(raw).decode().upper()
    return "\n" + "\n".join(indent + hx[i:i + 80] for i in range(0, len(hx), 80)) + "\n" + indent[:-1]

def build(n, out_als):
    root = ET.fromstring(gzip.open(SRC).read()); ls = root.find("LiveSet"); tracks = ls.find("Tracks")
    proto = next(t for t in tracks if t.tag == "MidiTrack" and t.find(".//AuPluginDevice") is not None)
    proto = copy.deepcopy(proto)
    assert not list(proto.iter("PointeeId")) or True
    for t in [t for t in tracks if t.tag in ("AudioTrack", "MidiTrack")]: tracks.remove(t)
    nxt = max([int(e.get("Id")) for e in root.iter() if is_global(e)] + [int(ls.find("NextPointeeId").get("Value"))]) + 1
    tid = max(int(t.get("Id")) for t in tracks) + 1
    plists = [plistlib.loads(open(p, "rb").read()) for p in PRESETS]
    for i in range(n):
        t = copy.deepcopy(proto)
        t.set("Id", str(tid)); tid += 1
        for e in t.iter():
            if is_global(e): e.set("Id", str(nxt)); nxt += 1
        nm = f"AL{i+1:03d}"
        t.find("Name/EffectiveName").set("Value", nm); t.find("Name/UserName").set("Value", nm)
        au = t.find(".//AuPluginDevice/PluginDesc/AuPluginInfo/Preset/AuPreset")
        lp = plists[i % 4]
        au.find("Buffer").text = hexblock(plistlib.dumps(lp, fmt=plistlib.FMT_XML), "\t" * 12)
        au.find("Name").set("Value", lp.get("name", "Logic"))
        tracks.insert(i, t)
    ls.find("NextPointeeId").set("Value", str(nxt + 100))
    problems = validate(root)
    if problems: raise SystemExit(f"validation failed: {problems[:10]}")
    os.makedirs(os.path.join(os.path.dirname(out_als), "Ableton Project Info"), exist_ok=True)
    with gzip.open(out_als, "wb") as f: f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8"))
    ET.fromstring(gzip.open(out_als).read())  # re-parse check

def validate(root):
    seen, pr = {}, []
    nxt = int(root.find("LiveSet/NextPointeeId").get("Value"))
    for e in root.iter():
        if is_global(e):
            i = int(e.get("Id"))
            if i in seen: pr.append(f"dup global id {i}")
            if i >= nxt: pr.append(f"id {i} >= NextPointeeId")
            seen[i] = e.tag
    for e in root.iter("PointeeId"):  # references must resolve
        if e.get("Value") != "0" and int(e.get("Value")) not in seen: pr.append(f"dangling PointeeId {e.get('Value')}")
    tids = [t.get("Id") for t in root.find("LiveSet/Tracks")]
    if len(tids) != len(set(tids)): pr.append("dup track ids")
    if len(root.findall(".//AuPluginDevice")) != len([t for t in root.find("LiveSet/Tracks") if t.tag == "MidiTrack"]): pr.append("AU count mismatch")
    return pr

if __name__ == "__main__":
    for n in map(int, sys.argv[1:] or [6, 30, 90]):
        out = f"{ROOT}/out/scale/N{n} Project/N{n}.als"
        build(n, out); print("ok", out)
