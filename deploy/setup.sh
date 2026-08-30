#!/usr/bin/env bash
# Provision one exact reviewed Glassbox revision on Debian/Ubuntu.
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "usage: $0 <full-40-character-reviewed-commit-sha>" >&2
  exit 64
fi

REVIEWED_SHA="${1,,}"
APP="${GLASSBOX_APP:-/opt/glassbox}"
REPO_DIR="$APP/repo"
REPO_URL="${GLASSBOX_REPO_URL:-https://github.com/jacklachan/alpaca.git}"
PYTHON_BIN="${GLASSBOX_PYTHON_BIN:-python3.12}"
PIP_BIN="${GLASSBOX_PIP_BIN:-$APP/.venv/bin/pip}"
GIT_BIN="${GLASSBOX_GIT_BIN:-git}"
CP_BIN="${GLASSBOX_CP_BIN:-cp}"
CHOWN_BIN="${GLASSBOX_CHOWN_BIN:-chown}"
CHMOD_BIN="${GLASSBOX_CHMOD_BIN:-chmod}"
SYSTEMCTL_BIN="${GLASSBOX_SYSTEMCTL_BIN:-systemctl}"

# Input validation above must stay before the first system mutation.
apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv git

id -u glassbox &>/dev/null || \
  useradd --system --home "$APP" --shell /usr/sbin/nologin glassbox

mkdir -p "$APP/state" "$REPO_DIR"
"$GIT_BIN" -C "$REPO_DIR" init --quiet
if "$GIT_BIN" -C "$REPO_DIR" remote get-url origin &>/dev/null; then
  "$GIT_BIN" -C "$REPO_DIR" remote set-url origin "$REPO_URL"
else
  "$GIT_BIN" -C "$REPO_DIR" remote add origin "$REPO_URL"
fi
"$GIT_BIN" -C "$REPO_DIR" fetch --depth 1 origin "$REVIEWED_SHA"
"$GIT_BIN" -C "$REPO_DIR" checkout --detach "$REVIEWED_SHA"

ACTUAL_SHA="$("$GIT_BIN" -C "$REPO_DIR" rev-parse HEAD)"
if [[ "$ACTUAL_SHA" != "$REVIEWED_SHA" ]]; then
  echo "checked out $ACTUAL_SHA, expected $REVIEWED_SHA; refusing deployment" >&2
  exit 65
fi

"$CP_BIN" -a "$REPO_DIR/." "$APP/"

"$PYTHON_BIN" -m venv "$APP/.venv"
"$PIP_BIN" install --quiet --requirement "$APP/requirements.lock"

if [[ ! -f "$APP/.env" ]]; then
  "$CP_BIN" "$APP/.env.example" "$APP/.env"
  echo "!! Fill in $APP/.env before starting. Keys are NOT in the repo."
fi
"$CHOWN_BIN" -R glassbox:glassbox "$APP"
"$CHMOD_BIN" 600 "$APP/.env"

"$CP_BIN" "$APP/deploy/glassbox.service" /etc/systemd/system/
"$SYSTEMCTL_BIN" daemon-reload

echo
echo "Provisioned reviewed commit $REVIEWED_SHA. Before starting:"
echo "  1. edit $APP/.env"
echo "  2. sudo -u glassbox $APP/.venv/bin/python $APP/main.py --dry-run"
echo "  3. systemctl enable --now glassbox"
