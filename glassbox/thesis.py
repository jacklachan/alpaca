"""The thesis layer. The only place a model appears.

Scope is deliberately narrow. The model is handed a computed feature table,
the macro calendar, the current book and the remaining risk budget, and asked
to do the one thing it is genuinely good at: synthesise unstructured context
into a structured hypothesis. It is never asked to predict a price.

Three hard rules:

  1. Output is validated against the TradePlan schema. If it does not validate,
     it is discarded. No repair, no retry loop that could drift.
  2. Whatever it returns goes through the kernel like anything else. There is
     no path from here to the broker.
  3. It is optional. If the API is down, hung, or returns nonsense, the
     deterministic sleeves keep trading and the failure is journalled. Verify
     this by running with a deliberately invalid key.

Rule 3 is why the timeout matters. The original plan handled a *failed* call
but not a *hung* one, and a hung call blocks the tick loop.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal

from . import config as C
from .macro import CALENDAR, MEASUREMENT_ET
from .schema import TradePlan

log = logging.getLogger("glassbox.thesis")

SYSTEM = """You are the research analyst for an autonomous options trading agent.

You propose. You never execute. Every plan you emit is checked against a
deterministic risk kernel that will refuse anything unsafe, so propose what you
actually believe rather than what you think will pass.

Rules:
- Only these underlyings: SPY, QQQ for options; the equity and crypto
  allowlists otherwise. Anything else is refused.
- Long option premium only. Never propose selling an option.
- Every plan needs a bounded, stated maximum loss.
- Cite evidence ONLY from the feature table and calendar you are given. Do not
  invent a number. If you cannot ground a claim, do not make it.
- Returning an empty list is a good answer when nothing is compelling.

You are not asked to predict prices. You are asked to notice when the
information set and the option market disagree."""


class ThesisLayer:
    def __init__(self, model: str = "claude-opus-5",
                 timeout: int = C.LLM_TIMEOUT_SECONDS):
        self.model = model
        self.timeout = timeout
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            key = os.getenv("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            # Explicit timeout. A hung call would otherwise stall the tick loop.
            self._client = anthropic.Anthropic(api_key=key, timeout=self.timeout)
        return self._client

    # -- proposals -------------------------------------------------------------

    def propose(self, state, features: dict, journal=None) -> list[TradePlan]:
        """Returns validated plans, or an empty list. Never raises."""
        try:
            payload = self._context(state, features)
            raw = self._ask(payload)
        except Exception as exc:
            log.warning("thesis call failed: %s", exc)
            if journal:
                journal.append("thesis.llm", "THESIS_UNAVAILABLE", {
                    "error": str(exc),
                    "impact": "none: deterministic sleeves continue trading"})
            return []

        plans: list[TradePlan] = []
        for item in raw:
            try:
                plans.append(TradePlan(**item))
            except Exception as exc:
                # Discarded, not repaired. A repair loop is a drift loop.
                if journal:
                    journal.append("thesis.llm", "PLAN_DISCARDED_INVALID", {
                        "error": str(exc), "raw": item})
        if journal:
            journal.append("thesis.llm", "THESIS_COMPLETE", {
                "proposed": len(raw), "validated": len(plans)})
        return plans

    def _context(self, state, features: dict) -> dict:
        upcoming = [
            {"event": e.name, "when_et": e.when.isoformat(), "tier": e.tier,
             "source": e.source}
            for e in CALENDAR if e.when > state.now_et
        ]
        return {
            "now_et": state.now_et.isoformat(),
            "measurement_et": MEASUREMENT_ET.isoformat(),
            "equity": str(state.equity),
            "cash": str(state.cash),
            "positions": [
                {"symbol": p.symbol, "instrument": p.instrument,
                 "qty": str(p.qty), "market_value": str(p.market_value)}
                for p in state.positions],
            "convex_premium_outstanding": str(state.convex_premium_outstanding),
            "convex_budget_remaining": str(
                C.CONVEX_TOTAL_PREMIUM_CAP - state.convex_premium_outstanding),
            "features": features,
            "macro_calendar": upcoming,
            "allowlists": {
                "equity": sorted(C.EQUITY_ALLOWLIST),
                "crypto": sorted(C.CRYPTO_ALLOWLIST),
                "option_underlyings": sorted(C.OPTION_UNDERLYING_ALLOWLIST)},
        }

    def _ask(self, payload: dict) -> list[dict]:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=0.2,
            system=SYSTEM,
            messages=[{"role": "user", "content":
                       "Here is the current information set.\n\n"
                       f"{json.dumps(payload, indent=2)}\n\n"
                       "Return a JSON array of trade plans, or [] if nothing is "
                       "compelling. Each object must have: sleeve, action, "
                       "instrument, symbol, side, notional_usd, max_loss_usd, "
                       "thesis, evidence, confidence. Return only the JSON array."}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return _extract_json_array(text)

    # -- daily review ----------------------------------------------------------

    def daily_review(self, state, journal) -> str:
        """A plain-English summary of the day, written into the journal.

        Read-only: this cannot produce an order under any circumstance.
        """
        recent = list(journal.read())[-60:]
        events = [{"ts": e["ts"], "actor": e["actor"], "event": e["event"]}
                  for e in recent]
        msg = self.client.messages.create(
            model=self.model, max_tokens=700, temperature=0.3,
            system="You write a short, factual end-of-day note for a trading "
                   "agent's audit log. No speculation, no advice, no predictions. "
                   "Describe what the system did and why, in plain English.",
            messages=[{"role": "user", "content":
                       f"Equity: {state.equity}. Positions: "
                       f"{[p.symbol for p in state.positions]}.\n"
                       f"Recent journal events:\n{json.dumps(events, indent=1)}\n\n"
                       "Write 4-6 sentences summarising the session."}])
        return "".join(b.text for b in msg.content
                       if getattr(b, "type", "") == "text").strip()


def _extract_json_array(text: str) -> list[dict]:
    """Tolerant of fenced output, intolerant of anything else."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        parsed = json.loads(t[start:end + 1])
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []
