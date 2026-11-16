# Ableton Logic Studio: notes for Claude Code

A band tool. It converts Logic Pro songs into Ableton Live 12 Session sets, with one Live set per song and a re-mappable lineup. Python package `lls` plus a local web UI (`python -m lls ui`, http://127.0.0.1:8765). Mac only. The stage show must run in Live **without this tool running**.

## First-time setup on a new Mac (do this when asked to "set it up")
1. Check prerequisites: Ableton Live 12 (Suite preferred) in /Applications, Arturia Analog Lab V (`/Library/Audio/Plug-Ins/Components/Analog Lab V.component`), Logic Pro. Missing ones are the user's to install. Tell them and don't try.
2. Run `./Setup.command` (non-interactive: `yes "" | ./Setup.command`). It builds `.venv` from Python ≥3.11 and installs `requirements.txt`. If no Python ≥3.11 exists it installs `uv` from astral.sh, then the AbletonOSC Remote Script (pinned commit, bound to 127.0.0.1).
3. Run `./.venv/bin/python -m lls doctor` and fix what it reports.
4. These steps happen in Live's GUI. Walk the user through them (you can't click Live's dropdowns): restart Live; Settings → Tempo & MIDI → Control Surface 1 = AbletonOSC; turn on Track for input ports they play; Settings → Plug-Ins → AU v2 on + Rescan.
5. Run the tests: `./.venv/bin/python -m unittest discover tests` (tests needing the sample project skip on other machines).
6. Start the UI with `./LLS.command` (or `.venv/bin/python -m lls ui`).

Full user guide: `docs/SETUP.md`. Architecture and data model: `README.md`, `docs/model.md`, `docs/STATUS.md`.

## Rules
- **Never modify the user's original `.logicx` projects.** Import makes an APFS clone into `samples/` and reads only that. Never open or save a user's Logic project yourself.
- Don't install big things (Live, Logic sound library packs, Max) without asking. Disk space may be tight.
- Song data lives in `songs/<slug>/` (`song.json` is the source of truth). Stems, samples and built sets are gitignored and stay local. Share songs between Macs with song packages (`python -m lls package export|import`).
- Built sets go to `out/sets/…` with timestamped filenames, so Live's "Save changes?" prompt can't overwrite a new build. Don't change that.
- `.als` generation follows strict ID rules (see `lls/als.py` and `docs/02-spike-als.md`). Always run `als.validate` / the tests after builder changes.

## Common commands
```bash
.venv/bin/python -m lls doctor                      # environment check
.venv/bin/python -m lls ingest <copy.logicx> songs/<slug>
.venv/bin/python -m lls link-stems songs/<slug> <logic export folder>
.venv/bin/python -m lls lineup suggest songs/<slug> <performer ids…> --save <lineup>
.venv/bin/python -m lls build songs/<slug> <lineup>
.venv/bin/python -m lls setlist build setlists/<name>.json
.venv/bin/python -m lls package export songs/<slug>   # → .llsong to send to a bandmate
```
