#!/usr/bin/env bash
# Provision a $5 VPS for Glassbox. Run as root on a fresh Debian/Ubuntu box.
set -euo pipefail

APP=/opt/glassbox

apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv git

id -u glassbox &>/dev/null || useradd --system --home "$APP" --shell /usr/sbin/nologin glassbox

mkdir -p "$APP/state"
git clone https://github.com/jacklachan/alpaca.git "$APP/repo" 2>/dev/null || \
  git -C "$APP/repo" pull
cp -r "$APP/repo/." "$APP/"

python3.12 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --quiet --upgrade pip
"$APP/.venv/bin/pip" install --quiet -r "$APP/requirements.txt"

if [ ! -f "$APP/.env" ]; then
  cp "$APP/.env.example" "$APP/.env"
  echo "!! Fill in $APP/.env before starting. Keys are NOT in the repo."
fi
chown -R glassbox:glassbox "$APP"
chmod 600 "$APP/.env"

cp "$APP/deploy/glassbox.service" /etc/systemd/system/
systemctl daemon-reload

echo
echo "Provisioned. Before starting:"
echo "  1. edit $APP/.env"
echo "  2. sudo -u glassbox $APP/.venv/bin/python $APP/main.py --dry-run"
echo "  3. systemctl enable --now glassbox"
