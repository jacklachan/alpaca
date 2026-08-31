"""Rebuild the agent's recorded decisions from the journal alone.

"Deterministic" is the load-bearing word in this project's argument, and until
now it was an adjective. This module makes it a command: take the candidate set
the agent said it offered, recompute the content address from the parts it
recorded, and check the answer against the hash it published at the time.

Two properties fall out, and both are things a sceptical reader would otherwise
have to take on faith:

  * **The manifest hash was not asserted, it was derived.** If the recorded
    parts do not hash to the recorded result, either the journal was edited or
    the hashing changed. Both are worth knowing and neither is visible from
    reading the code.
  * **The AI chose from that set.** A selection naming an id outside the
    replayed manifest means the model produced something the deterministic
    layer never offered, which is the one failure this whole design exists to
    make impossible.

Replay is pure and read-only: it reconstructs from recorded values and never
contacts a venue, so it can be run by anyone holding only the journal file.
A hash it cannot rebuild is reported as unverifiable rather than as a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .candidates import CANDIDATE_SCHEMA_VERSION, CandidateManifestEntry, canonical_hash


@dataclass(frozen=True)
class ManifestReplay:
    """One recorded candidate set, rebuilt from its recorded parts."""

    recorded_hash: str
    rebuilt_hash: str
    candidate_ids: tuple[str, ...]
    entries: int
    verified: bool
    reason: str = ""

    @property
    def matches(self) -> bool:
        return self.verified and self.recorded_hash == self.rebuilt_hash


def rebuild_manifest_hash(
    entries: Iterable[Mapping[str, Any]],
    *,
    schema_version: str = CANDIDATE_SCHEMA_VERSION,
) -> str:
    """Recompute a candidate-set content address from its recorded parts.

    Mirrors build_candidate_manifest exactly, including the canonical ordering
    by candidate id -- the ordering is what makes the address independent of
    the sequence the strategies happened to produce candidates in.
    """
    ordered = tuple(
        sorted(
            (
                CandidateManifestEntry(
                    candidate_id=str(e["candidate_id"]),
                    content_hash=str(e["content_hash"]),
                )
                for e in entries
            ),
            key=lambda entry: entry.candidate_id,
        )
    )
    return canonical_hash({"schema_version": schema_version, "candidates": ordered})


def replay_record(payload: Mapping[str, Any]) -> ManifestReplay:
    """Rebuild one CANDIDATE_SET_BUILT payload."""
    recorded = str(payload.get("manifest_hash") or "")
    entries = list(payload.get("manifest_entries") or [])
    ids = tuple(str(i) for i in (payload.get("candidate_ids") or []))

    if payload.get("manifest_unavailable"):
        return ManifestReplay(
            recorded, "", ids, 0, False, str(payload["manifest_unavailable"])[:200]
        )
    if not recorded:
        return ManifestReplay(recorded, "", ids, 0, False, "no manifest hash was recorded")
    if not entries:
        return ManifestReplay(
            recorded,
            "",
            ids,
            0,
            False,
            "manifest hash recorded without the parts it was built from",
        )

    try:
        rebuilt = rebuild_manifest_hash(
            entries,
            schema_version=str(payload.get("manifest_schema_version", CANDIDATE_SCHEMA_VERSION)),
        )
    except Exception as exc:
        return ManifestReplay(recorded, "", ids, len(entries), False, f"unreplayable: {exc}")

    return ManifestReplay(
        recorded_hash=recorded,
        rebuilt_hash=rebuilt,
        candidate_ids=ids,
        entries=len(entries),
        verified=True,
        reason="" if rebuilt == recorded else "rebuilt hash does not match the recorded hash",
    )


@dataclass
class ReplayReport:
    """Every candidate set in a journal, rebuilt."""

    replays: list[ManifestReplay] = field(default_factory=list)
    selections: list[str] = field(default_factory=list)
    unoffered_selections: list[str] = field(default_factory=list)

    @property
    def verified(self) -> int:
        return sum(1 for r in self.replays if r.matches)

    @property
    def mismatched(self) -> list[ManifestReplay]:
        return [r for r in self.replays if r.verified and not r.matches]

    @property
    def unverifiable(self) -> list[ManifestReplay]:
        return [r for r in self.replays if not r.verified]

    @property
    def ok(self) -> bool:
        """A mismatch or an unoffered selection is a contradiction.

        An unverifiable record is not: the agent may have journalled a set it
        could not address, and that is already recorded as its own reason.
        """
        return not self.mismatched and not self.unoffered_selections

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "sets_replayed": len(self.replays),
            "sets_verified": self.verified,
            "sets_mismatched": len(self.mismatched),
            "sets_unverifiable": len(self.unverifiable),
            "selections": len(self.selections),
            "unoffered_selections": list(self.unoffered_selections),
        }


def replay_journal(records: Iterable[Mapping[str, Any]]) -> ReplayReport:
    """Rebuild every recorded candidate set and check every selection."""
    report = ReplayReport()
    offered: set[str] = set()

    for record in records:
        event = record.get("event")
        payload = record.get("payload") or {}
        if event == "CANDIDATE_SET_BUILT":
            replay = replay_record(payload)
            report.replays.append(replay)
            offered.update(replay.candidate_ids)
        elif event == "CANDIDATE_SELECTED":
            chosen = str(payload.get("candidate_id") or payload.get("plan_id") or "")
            if not chosen:
                continue
            report.selections.append(chosen)
            # Only meaningful once a set has been seen; before that there is
            # nothing to have chosen from and absence proves nothing.
            if offered and chosen not in offered:
                report.unoffered_selections.append(chosen)

    return report
