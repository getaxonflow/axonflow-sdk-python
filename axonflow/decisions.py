"""Decision explainability types and helpers.

Implements ADR-043 (Explainability Data Contract). The DecisionExplanation
shape is frozen; additive-only changes are allowed; renames/removals require
a major version bump.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ExplainPolicy(BaseModel):
    """A policy reference inside a decision explanation."""

    policy_id: str
    policy_name: Optional[str] = None
    action: Optional[str] = None
    risk_level: Optional[str] = None  # low | medium | high | critical
    allow_override: bool = False
    policy_description: Optional[str] = None


class ExplainRule(BaseModel):
    """Rule-level detail inside a decision explanation."""

    policy_id: str
    rule_id: Optional[str] = None
    rule_text: Optional[str] = None
    matched_on: Optional[str] = None


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
    policy_matches: List[ExplainPolicy] = Field(default_factory=list)
    matched_rules: Optional[List[ExplainRule]] = None
    decision: str  # allow | deny | require_approval
    reason: str = ""
    risk_level: Optional[str] = None
    override_available: bool = False
    override_existing_id: Optional[str] = None
    historical_hit_count_session: int = 0
    policy_source_link: Optional[str] = None
    tool_signature: Optional[str] = None
