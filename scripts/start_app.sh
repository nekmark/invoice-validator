#!/bin/bash
# Starts the Invoice Validator on http://127.0.0.1:8501 (this computer only) and
# opens it in the browser. If it's already running, just opens the browser.
# Closing this Terminal window (or Ctrl+C) stops the app.
set -u
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8501
URL="http://127.0.0.1:${PORT}"
cd "$APP_DIR" || exit 1

open_browser() { [ "${NO_BROWSER:-0}" = "1" ] || open "$URL"; }

if curl -s -o /dev/null -m 2 "$URL/_stcore/health"; then
  echo "🎀 Invoice Validator is already running — opening $URL"
  open_browser
  exit 0
fi

if [ ! -x venv/bin/streamlit ]; then
  echo "The app's Python environment is missing. See README.md → One-time setup."
  read -r -p "Press Enter to close…" _
  exit 1
fi

echo "🎀 Starting Invoice Validator on $URL (this computer only)…"
echo "   Keep this window open while you use it. Close it to stop the app."
(
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null -m 1 "$URL/_stcore/health"; then open_browser; exit 0; fi
    sleep 0.5
  done
) &
exec venv/bin/streamlit run app.py --server.headless true --server.port "$PORT"
