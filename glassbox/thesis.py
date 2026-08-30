"""Bounded AI selection and read-only daily review.

The model cannot create a trade. Deterministic code supplies fully priced
option candidates and the model may return one existing candidate ID or
abstain. A valid ID retrieves the exact original object, which must still pass
through the deterministic risk kernel and executor.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import config as C
from . import env
from .candidates import (
    CandidateDataInvalid,
    build_candidate_manifest,
    build_selection_receipt,
)
from .macro import CALENDAR, MEASUREMENT_ET
from .schema import TradePlan

log = logging.getLogger("glassbox.thesis")

SYSTEM = """You are a bounded selector for an options trading agent.

Deterministic code has already selected the contracts, quantities, sides,
limit prices, maximum loss, exits, and evidence. You may choose exactly one
candidate_id from the supplied list or abstain with null. You must not invent,
alter, or return any trade field. Prefer abstention when the evidence is not
compelling. Return only the requested JSON object."""


class Selection(BaseModel):
    """The model's entire authority surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str | None
    rationale: str = Field(min_length=1, max_length=400)


class ThesisLayer:
    def __init__(self, model: str = "claude-opus-5", timeout: int = C.LLM_TIMEOUT_SECONDS):
        self.model = model
        self.timeout = timeout
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            key = env.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            # Explicit timeout. A hung call would otherwise stall the tick loop.
            self._client = anthropic.Anthropic(api_key=key, timeout=self.timeout)
        return self._client

    # -- bounded candidate selection -----------------------------------------

    def select(self, candidates: list[TradePlan], state, journal=None) -> TradePlan | None:
        """Return an exact supplied option candidate, or safely abstain.

        This method never constructs a ``TradePlan`` from model output and
        never raises on model or validation failures.
        """
        option_candidates = [c for c in candidates if c.instrument == "option"]
        if not option_candidates:
            self._record(
                journal, "CANDIDATE_ABSTAINED", {"reason": "no option candidates", "offered": 0}
            )
            return None

        by_id = {candidate.plan_id: candidate for candidate in option_candidates}
        try:
            manifest = build_candidate_manifest(option_candidates)
        except CandidateDataInvalid as exc:
            self._record(
                journal,
                "CANDIDATE_ABSTAINED",
                {
                    "reason": f"candidate_manifest_invalid:{exc}",
                    "offered": len(option_candidates),
                },
            )
            return None

        ordered_candidates = [by_id[candidate_id] for candidate_id in manifest.candidate_ids]
        context = self._selection_context(
            ordered_candidates,
            state,
            candidate_manifest_hash=manifest.manifest_hash,
        )
        self._record(
            journal,
            "CANDIDATE_SET_BUILT",
            {
                "candidate_manifest_hash": manifest.manifest_hash,
                "candidate_ids": manifest.candidate_ids,
                "candidate_hashes": tuple(entry.content_hash for entry in manifest.candidates),
            },
        )

        try:
            raw = self._ask_selection(context)
            receipt = build_selection_receipt(
                prompt=SYSTEM,
                model=self.model,
                manifest=manifest,
                selector_input=context,
                selector_output=raw,
            )
            selection = Selection.model_validate(raw)
        except ValidationError as exc:
            log.warning("invalid candidate selection: %s", exc)
            self._record(
                journal,
                "CANDIDATE_SELECTION_INVALID",
                {
                    "error": "selection_schema_invalid",
                    "offered": len(option_candidates),
                    "selector_receipt": receipt.model_dump(mode="json"),
                },
            )
            return None
        except Exception as exc:
            log.warning("candidate selection unavailable: %s", exc)
            receipt = build_selection_receipt(
                prompt=SYSTEM,
                model=self.model,
                manifest=manifest,
                selector_input=context,
                selector_output={"outcome": "unavailable", "error_type": type(exc).__name__},
            )
            self._record(
                journal,
                "CANDIDATE_SELECTION_UNAVAILABLE",
                {
                    "error_type": type(exc).__name__,
                    "impact": "abstained",
                    "selector_receipt": receipt.model_dump(mode="json"),
                },
            )
            return None

        if selection.candidate_id is None:
            self._record(
                journal,
                "CANDIDATE_ABSTAINED",
                {
                    "reason": selection.rationale,
                    "offered": len(option_candidates),
                    "selector_receipt": receipt.model_dump(mode="json"),
                },
            )
            return None

        selected = by_id.get(selection.candidate_id)
        if selected is None:
            self._record(
                journal,
                "CANDIDATE_SELECTION_INVALID",
                {
                    "error": "unknown or non-option candidate ID",
                    "candidate_id": selection.candidate_id,
                    "offered_ids": sorted(by_id),
                    "selector_receipt": receipt.model_dump(mode="json"),
                },
            )
            return None

        self._record(
            journal,
            "CANDIDATE_SELECTED",
            {
                "candidate_id": selected.plan_id,
                "rationale": selection.rationale,
                "offered": len(option_candidates),
                "selector_receipt": receipt.model_dump(mode="json"),
            },
        )
        return selected

    @staticmethod
    def _record(journal, event: str, payload: dict) -> None:
        if journal is not None:
            journal.append("thesis.llm", event, payload)

    def _selection_context(
        self,
        candidates: list[TradePlan],
        state,
        *,
        candidate_manifest_hash: str,
    ) -> dict:
        upcoming = [
            {"event": e.name, "when_et": e.when.isoformat(), "tier": e.tier, "source": e.source}
            for e in CALENDAR
            if e.when > state.now_et
        ]
        return {
            "now_et": state.now_et.isoformat(),
            "measurement_et": MEASUREMENT_ET.isoformat(),
            "equity": str(state.equity),
            "cash": str(state.cash),
            "convex_premium_outstanding": str(state.convex_premium_outstanding),
            "convex_budget_remaining": str(
                C.CONVEX_TOTAL_PREMIUM_CAP - state.convex_premium_outstanding
            ),
            "macro_calendar": upcoming,
            "candidate_manifest_hash": candidate_manifest_hash,
            "candidates": [self._candidate_summary(candidate) for candidate in candidates],
        }

    @staticmethod
    def _candidate_summary(candidate: TradePlan) -> dict:
        return {
            "candidate_id": candidate.plan_id,
            "underlying": candidate.symbol,
            "legs": [
                {
                    "symbol": leg.symbol,
                    "side": leg.side,
                    "qty": leg.qty,
                    "limit_price": str(leg.limit_price),
                }
                for leg in candidate.option_legs
            ],
            "notional_usd": str(candidate.notional_usd),
            "max_loss_usd": str(candidate.max_loss_usd),
            "time_exit": (
                candidate.time_exit.isoformat() if candidate.time_exit is not None else None
            ),
            "thesis": candidate.thesis,
            "evidence": candidate.evidence,
        }

    def _ask_selection(self, payload: dict) -> dict:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            temperature=0.2,
            system=SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": "Choose one of these immutable, pre-priced option "
                    "candidates or abstain.\n\n"
                    f"{json.dumps(payload, indent=2)}\n\n"
                    'Return exactly {"candidate_id": "<existing id>", '
                    '"rationale": "<brief reason>"} or use null for '
                    "candidate_id to abstain. Return no other fields.",
                }
            ],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return _extract_json_object(text)

    # -- daily review ----------------------------------------------------------

    def daily_review(self, state, journal) -> str:
        """A plain-English summary of the day, written into the journal.

        Read-only: this cannot produce an order under any circumstance.
        """
        recent = list(journal.read())[-60:]
        events = [{"ts": e["ts"], "actor": e["actor"], "event": e["event"]} for e in recent]
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=700,
            temperature=0.3,
            system="You write a short, factual end-of-day note for a trading "
            "agent's audit log. No speculation, no advice, no predictions. "
            "Describe what the system did and why, in plain English.",
            messages=[
                {
                    "role": "user",
                    "content": f"Equity: {state.equity}. Positions: "
                    f"{[p.symbol for p in state.positions]}.\n"
                    f"Recent journal events:\n{json.dumps(events, indent=1)}\n\n"
                    "Write 4-6 sentences summarising the session.",
                }
            ],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def _extract_json_object(text: str) -> dict:
    """Tolerant of fenced output, intolerant of anything else."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        parsed = json.loads(t[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}
