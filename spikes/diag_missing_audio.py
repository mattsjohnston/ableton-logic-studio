#!/usr/bin/env python3
"""Spike: why does logic2ableton say N used audio files have no region? (read-only)
Usage: diag_missing_audio.py <project.logicx>"""
import sys, plistlib, glob, os
from logic2ableton import logic_project_data as L
from logic2ableton.logic_project_data import _FLAG_TAKE
lx = sys.argv[1]
alt = sorted(glob.glob(os.path.join(lx, "Alternatives", "*")))[0]
meta = plistlib.load(open(os.path.join(alt, "MetaData.plist"), "rb"))
used = {f.replace("Audio Files/", "").casefold() for f in meta["AudioFiles"]}
data = open(os.path.join(alt, "ProjectData"), "rb").read()
arr = L.decode_project_data(data)
placed_arr, placed_take, placed_comp = set(), {}, set()
for p in arr.placements:
    if p.kind != "audio": continue
    n = arr.audio_files.get(p.audio_file_id, "?").casefold()
    if p.container == 4 and p.take_folder is None: placed_arr.add(n)
    elif p.take_folder is not None: placed_comp.add(n)
for p in arr.folder_regions:
    if p.kind != "audio": continue
    n = arr.audio_files.get(p.audio_file_id, "?").casefold()
    placed_take.setdefault(n, []).append("take" if p.flags & _FLAG_TAKE else "comp-seg")
reg = placed_arr | placed_comp
unplaced = sorted(used - reg)
print(f"MetaData AudioFiles: {len(used)}; AuFl objects: {len(arr.audio_files)}; placed (arrangement+comp): {len(reg & used)}")
print(f"unplaced: {len(unplaced)}")
for n in unplaced:
    fid = [k for k, v in arr.audio_files.items() if v.casefold() == n]
    kinds = placed_take.get(n)
    print(f"  {n:60s} AuFl ids={fid} in-take-folder-entries={kinds}")
print("\nfolder_regions audio entries by container:")
from collections import Counter
c = Counter()
for p in arr.folder_regions:
    if p.kind == "audio": c[(p.container, "take" if p.flags & _FLAG_TAKE else "comp")] += 1
print(dict(c))
print("orphan/warnings:", *arr.warnings, sep="\n  ")
