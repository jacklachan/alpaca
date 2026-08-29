#!/usr/bin/env bash
# Flatten everything, immediately, using the Alpaca CLI.
#
# This is the manual kill switch. It does not need the agent to be running,
# healthy, or even installed -- which is exactly when you will want it.
#
#   ./tools/panic.sh          show what would happen
#   ./tools/panic.sh --force  actually do it
set -euo pipefail

if ! command -v alpaca &>/dev/null; then
  echo "alpaca CLI not found. Install: brew install alpacahq/tap/cli" >&2
  exit 1
fi

echo "=== account ==="
alpaca account get --output json | head -30
echo
echo "=== open positions ==="
alpaca position list --output json
echo
echo "=== open orders ==="
alpaca order list --status open --output json

if [[ "${1:-}" != "--force" ]]; then
  echo
  echo "Dry run. Re-run with --force to cancel all orders and close all positions."
  exit 0
fi

echo
echo "!! CANCELLING ALL ORDERS"
alpaca order cancel --all || true
echo "!! CLOSING ALL POSITIONS"
alpaca position close --all || true

# Latch the software kill switch too, so a running agent does not simply
# re-enter the positions you just closed.
mkdir -p state
cat > state/kill_switch.json <<JSON
{"tripped": true, "reason": "manual panic script", "at": "$(date -Iseconds)"}
JSON
echo "Done. Software kill switch latched -- a human must re-arm it."
