#!/usr/bin/env bash
# Daily reconciliation report, via the Alpaca CLI.
#
# Deliberately independent of the agent's own code path. If broker.reconcile()
# has a bug, a report built from the same code would repeat it -- this one
# reads the broker directly through a different tool, so the two can disagree
# and the disagreement is visible.
#
#   ./tools/reconcile_report.sh
#   ./tools/reconcile_report.sh > reports/$(date +%F).txt
set -euo pipefail

command -v alpaca >/dev/null || { echo "alpaca CLI not found" >&2; exit 1; }
[[ -f .env ]] && { set -a; . ./.env; set +a; }

echo "Glassbox reconciliation — $(date -Iseconds)"
echo "========================================================"

echo
echo "-- account --"
alpaca account get --jq '{account: .account_number, equity: .equity, cash: .cash, buying_power: .buying_power}'

echo
echo "-- positions --"
alpaca position list --jq '.[] | {symbol, qty, market_value, unrealized_pl}' || echo "none"

echo
echo "-- orders today --"
alpaca order list --jq '.[] | {symbol, side, qty, filled_qty, status, client_order_id}' || echo "none"

echo
echo "-- journal --"
if [[ -f state/journal.jsonl ]]; then
  python tools/verify_chain.py state/journal.jsonl
else
  echo "no journal yet"
fi

echo
echo "-- kill switch --"
if [[ -f state/kill_switch.json ]]; then cat state/kill_switch.json; else echo "armed, not tripped"; fi
