#!/usr/bin/env bash
# Supply Brain installer for macOS and Linux.
#
#   ./installer/install.sh               install with the latest compatible packages
#   ./installer/install.sh --locked      install the exact versions the project was tested with
#   ./installer/install.sh --with-shap   also install the optional SHAP / XGBoost packages
#   ./installer/install.sh --skip-tests  skip the agent test suite at the end
#   PYTHON=/path/to/python3 ./installer/install.sh   use a specific Python (3.11 or newer)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOCKED=0; WITH_SHAP=0; SKIP_TESTS=0
for arg in "$@"; do
  case "$arg" in
    --locked) LOCKED=1 ;;
    --with-shap) WITH_SHAP=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }

step "Finding Python 3.11 or newer"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$(command -v "$c")"; break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi
echo "Using $PY ($("$PY" --version))"

step "Creating the virtual environment (.venv)"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
else
  echo ".venv already exists, reusing it"
fi
VPY="$ROOT/.venv/bin/python"

step "Installing packages"
"$VPY" -m pip install --upgrade pip --quiet
if [ "$LOCKED" = 1 ]; then
  "$VPY" -m pip install -r installer/requirements-lock.txt
else
  "$VPY" -m pip install -r requirements.txt
fi
if [ "$WITH_SHAP" = 1 ]; then
  "$VPY" -m pip install -r installer/requirements-optional.txt
fi

step "Preparing settings"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example (add API keys there if you want the Claude or NVIDIA agent profiles)"
else
  echo ".env already exists, left unchanged"
fi
mkdir -p "App/runs"
chmod +x installer/run_app.sh

step "Checking the installation"
"$VPY" installer/check_install.py

if [ "$SKIP_TESTS" = 0 ]; then
  step "Running the agent test suite"
  "$VPY" -m pytest -q Agents/tests
fi

printf '\nSupply Brain is installed.\nStart the front end with:\n\n    ./installer/run_app.sh\n\n'
