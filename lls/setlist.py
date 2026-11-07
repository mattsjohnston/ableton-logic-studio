"""Setlist -> out/setlists/<name>/ : one Live project per song (in show order), running-order.md/.html, phone fallback.

Setlist file schema: see docs/model.md ("setlists").
"""
import html, inspect, json, os, re, shutil

from . import fallback as fb

LIVE = ("live-vocal", "live-midi", "live-looped", "live-drums", "live-guitar")


def safe(s):
    return re.sub(r'[/\\:*?"<>|]+', "-", s).strip()


def load(setlist_path):
    sl = json.load(open(setlist_path))
    if not sl.get("songs"): raise ValueError("setlist has no songs")
    for e in sl["songs"]:
        if "song" not in e: raise ValueError(f"setlist entry without 'song': {e}")
    return sl


def _build(song_dir, lineup, out_root, band_path):
    from .build import build
    kw = {"band_path": band_path} if band_path and "band_path" in inspect.signature(build).parameters else {}
    try:
        return build(song_dir, lineup, out_root, **kw)
    except TypeError:
        return build(song_dir, lineup, out_root)


def fmt_time(sec):
    return f"{int(sec // 60)}:{int(round(sec % 60)):02d}" if round(sec % 60) < 60 else f"{int(sec // 60) + 1}:00"


def song_info(song, plan, people):
    """Plain data for one song's running-order entry."""
    bpb = fb.song_bpb(song)
    rows = []
    for s in song["sections"]:
        live, backing, dropped = [], [], []
        for p in song["parts"]:
            r = (plan.get(p["id"]) or {}).get(s["id"]) or {}
            t = r.get("treatment") or p["default_treatment"]
            if t in LIVE:
                who = people.get(r.get("performer"), r.get("performer")) or "?"
                live.append((who, p["name"], t))
            elif t == "dropped": dropped.append(p["name"])
            else: backing.append(p["name"])
        bar = int(s["start"] // bpb) + 1
        rows.append({"id": s["id"], "name": s["name"], "bar": bar, "bars": round((s["end"] - s["start"]) / bpb, 2),
                     "live": sorted(live), "backing": len(backing), "dropped": dropped})
    bars = song["length_beats"] / bpb
    return {"title": song["title"], "tempo": song["tempo"], "key": song.get("key") or "-",
            "ts": f"{song['time_signature'][0]}/{song['time_signature'][1]}", "bars": round(bars, 1),
            "length": fmt_time(song["length_beats"] * 60 / song["tempo"]), "sections": rows}


def _who(row):
    if not row["live"]: return "all backing"
    by = {}
    for who, part, _ in row["live"]: by.setdefault(who, []).append(part)
    return "; ".join(f"{w}: {', '.join(ps)}" for w, ps in by.items())


def running_order_md(sl, infos):
    L = [f"# {sl['name']} - running order", ""]
    for i, d in enumerate(infos, 1):
        e = d["entry"]
        L += [f"## {i:02d}. {d['title']}", "",
              f"lineup `{e.get('lineup', 'default')}` | {d['tempo']:g} BPM | {d['ts']} | key {d['key']} | {d['bars']:g} bars | {d['length']}"]
        if e.get("notes"): L += ["", f"Notes: {e['notes']}"]
        L += ["", "| Bar | Section | Bars | Who plays what |", "|---:|---|---:|---|"]
        for r in d["sections"]:
            L.append(f"| {r['bar']} | {r['name']} | {r['bars']:g} | {_who(r)} ({r['backing']} backing) |")
        L.append("")
    return "\n".join(L)


CSS = """
:root{--bg:#14161a;--fg:#e8e8ea;--mut:#9a9ca3;--line:#2c2f36;--acc:#ffb347}
@media (prefers-color-scheme: light){:root{--bg:#fff;--fg:#111;--mut:#555;--line:#ccc;--acc:#b35c00}}
@media print{:root{--bg:#fff;--fg:#000;--mut:#444;--line:#999;--acc:#000}.song{break-inside:avoid}}
body{background:var(--bg);color:var(--fg);font:14px/1.4 -apple-system,Helvetica,Arial,sans-serif;margin:0 auto;max-width:900px;padding:16px}
h1{font-size:22px}h2{font-size:18px;margin:20px 0 2px;color:var(--acc)}
.meta{color:var(--mut);margin:0 0 6px}.notes{font-style:italic}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid var(--line);padding:3px 6px;text-align:left;vertical-align:top}
td.n,th.n{text-align:right;width:3em}
"""


def running_order_html(sl, infos):
    e = html.escape
    out = [f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
           f"<title>{e(sl['name'])} - running order</title><style>{CSS}</style></head><body>", f"<h1>{e(sl['name'])}</h1>"]
    for i, d in enumerate(infos, 1):
        en = d["entry"]
        out.append(f"<section class=song><h2>{i:02d}. {e(d['title'])}</h2>")
        out.append(f"<p class=meta>lineup {e(str(en.get('lineup', 'default')))} &middot; {d['tempo']:g} BPM &middot; {d['ts']} &middot; "
                   f"key {e(str(d['key']))} &middot; {d['bars']:g} bars &middot; {d['length']}</p>")
        if en.get("notes"): out.append(f"<p class=notes>{e(en['notes'])}</p>")
        out.append("<table><tr><th class=n>Bar</th><th>Section</th><th class=n>Bars</th><th>Who plays what</th></tr>")
        for r in d["sections"]:
            out.append(f"<tr><td class=n>{r['bar']}</td><td>{e(r['name'])}</td><td class=n>{r['bars']:g}</td>"
                       f"<td>{e(_who(r))} <span class=meta>({r['backing']} backing)</span></td></tr>")
        out.append("</table></section>")
    out.append("</body></html>")
    return "\n".join(out)


def build_setlist(setlist_path, out_root="out/setlists", root=None, fallback=True, m4a=True, click=True):
    """Build everything for a setlist. Returns {"dir", "projects", "fallback", "running_order"}."""
    setlist_path = os.path.abspath(setlist_path)
    sl = load(setlist_path)
    root = root or os.path.dirname(os.path.dirname(setlist_path))
    band_path = os.path.join(root, sl.get("band", "band.json"))
    band = json.load(open(band_path)) if os.path.isfile(band_path) else {}
    people = {p["id"]: p["name"] for p in band.get("performers", [])}
    name = sl.get("name") or os.path.splitext(os.path.basename(setlist_path))[0]
    base = os.path.abspath(os.path.join(out_root, safe(name)))
    if os.path.exists(base): shutil.rmtree(base)
    os.makedirs(base)
    infos, projects, fb_reports = [], [], []
    for i, e in enumerate(sl["songs"], 1):
        song_dir = os.path.join(root, "songs", e["song"])
        song = json.load(open(os.path.join(song_dir, "song.json")))
        lineup = e.get("lineup", "default")
        label = f"{i:02d} {safe(song['title'])}"
        stage = os.path.join(base, ".staging", f"{i:02d}")
        als_path, _report = _build(song_dir, lineup, stage, band_path)
        proj = os.path.dirname(als_path)
        dest = os.path.join(base, f"{label} Project")
        shutil.move(proj, dest)
        acc = os.path.join(os.path.dirname(proj), "accounted-for.md")
        if os.path.isfile(acc): shutil.move(acc, os.path.join(base, f"{label} accounted-for.md"))
        projects.append(dest)
        plan = fb.resolve_lineup(song, band, lineup)
        d = song_info(song, plan, people); d["entry"] = e; infos.append(d)
        if fallback:
            fb_reports.append(fb.fallback(song_dir, lineup, os.path.join(base, "fallback"), label,
                                          band=band, click=click, m4a=m4a, plan=plan))
    shutil.rmtree(os.path.join(base, ".staging"), ignore_errors=True)
    ro = os.path.join(base, "running-order.md")
    open(ro, "w").write(running_order_md({**sl, "name": name}, infos))
    open(os.path.join(base, "running-order.html"), "w").write(running_order_html({**sl, "name": name}, infos))
    if fb_reports:
        open(os.path.join(base, "fallback", "fallback-report.md"), "w").write(fb.report_md(fb_reports))
    return {"dir": base, "projects": projects, "fallback": fb_reports, "running_order": ro}
