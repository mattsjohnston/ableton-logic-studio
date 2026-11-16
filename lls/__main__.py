"""CLI:  python -m lls ingest <project.logicx> <song_dir>
       python -m lls build  <song_dir> [lineup_id] [--band band.json]
       python -m lls lineup suggest  <song_dir> <performer ids...> [--save <lineup_id>] [--band band.json]
       python -m lls lineup validate <song_dir> <lineup_id> [--band band.json]
       python -m lls oneshots <song_dir>
       python -m lls link-stems <song_dir> <logic_export_dir>
       python -m lls setlist build <setlists/name.json> [--no-fallback] [--no-m4a]
       python -m lls package export <song_dir> [out.llsong]
       python -m lls package import <file.llsong> [--slug x] [--force] [--root .]
       python -m lls doctor
       python -m lls ui [--port 8765] [--root .]"""
import json, os, sys


def _opt(argv, name):
    if name in argv:
        i = argv.index(name); v = argv[i + 1]; del argv[i:i + 2]; return v
    return None


def _print_problems(problems):
    groups = {}
    for p in problems: groups.setdefault((p["level"], p["part"], p["message"]), []).append(p["section"])
    for (level, part, msg), ss in sorted(groups.items(), key=lambda g: (g[0][0] != "error", g[0][1] or "")):
        ss = [s for s in ss if s]
        print(f"  {level.upper():5} {part or '(lineup)'}: {msg}" + (f"  [{', '.join(ss)}]" if ss else ""))


def main(argv):
    argv = list(argv)
    band_path = _opt(argv, "--band")
    if len(argv) >= 3 and argv[0] == "ingest":
        from .ingest import ingest
        s = ingest(argv[1], argv[2])
        print(f"{s['title']}: {len(s['parts'])} parts, {len(s['sections'])} sections -> {argv[2]}/song.json")
    elif len(argv) >= 2 and argv[0] == "build":
        from .build import build
        out, report = build(argv[1], argv[2] if len(argv) > 2 else "default", band_path=band_path)
        for name, tr, note in report: print(f"  {name:38} {tr:14} {note}")
        print(out)
    elif len(argv) >= 3 and argv[0] == "link-stems":
        from .stems import link
        for f, what in link(argv[1], argv[2]): print(f"  {f:50} {what}")
    elif len(argv) >= 3 and argv[0] == "setlist" and argv[1] == "build":
        from .setlist import build_setlist
        r = build_setlist(argv[2], fallback="--no-fallback" not in argv, m4a="--no-m4a" not in argv)
        for p in r["projects"]: print(p)
        for f in r["fallback"]:
            for m in f["missing"]: print(f"  fallback missing: {f['song']} / {m['name']}: {m['reason']}")
        print(r["running_order"])
    elif len(argv) >= 2 and argv[0] == "oneshots":
        from . import lineup as L, oneshot
        song = L.load_song(argv[1])
        print(f"{'part':28} {'bar.beat':>9} {'vel':>4} {'len ms':>7} {'peak dBFS':>9}")
        for p, r in oneshot.extract_all(argv[1], song):
            if r is None: print(f"{p['id']:28} (no stem or notes: skipped)"); continue
            m = r[1]
            print(f"{p['id']:28} {oneshot.bar_beat(m['source_beat']):>9} {m['velocity']:>4} {m['length_ms']:7.0f} {m['peak_dbfs']:9.1f}")
    elif len(argv) >= 3 and argv[:2] == ["lineup", "suggest"]:
        from . import lineup as L
        save = _opt(argv, "--save")
        song, band = L.load_song(argv[2]), L.load_band(band_path or "band.json")
        lu = L.suggest(song, band, argv[3:])
        print(json.dumps(lu, indent=2))
        song.setdefault("lineups", {})["_suggested"] = lu
        problems = L.validate(song, band, "_suggested")
        print(f"{sum(p['level'] == 'error' for p in problems)} errors, {sum(p['level'] == 'warn' for p in problems)} warnings")
        _print_problems(problems)
        if save:
            L.save_lineup(argv[2], save, lu); print(f"saved as lineup '{save}' in {argv[2]}/song.json")
    elif len(argv) >= 4 and argv[:2] == ["lineup", "validate"]:
        from . import lineup as L
        song, band = L.load_song(argv[2]), L.load_band(band_path or "band.json")
        problems = L.validate(song, band, argv[3])
        errors = sum(p["level"] == "error" for p in problems)
        print(f"{argv[3]}: {errors} errors, {len(problems) - errors} warnings")
        _print_problems(problems)
        sys.exit(1 if errors else 0)
    elif len(argv) >= 3 and argv[:2] == ["package", "export"]:
        from . import package
        out, m = package.export(argv[2], argv[3] if len(argv) > 3 else None)
        size = os.path.getsize(out) / 1e6
        print(f"{m['title']}: {len(m['files'])} files, {size:.1f} MB -> {out}")
    elif len(argv) >= 3 and argv[:2] == ["package", "import"]:
        from . import package
        slug, root = _opt(argv, "--slug"), _opt(argv, "--root") or "."
        force = "--force" in argv
        try:
            r = package.import_package(argv[2], os.path.join(root, "songs"), slug=slug, force=force)
        except package.PackageError as e:
            print(f"error: {e}", file=sys.stderr); sys.exit(1)
        print(f"{r['title']}: {r['files']} files -> {r['song_dir']}" + (" (replaced)" if r["replaced"] else ""))
        print(f"  lineups: {', '.join(r['lineups']) or '(none)'}" + (f"  (kept from the old copy: {', '.join(r['kept_lineups'])})" if r["kept_lineups"] else ""))
    elif argv[:1] == ["doctor"]:
        from . import doctor
        sys.exit(doctor.main(argv[1:]))
    elif argv[:1] == ["ui"]:
        from .ui.server import main as ui_main
        ui_main(argv[1:])
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
