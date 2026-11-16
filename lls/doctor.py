"""Setup checklist for a Mac that will run lls:  python -m lls doctor   (UI: GET /api/doctor)

Each check: {id, label, ok, critical, detail, fix}. Critical = builds can't work without it.
"""
import importlib, os, sys

ANALOG_LAB = "/Library/Audio/Plug-Ins/Components/Analog Lab V.component"
LOGIC_APPS = ("/Applications/Logic Pro.app", "/Applications/Logic Pro X.app")
MIN_PY = (3, 9)
PACKAGES = (("numpy", True, "audio analysis"), ("logic2ableton", True, "Logic project reader, used by ingest and build"),
            ("pythonosc", False, "python-osc, optional: Live engine in the UI"))


def abletonosc_dir():
    return os.path.expanduser("~/Music/Ableton/User Library/Remote Scripts/AbletonOSC")


def _check(id, label, ok, critical, detail="", fix=""):
    return {"id": id, "label": label, "ok": bool(ok), "critical": critical, "detail": detail, "fix": "" if ok else fix}


def checks():
    from . import liveapp
    out = []
    v = sys.version_info
    out.append(_check("python", "Python", v[:2] >= MIN_PY, True, f"{v.major}.{v.minor}.{v.micro} ({sys.executable})",
                      f"Install Python {MIN_PY[0]}.{MIN_PY[1]}+ (python.org) and recreate .venv"))
    for mod, critical, why in PACKAGES:
        try:
            m = importlib.import_module(mod); ok, detail = True, getattr(m, "__version__", "") or "installed"
        except Exception as e:  # noqa: BLE001 — any import failure is a failed check
            ok, detail = False, f"{type(e).__name__}: {e}"
        out.append(_check(f"pkg-{mod}", f"Python package {mod} · {why}", ok, critical, detail,
                          ".venv/bin/pip install -r requirements.txt"))

    try:
        app = liveapp.find_live()
    except liveapp.LiveNotFound as e:
        app = None
        out.append(_check("live", "Ableton Live 12", False, True, str(e).replace(liveapp.INSTALL_HINT, "").strip(), liveapp.INSTALL_HINT))
    if app:
        others = [a.describe() for a in liveapp.find_all() if a.path != app.path]
        edition = app.edition or "edition not in the app name"
        detail = f"{app.path} · version {app.short_version} · {edition}" + (" · beta" if app.beta else "")
        if others: detail += f" · also installed: {', '.join(others)}"
        if os.environ.get("LIVE_APP"): detail += " · chosen by $LIVE_APP"
        out.append(_check("live", "Ableton Live 12", True, True, detail))
        sch = app.schema()
        out.append(_check("live-schema", "Live set format", app.schema_ok(), True,
                          f"newest schema {sch or 'unknown'} · our templates {liveapp.PROTO_SCHEMA}",
                          app.schema_warning() or ""))
        if app.edition and app.edition != "Suite":
            out.append(_check("live-edition", "Live edition", False, False, f"{app.edition}: lls is developed on Suite",
                              "Suite is recommended; on Standard/Intro some Core Library presets may be missing (see below)."))
        base = app.base_template()
        out.append(_check("core-template", "Core Library: 8-Track Template", os.path.isfile(base), True, base,
                          "Reinstall Live 12 (the Core Library ships inside the app)"))
        demo = app.demo_song()
        out.append(_check("core-demo", "Core Library: Patience demo set (prototypes)", bool(demo), True,
                          os.path.basename(demo) if demo else f"none matching '{liveapp.DEMO_GLOB}'",
                          "Reinstall Live 12 Suite or Standard; lls copies clip, track and device prototypes from this set"))
        from . import chains
        specs = [(n, rel) for n, spec in (("vocal", chains.VOCAL), ("bass", chains.BASS), ("guitar", chains.GUITAR))
                 for rel, _ in spec]
        fx = os.path.join(app.core_library, "Devices", "Audio Effects")
        missing = [f"{n}: {rel}" for n, rel in specs if not os.path.isfile(os.path.join(fx, rel))]
        out.append(_check("core-presets", "Core Library: input-chain device presets", not missing, False,
                          f"{len(specs) - len(missing)}/{len(specs)} present" + (f"; missing {', '.join(missing)}" if missing else ""),
                          "Missing presets fall back to plainer devices from the demo set (Amp/Cabinet → Saturator). "
                          "Live 12 Suite has them all."))
        samples = os.path.join(app.core_library, "Samples", "One Shots", "Drums")
        out.append(_check("core-samples", "Core Library: drum one-shots (stand-ins)", os.path.isdir(samples), False,
                          samples, "Without them, drum parts whose Logic samples are missing stay silent"))

    out.append(_check("analoglab", "Analog Lab V (Audio Unit)", os.path.isdir(ANALOG_LAB), True, ANALOG_LAB,
                      "Install Analog Lab V with Arturia Software Center and open it once to authorise it"))
    logic = next((p for p in LOGIC_APPS if os.path.isdir(p)), None)
    out.append(_check("logic", "Logic Pro (optional: only to import new songs)", bool(logic), False,
                      logic or "not found", "Only needed to import songs from .logicx; song packages work without it"))
    osc = abletonosc_dir()
    out.append(_check("abletonosc", "AbletonOSC remote script (optional: Live engine in the UI)",
                      os.path.isfile(os.path.join(osc, "__init__.py")) or os.path.isdir(osc), False, osc,
                      "Copy AbletonOSC (github.com/ideoforms/AbletonOSC) to that folder, restart Live, then choose it under "
                      "Settings → Link, Tempo & MIDI → Control Surface"))
    return out


def report():
    cs = checks()
    return {"ok": all(c["ok"] for c in cs if c["critical"]), "critical_missing": [c["label"] for c in cs if c["critical"] and not c["ok"]],
            "checks": cs}


def main(argv=()):
    r = report()
    for c in r["checks"]:
        mark = "✓" if c["ok"] else "✗"
        print(f" {mark} {c['label']}" + (f"  — {c['detail']}" if c["detail"] else ""))
        if not c["ok"] and c["fix"]: print(f"      fix: {c['fix']}")
    print("\nAll critical checks passed." if r["ok"] else f"\nMissing (critical): {', '.join(r['critical_missing'])}")
    return 0 if r["ok"] else 1
