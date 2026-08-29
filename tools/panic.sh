#!/usr/bin/env bash
# Flatten everything, immediately, using the Alpaca CLI.
#
# The manual kill switch. It does not need the agent running, healthy, or even
# installed -- which is exactly when you will want it.
#
#   ./tools/panic.sh            show what would happen, change nothing
#   ./tools/panic.sh --force    cancel all orders, close all positions, latch
#
# Command syntax verified against github.com/alpacahq/cli.
set -euo pipefail

if ! command -v alpaca &>/dev/null; then
  cat >&2 <<'MSG'
alpaca CLI not found. Install one of:
  brew install alpacahq/tap/cli
  go install github.com/alpacahq/cli/cmd/alpaca@latest
MSG
  exit 1
fi

# The CLI reads ALPACA_API_KEY / ALPACA_SECRET_KEY from the environment and
# trades LIVE if ALPACA_LIVE_TRADE=true. Refuse to run anywhere near that.
if [[ "${ALPACA_LIVE_TRADE:-false}" == "true" ]]; then
  echo "REFUSING: ALPACA_LIVE_TRADE=true. This script is for the paper account." >&2
  exit 2
fi

if [[ -f .env ]]; then
  set -a; . ./.env; set +a
fi

echo "=== account ==="
alpaca account get --json

echo
echo "=== open positions ==="
alpaca position list --json

echo
echo "=== open orders ==="
alpaca order list --status open --json

if [[ "${1:-}" != "--force" ]]; then
  cat <<'MSG'

Dry run. Nothing was changed.
Re-run with --force to cancel all orders and close all positions.
MSG
  exit 0
fi

echo
echo "!! CANCELLING ALL ORDERS"
alpaca order cancel-all || echo "   (cancel-all returned non-zero; continuing)"

echo "!! CLOSING ALL POSITIONS"
alpaca position close-all || echo "   (close-all returned non-zero; continuing)"

# Latch the software kill switch too. Without this a running agent simply
# re-enters the positions you just closed on its next tick.
mkdir -p state
cat > state/kill_switch.json <<JSON
{"tripped": true, "reason": "manual panic script", "at": "$(date -Iseconds)"}
JSON

echo
echo "Done. Software kill switch latched -- a human must re-arm it."
echo "Verify:"
echo "  alpaca position list --json"
