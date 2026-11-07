"""Phone fallback: stereo WAV (+ M4A) of everything NOT performed live, from bar 1, with a 2-bar count-in.

Used if Live/laptop dies on stage: play the file from the phone into the PA. The drummer takes the
"click-left" variant (click on the left, music summed to mono on the right).
Assumes a constant tempo (song["tempo"]); per-section tempo changes are not rendered.
"""
import json, os, shutil, subprocess
import numpy as np

from .ingest import write_wav, _read_wav

PLAYBACK = {"backing-audio", "audio-loop", "midi-clip"}
COUNT_IN_BARS = 2
PEAK_DBFS = -1.0


def song_bpb(song):
    return song["time_signature"][0] * 4 / song["time_signature"][1]


def resolve_lineup(song, band, lineup_id):
    """{part_id: {section_id: {"treatment","performer","rig"}}}. Uses lls.lineup.resolve when it exists,
    else falls back to the song's lineup `treatments` map / part default_treatment."""
    try:
        from . import lineup as _lineup
        return _lineup.resolve(song, band, lineup_id)
    except (ImportError, AttributeError):
        pass
    over = song.get("lineups", {}).get(lineup_id, {}).get("treatments", {})
    return {p["id"]: {s["id"]: {"treatment": over.get(p["id"]) or p["default_treatment"], "performer": None, "rig": None}
                      for s in song["sections"]} for p in song["parts"]}


def treatment_at(plan, part, sec):
    return ((plan.get(part["id"]) or {}).get(sec["id"]) or {}).get("treatment") or part["default_treatment"]


def click_track(tempo, beats, sr, bpb, left_only=False):
    """Click for `beats` beats; accent on beat 1 of each bar. Returns (n, 2) float32."""
    spb = 60.0 / tempo
    buf = np.zeros((int(round(beats * spb * sr)) + sr // 10, 2), np.float32)
    tick = np.arange(int(0.03 * sr)) / sr
    for b in range(int(beats)):
        f, amp = (1500, 0.6) if b % bpb == 0 else (1000, 0.4)
        s = (np.sin(2 * np.pi * f * tick) * np.exp(-tick * 120) * amp).astype(np.float32)
        i = int(round(b * spb * sr))
        buf[i:i + len(s), 0] += s
        if not left_only: buf[i:i + len(s), 1] += s
    return buf


def render_fallback(song, song_dir, plan):
    """Mix included stems section by section. Returns (audio (n,2) float32 from bar 1, sr, included, missing)."""
    sr, tempo = song["sample_rate"], song["tempo"]
    total = int(round(song["length_beats"] * 60 / tempo * sr))
    mix = np.zeros((total, 2), np.float32)
    included, missing = [], []
    for part in song["parts"]:
        secs = [s for s in song["sections"] if treatment_at(plan, part, s) in PLAYBACK]
        if not secs: continue
        rel = part.get("stem") or part.get("raw_stem")
        if not rel:
            missing.append({"part": part["id"], "name": part["name"], "reason": "no stem (MIDI-only; export its stem from Logic)"}); continue
        path = os.path.join(song_dir, rel)
        if not os.path.isfile(path):
            missing.append({"part": part["id"], "name": part["name"], "reason": f"stem file not found: {rel}"}); continue
        audio, fsr = _read_wav(path)
        if fsr != sr:
            missing.append({"part": part["id"], "name": part["name"], "reason": f"stem sample rate {fsr} != song {sr}"}); continue
        used = 0
        for s in secs:
            a, b = int(round(s["start"] * 60 / tempo * sr)), int(round(s["end"] * 60 / tempo * sr))
            seg = audio[a:min(b, len(audio), total)]
            if len(seg): mix[a:a + len(seg)] += seg; used += 1
        included.append({"part": part["id"], "name": part["name"], "sections": used})
    return mix, sr, included, missing


def normalize(a, dbfs=PEAK_DBFS):
    peak = float(np.abs(a).max()) if a.size else 0.0
    return a if peak == 0 else a * np.float32(10 ** (dbfs / 20) / peak)


def make_variants(mix, song, sr, with_click=True):
    """{"plain": audio, "click-left": audio}; both begin with COUNT_IN_BARS of count-in click.
    plain = count-in on both sides then the mix. click-left = L: click, R: mono sum of the mix."""
    tempo, bpb = song["tempo"], song_bpb(song)
    ci_beats = int(COUNT_IN_BARS * bpb)
    ci_len = int(round(ci_beats * 60 / tempo * sr))
    out = {"plain": normalize(np.concatenate([click_track(tempo, ci_beats, sr, bpb)[:ci_len], mix]))}
    if with_click:
        ck = click_track(tempo, song["length_beats"], sr, bpb, left_only=True)[:len(mix)]
        if len(ck) < len(mix): ck = np.pad(ck, ((0, len(mix) - len(ck)), (0, 0)))
        body = np.stack([ck[:, 0], mix.mean(axis=1)], axis=1)
        ci = click_track(tempo, ci_beats, sr, bpb, left_only=True)[:ci_len]
        out["click-left"] = normalize(np.concatenate([ci, body]))
    return out


def to_m4a(wav, m4a):
    """AAC .m4a via macOS afconvert. False if afconvert is unavailable or fails."""
    if not shutil.which("afconvert"): return False
    for rate in (["-b", "256000"], []):  # some sample rates (tiny test files) reject 256k; retry with the default
        r = subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", *rate, wav, m4a], capture_output=True, text=True)
        if r.returncode == 0 and os.path.isfile(m4a): return True
    return False


def fallback(song_dir, lineup_id, out_dir, label, band=None, click=True, m4a=True, plan=None):
    """Write `<out_dir>/<label>.wav` (+ `<label> click-left.wav`, + .m4a of each). Returns a report dict."""
    song = json.load(open(os.path.join(song_dir, "song.json")))
    if plan is None: plan = resolve_lineup(song, band or {}, lineup_id)
    mix, sr, included, missing = render_fallback(song, song_dir, plan)
    os.makedirs(out_dir, exist_ok=True)
    files = []
    for kind, audio in make_variants(mix, song, sr, with_click=click).items():
        base = label if kind == "plain" else f"{label} click-left"
        wav = os.path.join(out_dir, base + ".wav")
        write_wav(wav, audio, sr); files.append(wav)
        if m4a:
            m = os.path.join(out_dir, base + ".m4a")
            if to_m4a(wav, m): files.append(m)
    return {"song": song["title"], "files": files, "included": included, "missing": missing,
            "count_in_bars": COUNT_IN_BARS, "silent": not bool(np.abs(mix).max() > 0)}


def report_md(reports):
    L = ["# Phone fallback", ""]
    for r in reports:
        L += [f"## {r['song']}", ""]
        L += [f"- file: `{os.path.basename(f)}`" for f in r["files"]]
        L += [f"- included: {', '.join(i['name'] for i in r['included']) or 'nothing'}"]
        if r["missing"]:
            L += ["", "Missing from fallback:"] + [f"- {m['name']}: {m['reason']}" for m in r["missing"]]
        L += [""]
    return "\n".join(L)
