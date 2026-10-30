#!/usr/bin/env bash
# Supply Brain one-line installer for macOS and Linux: downloads the project, then installs it.
#
#   curl -fsSL https://raw.githubusercontent.com/shivanginiaggarwal13-bit/Capstone/installer/installer/bootstrap.sh | bash
#
# Optional settings (put them before "bash", e.g. "| SB_DIR=~/Projects/SupplyBrain bash"):
#   SB_DIR      where to put the project            (default: ~/SupplyBrain)
#   SB_BRANCH   which branch to download             (default: installer)
#   SB_RUN=1    start the front end when done
# Any arguments are passed on to install.sh, e.g. "| bash -s -- --locked".
set -euo pipefail

REPO="https://github.com/shivanginiaggarwal13-bit/Capstone"
BRANCH="${SB_BRANCH:-installer}"
DIR="${SB_DIR:-$HOME/SupplyBrain}"

step() { printf '\n==> %s\n' "$1"; }

if [ -d "$DIR/.git" ]; then
  step "Updating the existing copy in $DIR"
  git -C "$DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$DIR" checkout -q -B "$BRANCH" FETCH_HEAD
elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
  echo "$DIR already exists and is not a Supply Brain download. Choose another folder with SB_DIR=..."
  exit 1
elif command -v git >/dev/null 2>&1; then
  step "Downloading Supply Brain into $DIR (about 500 MB)"
  git clone --depth 1 --branch "$BRANCH" "$REPO.git" "$DIR"
else
  step "Git not found; downloading a zip of Supply Brain into $DIR (about 500 MB)"
  TMP="$(mktemp -d)"
  curl -fL --progress-bar "https://codeload.github.com/shivanginiaggarwal13-bit/Capstone/zip/refs/heads/$BRANCH" -o "$TMP/sb.zip"
  unzip -q "$TMP/sb.zip" -d "$TMP"
  mkdir -p "$(dirname "$DIR")"
  mv "$TMP"/Capstone-* "$DIR"
  rm -rf "$TMP"
fi

cd "$DIR"
chmod +x installer/*.sh
./installer/install.sh "$@"

if [ "${SB_RUN:-0}" = 1 ]; then
  exec ./installer/run_app.sh
fi
printf 'Supply Brain is in %s\nStart it any time with:\n\n    %s/installer/run_app.sh\n\n' "$DIR" "$DIR"
