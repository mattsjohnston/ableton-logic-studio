#!/usr/bin/env python3
"""Spike 5b: write Logic Analog Lab states (.aupreset) into a Live-saved set's AU + VST3 plugin blocks."""
import sys, gzip, re, binascii, plistlib, base64, os, shutil, xml.etree.ElementTree as ET

def hexblock(raw, indent):
    hx = binascii.hexlify(raw).decode().upper()
    return "\n" + "\n".join(indent + hx[i:i + 80] for i in range(0, len(hx), 80)) + "\n" + indent[:-1]

src, au_preset, vst_preset, out = sys.argv[1:5]
r = ET.fromstring(gzip.open(src).read())
au = next(r.iter("AuPluginDevice")).find("PluginDesc/AuPluginInfo/Preset/AuPreset")
live_keys = set(plistlib.loads(binascii.unhexlify(re.sub(r"\s", "", au.find("Buffer").text))).keys())
lp = plistlib.loads(open(au_preset, "rb").read())
print("Live AU plist keys:", sorted(live_keys)); print("Logic plist keys:  ", sorted(lp.keys()))
au.find("Buffer").text = hexblock(plistlib.dumps(lp, fmt=plistlib.FMT_XML), "\t" * 12)
au.find("Name").set("Value", lp.get("name", "Logic"))
v = next(r.iter("PluginDevice")).find("PluginDesc/Vst3PluginInfo/Preset/Vst3Preset")
vp = plistlib.loads(open(vst_preset, "rb").read())
v.find("ProcessorState").text = hexblock(vp["Controller State"], "\t" * 12)  # base64 already decoded by plistlib
os.makedirs(os.path.join(os.path.dirname(out), "Ableton Project Info"), exist_ok=True)
gzip.open(out, "wb").write(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(r, encoding="utf-8"))
print("wrote", out)
