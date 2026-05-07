"""Decision explainability types and helpers.

Implements ADR-043 (Explainability Data Contract). The DecisionExplanation
shape is frozen; additive-only changes are allowed; renames/removals require
a major version bump.

list_decisions companion (Session γ / #1982) reuses the same conventions:
DecisionSummary is the slim 5-field row; ListDecisionsOptions carries the
5 optional filters.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ExplainPolicy(BaseModel):
    """A policy reference inside a decision explanation."""

    policy_id: str
    policy_name: str | None = None
    action: str | None = None
    risk_level: str | None = None  # low | medium | high | critical
    allow_override: bool = False
    policy_description: str | None = None


class ExplainRule(BaseModel):
    """Rule-level detail inside a decision explanation."""

    policy_id: str
    rule_id: str | None = None
    rule_text: str | None = None
    matched_on: str | None = None


class DecisionExplanation(BaseModel):
    """Canonical payload returned by ``client.explain_decision``.

    Shape frozen per ADR-043. Fields:

    * ``decision_id`` — the global decision identifier.
    * ``timestamp`` — when the decision was made.
    * ``policy_matches`` — every policy that contributed to the decision,
      with risk level and overridability.
    * ``matched_rules`` — rule-level detail (optional, populated when the
      upstream engine supports it).
    * ``decision`` — ``allow`` | ``deny`` | ``require_approval``.
    * ``reason`` — human-readable reason string.
    * ``risk_level`` — aggregate risk label for the decision.
    * ``override_available`` — True iff at least one non-critical policy
      with ``allow_override=True`` matched.
    * ``override_existing_id`` — populated when an active override already
      exists for this caller and policy scope.
    * ``historical_hit_count_session`` — how many times the caller has hit
      the same rule in the rolling 24-hour session window.
    * ``policy_source_link`` — URL to the policy definition (optional).
    * ``tool_signature`` — the tool signature the decision was scoped to,
      if any.
    """

    decision_id: str
    timestamp: datetime
    policy_matches: list[ExplainPolicy] = Field(default_factory=list)
    matched_rules: list[ExplainRule] | None = None
    decision: str  # allow | deny | require_approval
    reason: str = ""
    risk_level: str | None = None
    override_available: bool = False
    override_existing_id: str | None = None
    historical_hit_count_session: int = 0
    policy_source_link: str | None = None
    tool_signature: str | None = None


class DecisionSummary(BaseModel):
    """Slim 5-field row returned by ``client.list_decisions``.

    ``policy_id`` and ``tool_signature`` are optional because dynamic-only
    blocks + pre-α1 audit rows may not populate them. Additive new fields
    are non-breaking per ADR-043 §"Versioning"; arbitrary unknown fields
    on the wire are accepted via ``extra='ignore'``.

    Cross-SDK parity:

      Go:     axonflow-sdk-go/decisions.go (DecisionSummary)
      TS:     axonflow-sdk-typescript/src/types/decisions.ts (DecisionSummary)
      Java:   .../sdk/types/DecisionSummary.java
      Rust:   axonflow-sdk-rust/src/types/decisions.rs (DecisionSummary)
    """

    model_config = ConfigDict(extra="ignore")

    decision_id: str
    timestamp: datetime
    decision: str  # allow | deny | require_approval
    policy_id: str | None = None
    tool_signature: str | None = None


class ListDecisionsOptions(BaseModel):
    """Optional filters for ``client.list_decisions``.

    Every field is optional; ``None`` values are omitted from the URL so
    the platform applies its tier-default page. ``decision`` must be one
    of ``"allow"``, ``"deny"``, or ``"require_approval"`` when set.
    ``limit`` is server-capped per tier; over-cap requests yield a 429
    with the V1 upgrade envelope (surfaced as
    :class:`axonflow.exceptions.RateLimitError`).
    """

    since: datetime | None = None
    decision: str | None = None
    policy_id: str | None = None
    tool_signature: str | None = None
    limit: int | None = None
