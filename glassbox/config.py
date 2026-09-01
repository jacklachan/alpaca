"""Every threshold in one place.

Nothing in this file is derived at runtime. If a number matters, it lives here
so it can be read, tested, and cited in the journal without hunting.
"""

from __future__ import annotations

import os as _os
from decimal import Decimal
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# --- Universe -----------------------------------------------------------------
# Hard-coded. The kernel refuses anything outside these sets, which also means an
# LLM hallucinating a ticker cannot cost money.

EQUITY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "SPY",
        "QQQ",
        "IWM",
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "GOOGL",
        "META",
        "AVGO",
        "JPM",
        "XOM",
    }
)

CRYPTO_ALLOWLIST: frozenset[str] = frozenset({"BTC/USD", "ETH/USD"})

# Options: underlyings only. Contract symbols are validated by parsing out the
# underlying and checking membership here.
OPTION_UNDERLYING_ALLOWLIST: frozenset[str] = frozenset({"SPY", "QQQ"})

# The scored account registers only these deterministic option candidate
# generators. Keep this ordered so the model sees candidates consistently.
SCORED_OPTION_UNDERLYINGS: tuple[str, ...] = ("SPY", "QQQ")

# --- Account ------------------------------------------------------------------

STARTING_EQUITY = Decimal("100000")

# --- Sleeve budgets -----------------------------------------------------------
# AUDIT NOTE: the original plan ran 80/15/5. "Options Alpha Agents" is the
# event's TRACK name (lablab lists it as the main track, open to all
# participants), and the event page states that all strategies must incorporate
# options trading. Under the original split, 85% of capital sat in instruments
# that score on none of the five published criteria.
#
# 65/25/10 preserves the barbell shape -- bounded left tail, open right tail --
# while putting the track's mandated instrument at the centre.
#
# HISTORY, so nobody re-litigates this a third time: a 29 Aug review briefly
# moved this to 70/20/10 on the belief that "Options Alpha Agents" was
# fabricated, because the phrase does not appear on the public event page. It
# does appear on the /live page as the track name, and the deadline printed
# there (4 Sep 15:00 UTC) matches the rest of our notes exactly -- the public
# page simply renders less than the enrolled view. The reviewer's check was
# weaker than the original author's. Reverted. If you are about to change these
# numbers, read DECISIONS.md section 2 first, and confirm against the enrolled
# view rather than the public page.

CORE_SLEEVE_USD = Decimal("65000")
CONVEX_SLEEVE_USD = Decimal("25000")
CRYPTO_SLEEVE_USD = Decimal("10000")

# --- Convex sleeve rules ------------------------------------------------------

# Total option premium outstanding, portfolio-level, checked before every order.
CONVEX_TOTAL_PREMIUM_CAP = CONVEX_SLEEVE_USD

# New premium committed per calendar day, so a bad Monday cannot spend the week.
CONVEX_DAILY_BURN_CAP = Decimal("8000")

# AUDIT NOTE: invariant #04 as originally specified ($5k/day) made the flagship
# Thursday event trade ("sized to whatever remains of the budget") impossible --
# the kernel would have refused the trade the whole plan was built around.
# Resolved with an explicit, separately budgeted exemption rather than by
# weakening the daily cap.
EVENT_TRADE_DAILY_CAP = Decimal("18000")

OPTION_STRIKE_BAND_PCT = Decimal("0.02")  # within ~2% of spot

# AUDIT NOTE: this was 3, calibrated when we believed the measurement was Fri
# 4 Sep 09:30. Alpaca's guidelines put it at EOD Thu 3 Sep, which pulls every
# sensible expiry a session closer and made a 3-day entry minimum refuse the
# strategy's own best candidate (the 4 Sep expiry, two sessions alive at
# measurement, 3x the convexity per dollar of the 11 Sep contract).
#
# Lowered to 2 deliberately, not loosened carelessly. The safety property we
# actually care about is "never hold a contract that marks as a stub when the
# account is valued, and never depend on expiry-day settlement". That property
# is enforced by OPTION_MIN_DTE_AT_MEASUREMENT (sessions remaining AT the
# snapshot) and by the 14:30 ET force-close -- not by days-since-entry, which
# is only a proxy for it. With the measurement moved, the proxy and the real
# constraint disagreed, so we kept the real one.
OPTION_MIN_DTE = 2  # trading days at ENTRY
OPTION_MAX_DTE = 10  # trading days at ENTRY

# AUDIT NOTE: Alpaca stops accepting options orders at 15:30 ET on expiration
# day. The original plan force-closed at 15:45 ET -- after the cutoff -- which
# would have handed the position to auto-exercise/liquidation, and non-trade
# activity syncs the *following* day, i.e. after the snapshot.
OPTION_FORCE_CLOSE_ET = (14, 30)

# Bank a convexity trade that has already worked.
#
# A long strangle is a bet that a move happens. When one does, the position can
# be up large and then give the whole gain back -- long gamma round-trips, that
# is its nature. Until now nothing took profit on an option at all: stop and
# target were evaluated only for equity and crypto, so an option's only exits
# were the measurement deadline and the expiry-day force close. A position that
# doubled on Tuesday was still held into Thursday, decaying, with the gain
# unrealised.
#
# Theta is certain and the continuation of a move is not, so the account is
# measured on what was banked rather than on what it briefly showed. This is
# deliberately not paired with a stop: cutting a long-premium position because
# it is down forfeits exactly the optionality the premium bought, and max loss
# is already bounded by the premium itself.
# Set to 30%, not 50%, because the losing branch dominates. If no catalyst
# produces a move the position decays ~30% into measurement, so what matters
# is that the winning branch HAPPENS rather than that it is large: a target
# that fires converts a touch into a realised gain, and "touches a level at
# some point" is strictly more probable than "is above it at one instant".
# +30% on premium is roughly a 1.3% move in the underlying and lands the
# account near +5%, which wins a four-day P&L field that mostly clusters flat.
#
# Taking the whole position rather than scaling out is deliberate. These are
# economic prints, and implied volatility collapses once one resolves -- the
# gain on a long strangle is largest at the print and decays after it, so a
# quick full exit is the right shape for this structure.
OPTION_PROFIT_TARGET_PCT = Decimal("0.30")

# --- Risk limits --------------------------------------------------------------

# AUDIT NOTE: the original invariant said "no single underlying > 25% of equity,
# counting option delta exposure" without saying gross or net, and applied one
# number to both instruments. Both readings fail:
#
#   Gross  -- a single ATM SPY call is ~160% of a $100k account in notional
#             delta, so every option trade is refused.
#   Net at 25% -- still refuses any normal convex position, because long
#             options deliver large notional delta for small premium. That IS
#             the leverage; it is the reason we buy them.
#
# Split into two limits with different jobs:
#
#   CAPITAL concentration (equity/crypto): how much cash is exposed to one
#   name. This is the real risk control for the core sleeve.
#
#   DELTA sanity (options): a wide bound that catches "the model asked for 100
#   contracts" without refusing a legitimate 5-lot. It is deliberately loose,
#   because for a long-premium-only book the binding constraint is the premium
#   cap (invariants 03/04) -- loss is bounded by premium regardless of delta.
#   Do not describe this one as the risk control; the premium caps are.
CONCENTRATION_CAPITAL_PCT = Decimal("0.25")  # equity/crypto, notional
OPTION_NET_DELTA_MAX_PCT = Decimal("2.0")  # options, net delta notional
CONCENTRATION_BASIS = "net"

MAX_CORE_POSITIONS = 6
MAX_CRYPTO_POSITIONS = 2
MAX_OPTION_LEGS = 6

CORE_GROSS_EXPOSURE_MAX = Decimal("1.0")  # no equity margin leverage anywhere

# --- Kill switch --------------------------------------------------------------
# AUDIT NOTE: a single portfolio switch at 88% of starting equity fires in the
# plan's own modal scenario -- the convex sleeve expiring worthless is a ~50%
# outcome by design and would trip it. A switch that fires when a sleeve does
# exactly what it was designed to do is mis-specified. Split per sleeve:
# the convex sleeve is *permitted* to go to zero; the core sleeve is not.

CORE_DRAWDOWN_KILL_PCT = Decimal("0.06")  # core sleeve down 6% -> halt

# AUDIT NOTE: this was 0.15, which reintroduced the exact bug the per-sleeve
# split above exists to prevent. The convex sleeve is 25% of equity and is
# explicitly *permitted* to go to zero -- that is a designed ~50% outcome. A
# 15% portfolio backstop therefore trips while the convex sleeve is doing
# nothing but what it was built to do: measured tripping with the core sleeve
# exactly flat and the convex sleeve down only 60% of its premium. It then
# latches, force-closes the convex sleeve at the bottom, and refuses all
# further trading for the rest of the week, with no human present to re-arm.
#
# The backstop must sit BELOW the designed floor, not inside it. Designed
# worst case is the convex sleeve fully spent (-25%) plus the core sleeve at
# its own -6% stop on 65% of capital (-3.9%), i.e. equity near $71,100.
# 30% leaves the strategy room to be wrong in the way it is allowed to be
# wrong, while still catching something genuinely pathological.
PORTFOLIO_DRAWDOWN_KILL_PCT = Decimal("0.30")

# Latching: once tripped, only a human re-arms. See README for the re-arm rule.
KILL_SWITCH_STATE_FILE = "state/kill_switch.json"

# Which catalysts we have already positioned for today. Persisted so a restart
# does not re-buy the same event.
POSITIONED_STATE_FILE = _os.getenv("GLASSBOX_POSITIONED_FILE", "state/positioned_for.json")

# Stops, targets and time exits. Persisted so a restart does not leave open
# positions with no exit logic attached to them.
TARGETS_STATE_FILE = _os.getenv("GLASSBOX_TARGETS_FILE", "state/targets.json")

# Per-contract strategy ownership. Only the scored account keeps one: the
# development sleeves are equity/crypto and do not own option contracts.
LEDGER_STATE_FILE = _os.getenv("GLASSBOX_LEDGER_FILE", "state/position_ledger.json")

# Exit attempt counts and the uncertainty latch. Durable because a restart
# is exactly when a lost latch would resume a rejected submit loop.
EXIT_STATE_FILE = _os.getenv("GLASSBOX_EXIT_STATE_FILE", "state/exit_state.json")

# --- Circuit breakers ---------------------------------------------------------
# AUDIT NOTE: invariant #11 (idempotency) stops the *same* plan being sent twice.
# Nothing in the original twelve stopped the agent generating 500 *distinct*
# plans. Runaway loops are the classic unattended-overnight failure.

MAX_ORDERS_PER_DAY = 40
MAX_ORDERS_PER_SYMBOL_PER_DAY = 8

# --- Sanity band --------------------------------------------------------------

LIMIT_PRICE_BAND_PCT = Decimal("0.05")  # limit within 5% of snapshot
NOTIONAL_SANITY_MULTIPLE = Decimal("10")  # vs rolling median order size

# --- Max-loss estimation ------------------------------------------------------
# AUDIT NOTE: "every position had a computable maximum loss before it was opened"
# is exactly true for long options (premium paid) and false for equities and
# crypto, where price gaps straight through a stop. Stated honestly per
# instrument so the claim survives cross-examination.

GAP_MULTIPLIER = Decimal("1.5")  # applied to stop distance for equity/crypto

# --- Operations ---------------------------------------------------------------

RATE_LIMIT_PER_MIN = 150  # against Alpaca's 200 ceiling
HEARTBEAT_INTERVAL_MIN = 15
LLM_TIMEOUT_SECONDS = 45  # a *hung* thesis call stalls the tick loop

# Overridable so drills and tests can point at a scratch journal without
# touching the real one. Production leaves these unset.
JOURNAL_PATH = _os.getenv("GLASSBOX_JOURNAL_PATH", "state/journal.jsonl")
DB_PATH = _os.getenv("GLASSBOX_DB_PATH", "state/glassbox.db")

SLEEVES = ("core", "crypto", "convex")


# --- Event-driven volatility strategy -----------------------------------------

EVENT_MIN_TIER = 2  # 1 = payrolls only; 2 = every scheduled print
EVENT_LOOKAHEAD_HOURS = 30  # how far ahead a catalyst counts as actionable

# Buy convexity only when it is cheap. Above this, the underlying is already
# realising more than the options imply and we are not paid to own gamma.
MAX_IV_TO_RV_RATIO = Decimal("1.35")

# Strangle rather than straddle: ~45% of the cost for ~2.2x the contracts.
# Correct shape when the payoff is a step function rather than linear.
STRANGLE_OTM_PCT = Decimal("0.012")

# The option must still have this many trading days left AT MEASUREMENT, so it
# marks off a two-sided quote instead of a 0DTE stub in the widest window of
# the week.
OPTION_MIN_DTE_AT_MEASUREMENT = 2

# Upper bound on sessions remaining at measurement. Distinct from
# OPTION_MAX_DTE, which bounds days-to-expiry at ENTRY -- conflating the two
# let 18 Sep into the candidate set and made the convexity ratio meaningless
# by comparing against an expiry we would never trade. Six sessions keeps the
# comparison against the expiry a normal team would default to (11 Sep).
OPTION_MAX_SESSIONS_AT_MEASUREMENT = 6

# Marketable limit tolerance. Pricing off an indicative feed, so pay up slightly
# rather than sit unfilled -- but never chase beyond this.
LIMIT_TOLERANCE = Decimal("0.03")

# Option candidates use Alpaca's free indicative feed until the dedicated
# account proves a different entitlement. Quote age may not exceed the cache
# window; a slower mark is evidence to abstain, never to widen the limit.
OPTION_DATA_FEED = "indicative"
MAX_OPTION_QUOTE_AGE_SECONDS = Decimal("30")

# Max ATM relative bid/ask for an expiry to be tradeable. Measured 29 Aug:
# Fridays quote ~4.0-4.2%, the post-holiday Tuesday 5.3%, mid-week dailies
# 6.0%. 5.5% cleanly separates the liquid expiries from the thin ones.
MAX_ATM_SPREAD_PCT = Decimal("0.055")

# Incremental widening per reprice attempt, ADDED to LIMIT_TOLERANCE. Chasing
# stops when the cumulative bump would exceed LIMIT_PRICE_BAND_PCT, so the
# band is what actually bounds how far we chase.
#   attempt 1 -> 4%, attempt 2 -> 5%, attempt 3 -> refused
REPRICE_STEP_PCT = Decimal("0.01")


# --- Measurement approach ------------------------------------------------------
# The agent knows exactly when the account is valued. An option held into that
# moment is worth whatever the feed says at that instant, and this account
# prices options off the INDICATIVE feed -- a derived estimate, not OPRA.
#
# So the last decision of the week is not "is this position good" but "can this
# position be marked honestly". A wide or stale quote at the snapshot is a
# number nobody can defend, in either direction. Cash has no marking ambiguity
# at all, so when the mark cannot be trusted the certain number is worth more
# than the hopeful one.
#
# This only ever converts an untrustworthy mark into cash. It never opens risk,
# and it is deliberately narrow: inside the window, on options, only when the
# quote fails the same width test used to choose the contract in the first
# place.

#: How long before measurement the mark-quality check begins.
MEASUREMENT_FLATTEN_MINUTES = 45

#: A mark this wide at the snapshot is not evidence of anything. Deliberately
#: looser than MAX_ATM_SPREAD_PCT: refusing to *enter* on a 5.5% spread is
#: prudence, but flattening a working position needs the quote to be genuinely
#: unusable, not merely worse than we would have chosen.
MEASUREMENT_MAX_MARK_SPREAD_PCT = Decimal("0.12")
