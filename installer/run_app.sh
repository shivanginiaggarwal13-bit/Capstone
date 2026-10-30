#!/usr/bin/env bash
# Start the Supply Brain front end. Reads settings from .env; the port can also be passed: ./installer/run_app.sh 8600
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then
  echo "Not installed yet. Run ./installer/install.sh first."; exit 1
fi
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PORT="${1:-${SUPPLY_BRAIN_PORT:-8501}}"
URL="http://localhost:$PORT"
echo "Supply Brain front end: $URL  (Ctrl+C to stop)"
# Headless skips Streamlit's first-run e-mail prompt; the browser is opened here instead.
if [ -z "${SUPPLY_BRAIN_NO_BROWSER:-}" ]; then
  (sleep 4; .venv/bin/python -m webbrowser "$URL" >/dev/null 2>&1) &
fi
exec .venv/bin/python -m streamlit run App/streamlit_app.py --server.port "$PORT" --server.headless true \
  --browser.gatherUsageStats false
