#!/bin/bash
# Ableton Logic Studio: one-time setup. Double-click this file in Finder (or run ./Setup.command).
# Safe to run again: it rebuilds the Python environment and re-checks everything.
set -e
cd "$(dirname "$0")"
echo ""
echo "=== Ableton Logic Studio · setup ==="
echo ""

# 1. Find Python 3.11+ (never the macOS /usr/bin/python3 stub: it is 3.9 and may pop up an Xcode install dialog)
PY=""
for c in "$HOME/.local/bin/python3.12" /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 \
         /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.11/bin/python3; do
  if [ -x "$c" ] && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ] && command -v uv >/dev/null 2>&1; then PY="$(uv python find '>=3.11' 2>/dev/null || true)"; fi
if [ -z "$PY" ]; then
  echo "• Python 3.11+ not found. Installing it with uv (a small, self-contained Python installer from astral.sh)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.12
  PY="$(uv python find 3.12)"
fi
echo "• Python: $PY ($("$PY" --version))"

# 2. Project environment + packages
rm -rf .venv
"$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
echo "• Packages installed (requirements.txt)"

# 3. AbletonOSC (optional: lets the web UI start/stop scenes in Live during rehearsal)
echo ""
read -r -p "Install AbletonOSC so the UI can play sections in Live? [Y/n] " ans || ans=""
if [[ ! "$ans" =~ ^[Nn] ]]; then bash scripts/install_abletonosc.sh; fi

# 4. Check the machine
echo ""
.venv/bin/python -m lls doctor || true
echo ""
echo "Setup finished. Start the app any time by double-clicking LLS.command."
echo "(See docs/SETUP.md for the Live settings to check once, e.g. enabling AbletonOSC.)"
