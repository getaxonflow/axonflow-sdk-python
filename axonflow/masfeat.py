"""MAS FEAT Compliance Module for AxonFlow SDK.

This module provides types and data classes for the MAS FEAT (Monetary Authority
of Singapore - Fairness, Ethics, Accountability, Transparency) compliance framework.

Enterprise Feature: Full MAS FEAT compliance requires AxonFlow Enterprise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MaterialityClassification(str, Enum):
    """Materiality classification based on 3D risk rating."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SystemStatus(str, Enum):
    """AI system lifecycle status."""

    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class FEATAssessmentStatus(str, Enum):
    """FEAT assessment lifecycle status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    APPROVED = "approved"
    REJECTED = "rejected"


class KillSwitchStatus(str, Enum):
    """Kill switch status."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    TRIGGERED = "triggered"


class AISystemUseCase(str, Enum):
    """AI system use case categories."""

    CREDIT_SCORING = "credit_scoring"
    ROBO_ADVISORY = "robo_advisory"
    INSURANCE_UNDERWRITING = "insurance_underwriting"
    TRADING_ALGORITHM = "trading_algorithm"
    AML_CFT = "aml_cft"
    CUSTOMER_SERVICE = "customer_service"
    FRAUD_DETECTION = "fraud_detection"
    OTHER = "other"


# =============================================================================
# AI System Registry Types
# =============================================================================


@dataclass
class RegisterSystemRequest:
    """Request to register an AI system."""

    system_id: str
    system_name: str
    use_case: AISystemUseCase
    owner_team: str
    customer_impact: int  # 1-5 scale
    model_complexity: int  # 1-5 scale
    human_reliance: int  # 1-5 scale
    description: str | None = None
    technical_owner: str | None = None
    business_owner: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class UpdateSystemRequest:
    """Request to update an AI system."""

    system_name: str | None = None
    description: str | None = None
    owner_team: str | None = None
    technical_owner: str | None = None
    business_owner: str | None = None
    customer_impact: int | None = None
    model_complexity: int | None = None
    human_reliance: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class AISystemRegistry:
    """AI system registry entry."""

    id: str
    org_id: str
    system_id: str
    system_name: str
    use_case: AISystemUseCase
    owner_team: str
    customer_impact: int
    model_complexity: int
    human_reliance: int
    materiality: MaterialityClassification
    status: SystemStatus
    created_at: datetime
    updated_at: datetime
    description: str | None = None
    technical_owner: str | None = None
    business_owner: str | None = None
    metadata: dict[str, Any] | None = None
    created_by: str | None = None


@dataclass
class RegistrySummary:
    """Registry summary statistics."""

    total_systems: int
    active_systems: int
    high_materiality_count: int
    medium_materiality_count: int
    low_materiality_count: int
    by_use_case: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)


@dataclass
class ListSystemsOptions:
    """Options for listing AI systems."""

    status: SystemStatus | None = None
    use_case: AISystemUseCase | None = None
    materiality: MaterialityClassification | None = None
    limit: int = 100
    offset: int = 0


# =============================================================================
# FEAT Assessment Types
# =============================================================================


@dataclass
class CreateAssessmentRequest:
    """Request to create a FEAT assessment."""

    system_id: str
    assessment_type: str = "initial"
    assessors: list[str] | None = None


@dataclass
class UpdateAssessmentRequest:
    """Request to update a FEAT assessment."""

    fairness_score: int | None = None
    ethics_score: int | None = None
    accountability_score: int | None = None
    transparency_score: int | None = None
    fairness_details: dict[str, Any] | None = None
    ethics_details: dict[str, Any] | None = None
    accountability_details: dict[str, Any] | None = None
    transparency_details: dict[str, Any] | None = None
    findings: list[str] | None = None
    recommendations: list[str] | None = None
    assessors: list[str] | None = None


@dataclass
class FEATAssessment:
    """FEAT assessment record."""

    id: str
    org_id: str
    system_id: str
    assessment_type: str
    status: FEATAssessmentStatus
    assessment_date: datetime
    created_at: datetime
    updated_at: datetime
    valid_until: datetime | None = None
    fairness_score: int | None = None
    ethics_score: int | None = None
    accountability_score: int | None = None
    transparency_score: int | None = None
    overall_score: int | None = None
    fairness_details: dict[str, Any] | None = None
    ethics_details: dict[str, Any] | None = None
    accountability_details: dict[str, Any] | None = None
    transparency_details: dict[str, Any] | None = None
    findings: list[str] | None = None
    recommendations: list[str] | None = None
    assessors: list[str] | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_by: str | None = None


@dataclass
class ApproveAssessmentRequest:
    """Request to approve an assessment."""

    approved_by: str
    comments: str | None = None


@dataclass
class RejectAssessmentRequest:
    """Request to reject an assessment."""

    rejected_by: str
    reason: str


@dataclass
class ListAssessmentsOptions:
    """Options for listing assessments."""

    system_id: str | None = None
    status: FEATAssessmentStatus | None = None
    limit: int = 100
    offset: int = 0


# =============================================================================
# Kill Switch Types
# =============================================================================


@dataclass
class KillSwitch:
    """Kill switch configuration."""

    id: str
    org_id: str
    system_id: str
    status: KillSwitchStatus
    auto_trigger_enabled: bool
    created_at: datetime
    updated_at: datetime
    accuracy_threshold: float | None = None
    bias_threshold: float | None = None
    error_rate_threshold: float | None = None
    triggered_at: datetime | None = None
    triggered_by: str | None = None
    triggered_reason: str | None = None
    restored_at: datetime | None = None
    restored_by: str | None = None


@dataclass
class ConfigureKillSwitchRequest:
    """Request to configure a kill switch."""

    accuracy_threshold: float | None = None
    bias_threshold: float | None = None
    error_rate_threshold: float | None = None
    auto_trigger_enabled: bool | None = None


@dataclass
class CheckKillSwitchRequest:
    """Request to check kill switch metrics."""

    accuracy: float
    bias_score: float | None = None
    error_rate: float | None = None


@dataclass
class TriggerKillSwitchRequest:
    """Request to trigger a kill switch."""

    reason: str
    triggered_by: str | None = None


@dataclass
class RestoreKillSwitchRequest:
    """Request to restore a kill switch."""

    reason: str
    restored_by: str | None = None


@dataclass
class KillSwitchEvent:
    """Kill switch event record."""

    id: str
    kill_switch_id: str
    event_type: str
    created_at: datetime
    event_data: dict[str, Any] | None = None
    created_by: str | None = None


# =============================================================================
# Helper Functions
# =============================================================================


def parse_datetime(value: str | datetime | None) -> datetime | None:
    """Parse a datetime value from various formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        # Try ISO format
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # Try other common formats
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def dict_to_ai_system(data: dict[str, Any]) -> AISystemRegistry:
    """Convert a dictionary to an AISystemRegistry object."""
    return AISystemRegistry(
        id=data["id"],
        org_id=data["org_id"],
        system_id=data["system_id"],
        system_name=data["system_name"],
        use_case=AISystemUseCase(data["use_case"]),
        owner_team=data["owner_team"],
        customer_impact=data["customer_impact"],
        model_complexity=data["model_complexity"],
        human_reliance=data["human_reliance"],
        materiality=MaterialityClassification(data["materiality"]),
        status=SystemStatus(data["status"]),
        created_at=parse_datetime(data["created_at"]),
        updated_at=parse_datetime(data["updated_at"]),
        description=data.get("description"),
        technical_owner=data.get("technical_owner"),
        business_owner=data.get("business_owner"),
        metadata=data.get("metadata"),
        created_by=data.get("created_by"),
    )


def dict_to_assessment(data: dict[str, Any]) -> FEATAssessment:
    """Convert a dictionary to a FEATAssessment object."""
    return FEATAssessment(
        id=data["id"],
        org_id=data["org_id"],
        system_id=data["system_id"],
        assessment_type=data["assessment_type"],
        status=FEATAssessmentStatus(data["status"]),
        assessment_date=parse_datetime(data["assessment_date"]),
        created_at=parse_datetime(data["created_at"]),
        updated_at=parse_datetime(data["updated_at"]),
        valid_until=parse_datetime(data.get("valid_until")),
        fairness_score=data.get("fairness_score"),
        ethics_score=data.get("ethics_score"),
        accountability_score=data.get("accountability_score"),
        transparency_score=data.get("transparency_score"),
        overall_score=data.get("overall_score"),
        fairness_details=data.get("fairness_details"),
        ethics_details=data.get("ethics_details"),
        accountability_details=data.get("accountability_details"),
        transparency_details=data.get("transparency_details"),
        findings=data.get("findings"),
        recommendations=data.get("recommendations"),
        assessors=data.get("assessors"),
        approved_by=data.get("approved_by"),
        approved_at=parse_datetime(data.get("approved_at")),
        created_by=data.get("created_by"),
    )


def dict_to_kill_switch(data: dict[str, Any]) -> KillSwitch:
    """Convert a dictionary to a KillSwitch object."""
    return KillSwitch(
        id=data["id"],
        org_id=data["org_id"],
        system_id=data["system_id"],
        status=KillSwitchStatus(data["status"]),
        auto_trigger_enabled=data.get("auto_trigger_enabled", False),
        created_at=parse_datetime(data["created_at"]),
        updated_at=parse_datetime(data["updated_at"]),
        accuracy_threshold=data.get("accuracy_threshold"),
        bias_threshold=data.get("bias_threshold"),
        error_rate_threshold=data.get("error_rate_threshold"),
        triggered_at=parse_datetime(data.get("triggered_at")),
        triggered_by=data.get("triggered_by"),
        triggered_reason=data.get("triggered_reason"),
        restored_at=parse_datetime(data.get("restored_at")),
        restored_by=data.get("restored_by"),
    )


def dict_to_registry_summary(data: dict[str, Any]) -> RegistrySummary:
    """Convert a dictionary to a RegistrySummary object."""
    return RegistrySummary(
        total_systems=data["total_systems"],
        active_systems=data["active_systems"],
        high_materiality_count=data["high_materiality_count"],
        medium_materiality_count=data["medium_materiality_count"],
        low_materiality_count=data["low_materiality_count"],
        by_use_case=data.get("by_use_case", {}),
        by_status=data.get("by_status", {}),
    )


def dict_to_kill_switch_event(data: dict[str, Any]) -> KillSwitchEvent:
    """Convert a dictionary to a KillSwitchEvent object."""
    return KillSwitchEvent(
        id=data["id"],
        kill_switch_id=data["kill_switch_id"],
        event_type=data["event_type"],
        created_at=parse_datetime(data["created_at"]),
        event_data=data.get("event_data"),
        created_by=data.get("created_by"),
    )
