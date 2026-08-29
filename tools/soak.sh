#!/usr/bin/env bash
# Prove the agent survives on the host it will actually run on.
#
# tools/crash_drill.py proves the RECOVERY LOGIC is correct. It cannot prove
# the box stays up, that systemd restarts what it promised to restart, or that
# the unit file is even valid. This does, and it produces journal evidence you
# can point a judge at.
#
#   sudo bash tools/soak.sh            # 30 minutes, 3 kills
#   sudo bash tools/soak.sh 60 5       # 60 minutes, 5 kills
#
# Run this on the VPS, pointed at the DEV account, before Monday.
set -uo pipefail

MINUTES="${1:-30}"
KILLS="${2:-3}"
UNIT=glassbox
APP=/opt/glassbox
JOURNAL="$APP/state/journal.jsonl"
FAILED=0

say()  { printf '\n\033[33m== %s\033[0m\n' "$*"; }
pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }

say "0. Unit file validity"
if systemd-analyze verify "/etc/systemd/system/${UNIT}.service" 2>&1 | grep -qi 'unknown\|error'; then
  systemd-analyze verify "/etc/systemd/system/${UNIT}.service" 2>&1 | sed 's/^/     /'
  fail "systemd reports problems with the unit file"
else
  pass "unit file parses with no unknown keys"
fi

# The bug this catches: StartLimitIntervalSec in [Service] is silently ignored,
# and the default (5 starts / 10s, then give up forever) applies instead.
if systemctl show "$UNIT" -p StartLimitIntervalSec --value | grep -q '^0$'; then
  pass "restart rate limit disabled (will retry forever)"
else
  fail "StartLimitIntervalSec is $(systemctl show "$UNIT" -p StartLimitIntervalSec --value); \
a crash loop will stop restarting permanently"
fi

say "1. Preflight"
if sudo -u glassbox "$APP/.venv/bin/python" -m glassbox.preflight; then
  pass "environment parses identically under systemd and dotenv"
else
  fail "preflight refused; fix .env before soaking"
  exit 1
fi

say "2. Start and settle"
systemctl restart "$UNIT"
sleep 20
systemctl is-active --quiet "$UNIT" && pass "service active" || { fail "service not active"; exit 1; }

START_SEQ=$(wc -l < "$JOURNAL" 2>/dev/null || echo 0)
INTERVAL=$(( MINUTES * 60 / (KILLS + 1) ))

say "3. Kill/restart cycles: $KILLS kills over ${MINUTES}m"
for i in $(seq 1 "$KILLS"); do
  sleep "$INTERVAL"
  PID=$(systemctl show "$UNIT" -p MainPID --value)
  if [ -z "$PID" ] || [ "$PID" = "0" ]; then fail "no live PID before kill $i"; continue; fi

  echo "     kill -9 $PID  (cycle $i/$KILLS)"
  kill -9 "$PID"
  sleep 15

  if systemctl is-active --quiet "$UNIT"; then
    NEWPID=$(systemctl show "$UNIT" -p MainPID --value)
    pass "cycle $i: restarted automatically ($PID -> $NEWPID)"
  else
    fail "cycle $i: did NOT come back"
  fi

  if sudo -u glassbox "$APP/.venv/bin/python" "$APP/tools/verify_chain.py" >/dev/null 2>&1; then
    pass "cycle $i: journal chain still verifies"
  else
    fail "cycle $i: journal chain broken after restart"
  fi
done

say "4. Final state after ${MINUTES}m"
sleep "$INTERVAL"
END_SEQ=$(wc -l < "$JOURNAL" 2>/dev/null || echo 0)

systemctl is-active --quiet "$UNIT" && pass "still running" || fail "not running at end"
[ "$END_SEQ" -gt "$START_SEQ" ] \
  && pass "journal grew $START_SEQ -> $END_SEQ entries (agent did work, not just booted)" \
  || fail "journal did not grow; the agent is up but idle"

sudo -u glassbox "$APP/.venv/bin/python" "$APP/tools/verify_chain.py" \
  && pass "final chain verification" || fail "final chain verification"

RESTARTS=$(systemctl show "$UNIT" -p NRestarts --value)
echo "     systemd recorded $RESTARTS restart(s)"

# Evidence for the demo: the recovery markers are the interesting entries.
say "5. Recovery evidence in the journal"
grep -c TORN_ENTRY_DISCARDED "$JOURNAL" 2>/dev/null | while read -r n; do
  echo "     TORN_ENTRY_DISCARDED entries: $n"
done
grep -c '"event":"STARTUP"' "$JOURNAL" 2>/dev/null | while read -r n; do
  echo "     STARTUP entries (one per restart): $n"
done

echo
if [ "$FAILED" -eq 0 ]; then
  printf '\033[32mSOAK PASSED\033[0m  "runs unattended" is now evidence, not a claim.\n'
  echo 'Screenshot the STARTUP entries and NRestarts for the demo.'
else
  printf '\033[31mSOAK FAILED\033[0m  do not go live until this is green.\n'
fi
exit "$FAILED"
