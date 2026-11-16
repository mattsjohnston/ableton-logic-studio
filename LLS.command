#!/bin/bash
# Start the Ableton Logic Studio web UI and open it in the browser. Close this window (or press Ctrl-C) to stop it.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run Setup.command (double-click it)."; read -r -p "Press Return to close." _; exit 1
fi
PORT="${LLS_PORT:-8765}"
if curl -s "http://127.0.0.1:$PORT/api/songs" >/dev/null 2>&1; then
  echo "Already running: opening http://127.0.0.1:$PORT/"; open "http://127.0.0.1:$PORT/"; exit 0
fi
if lsof -ti ":$PORT" >/dev/null 2>&1; then PORT=$((PORT + 1)); fi   # something else uses the port
(sleep 1.5; open "http://127.0.0.1:$PORT/") &
echo "Ableton Logic Studio → http://127.0.0.1:$PORT/   (close this window to stop)"
exec .venv/bin/python -m lls ui --port "$PORT"
