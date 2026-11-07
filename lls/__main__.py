"""CLI:  python -m lls ingest <project.logicx> <song_dir>
       python -m lls build  <song_dir> [lineup]
       python -m lls ui     [--port 8765] [--root .]"""
import sys

def main(argv):
    if len(argv) >= 3 and argv[0] == "ingest":
        from .ingest import ingest
        s = ingest(argv[1], argv[2])
        print(f"{s['title']}: {len(s['parts'])} parts, {len(s['sections'])} sections -> {argv[2]}/song.json")
    elif len(argv) >= 2 and argv[0] == "build":
        from .build import build
        out, report = build(argv[1], argv[2] if len(argv) > 2 else "default")
        for name, tr, note in report: print(f"  {name:38} {tr:14} {note}")
        print(out)
    elif argv[:1] == ["ui"]:
        from .ui.server import main as ui_main
        ui_main(argv[1:])
    else:
        print(__doc__); sys.exit(1)

if __name__ == "__main__":
    main(sys.argv[1:])
