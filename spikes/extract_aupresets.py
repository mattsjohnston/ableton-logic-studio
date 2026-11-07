#!/usr/bin/env python3
"""Spike 5: pull every AU plugin state (.aupreset plist) out of a Logic ProjectData (read-only).
Usage: extract_aupresets.py <project.logicx> <outdir>"""
import os, plistlib, re, sys, glob

def field(cs, name):
    m = re.search(r"\d+ " + re.escape(name) + r" (\d+) ", cs)
    return cs[m.end():m.end() + int(m.group(1))] if m else None

def extract(logicx, outdir):
    pd = sorted(glob.glob(os.path.join(logicx, "Alternatives", "*", "ProjectData")))[0]
    d = open(pd, "rb").read(); os.makedirs(outdir, exist_ok=True); out = []
    pos = 0
    while (s := d.find(b"<?xml", pos)) != -1:
        e = d.find(b"</plist>", s) + 8; pos = e
        try: p = plistlib.loads(d[s:e])
        except Exception: continue
        if not isinstance(p, dict) or "manufacturer" not in p: continue
        cs = p.get("Controller State", b"").decode("latin1")
        name = field(cs, "OriginalPresetName") or p.get("name", "preset")
        fn = re.sub(r"[^\w .-]", "_", f"{len(out):02d} {name}") + ".aupreset"
        p["name"] = name
        open(os.path.join(outdir, fn), "wb").write(plistlib.dumps(p))
        out.append((hex(s), p["manufacturer"].to_bytes(4, "big").decode(), p["subtype"].to_bytes(4, "big").decode(), name, field(cs, "OriginalPackName")))
    return out

if __name__ == "__main__":
    for row in extract(sys.argv[1], sys.argv[2]): print(*row, sep=" | ")
