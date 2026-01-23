"""
MAS FEAT Compliance Types

Types for the Monetary Authority of Singapore FEAT (Fairness, Ethics,
Accountability, Transparency) compliance module.

Enterprise Feature: Requires AxonFlow Enterprise license.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

# ===========================================================================
# Enums
# ===========================================================================


class MaterialityClassification(str, Enum):
    """Materiality classification based on 3-dimensional risk rating."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SystemStatus(str, Enum):
    """AI System lifecycle status."""

    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class FEATAssessmentStatus(str, Enum):
    """FEAT Assessment lifecycle status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    APPROVED = "approved"
    REJECTED = "rejected"


class KillSwitchStatus(str, Enum):
    """Kill Switch operational status."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    TRIGGERED = "triggered"


class FEATPillar(str, Enum):
    """FEAT framework pillars."""

    FAIRNESS = "fairness"
    ETHICS = "ethics"
    ACCOUNTABILITY = "accountability"
    TRANSPARENCY = "transparency"


class AISystemUseCase(str, Enum):
    """Predefined AI system use cases for MAS compliance."""

    CREDIT_SCORING = "credit_scoring"
    ROBO_ADVISORY = "robo_advisory"
    INSURANCE_UNDERWRITING = "insurance_underwriting"
    TRADING_ALGORITHM = "trading_algorithm"
    AML_CFT = "aml_cft"
    CUSTOMER_SERVICE = "customer_service"
    FRAUD_DETECTION = "fraud_detection"
    OTHER = "other"


class KillSwitchEventType(str, Enum):
    """Kill Switch event types."""

    CREATED = "created"
    ENABLED = "enabled"
    DISABLED = "disabled"
    TRIGGERED = "triggered"
    RESTORED = "restored"
    CONFIGURED = "configured"


# ===========================================================================
# AI System Registry
# ===========================================================================


@dataclass
class AISystemRegistry:
    """Registered AI system in the MAS FEAT registry."""

    id: str
    org_id: str
    system_id: str
    system_name: str
    use_case: AISystemUseCase
    owner_team: str
    customer_impact: int | None
    model_complexity: int | None
    human_reliance: int | None
    materiality: MaterialityClassification
    status: SystemStatus
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    description: Optional[str] = None
    technical_owner: Optional[str] = None
    business_owner: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    created_by: Optional[str] = None


@dataclass
class RegistrySummary:
    """Summary of all AI systems in the registry."""

    total_systems: int
    active_systems: int
    high_materiality_count: int
    medium_materiality_count: int
    low_materiality_count: int
    by_use_case: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)


# ===========================================================================
# FEAT Assessments
# ===========================================================================


@dataclass
class FEATAssessment:
    """FEAT Assessment record."""

    id: str
    org_id: str
    system_id: str
    assessment_type: str
    status: FEATAssessmentStatus
    assessment_date: Optional[datetime]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    valid_until: Optional[datetime] = None
    fairness_score: Optional[int] = None
    ethics_score: Optional[int] = None
    accountability_score: Optional[int] = None
    transparency_score: Optional[int] = None
    overall_score: Optional[int] = None
    fairness_details: Optional[dict[str, Any]] = None
    ethics_details: Optional[dict[str, Any]] = None
    accountability_details: Optional[dict[str, Any]] = None
    transparency_details: Optional[dict[str, Any]] = None
    findings: Optional[list[str]] = None
    recommendations: Optional[list[str]] = None
    assessors: Optional[list[str]] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_by: Optional[str] = None


# ===========================================================================
# Kill Switch
# ===========================================================================


@dataclass
class KillSwitch:
    """Kill Switch configuration and status."""

    id: str
    org_id: str
    system_id: str
    status: KillSwitchStatus
    auto_trigger_enabled: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    accuracy_threshold: Optional[float] = None
    bias_threshold: Optional[float] = None
    error_rate_threshold: Optional[float] = None
    triggered_at: Optional[datetime] = None
    triggered_by: Optional[str] = None
    triggered_reason: Optional[str] = None
    restored_at: Optional[datetime] = None
    restored_by: Optional[str] = None


@dataclass
class KillSwitchEvent:
    """Kill Switch event record."""

    id: str
    kill_switch_id: str
    event_type: KillSwitchEventType
    created_at: Optional[datetime]
    event_data: Optional[dict[str, Any]] = None
    created_by: Optional[str] = None


# ===========================================================================
# Helper Functions
# ===========================================================================


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Parse datetime from API response."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # Handle ISO format with Z suffix
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        # Handle nanosecond precision (Go sends 9 digits, Python supports 6)
        match = re.match(r"(.+\.\d{6})\d*(\+.*)", value)
        if match:
            value = match.group(1) + match.group(2)
        return datetime.fromisoformat(value)
    return None


def _parse_enum(enum_class: type, value: Any) -> Any:
    """Parse enum from API response."""
    if value is None:
        return None
    if isinstance(value, enum_class):
        return value
    return enum_class(value)


def ai_system_registry_from_dict(data: dict[str, Any]) -> AISystemRegistry:
    """Create AISystemRegistry from API response dict."""
    return AISystemRegistry(
        id=data["id"],
        org_id=data["org_id"],
        system_id=data["system_id"],
        system_name=data["system_name"],
        description=data.get("description"),
        use_case=_parse_enum(AISystemUseCase, data["use_case"]),
        owner_team=data["owner_team"],
        technical_owner=data.get("technical_owner"),
        business_owner=data.get("business_owner") or data.get("owner_email"),
        customer_impact=data.get("customer_impact") or data.get("risk_rating_impact"),
        model_complexity=data.get("model_complexity") or data.get("risk_rating_complexity"),
        human_reliance=data.get("human_reliance") or data.get("risk_rating_reliance"),
        materiality=_parse_enum(
            MaterialityClassification,
            data.get("materiality") or data.get("materiality_classification"),
        ),
        status=_parse_enum(SystemStatus, data["status"]),
        metadata=data.get("metadata"),
        created_at=_parse_datetime(data["created_at"]),
        updated_at=_parse_datetime(data["updated_at"]),
        created_by=data.get("created_by"),
    )


def registry_summary_from_dict(data: dict[str, Any]) -> RegistrySummary:
    """Create RegistrySummary from API response dict."""
    return RegistrySummary(
        total_systems=data["total_systems"],
        active_systems=data["active_systems"],
        high_materiality_count=(
            data.get("high_materiality_count") or data.get("high_materiality", 0)
        ),
        medium_materiality_count=(
            data.get("medium_materiality_count") or data.get("medium_materiality", 0)
        ),
        low_materiality_count=data.get("low_materiality_count") or data.get("low_materiality", 0),
        by_use_case=data.get("by_use_case", {}),
        by_status=data.get("by_status", {}),
    )


def feat_assessment_from_dict(data: dict[str, Any]) -> FEATAssessment:
    """Create FEATAssessment from API response dict."""
    return FEATAssessment(
        id=data["id"],
        org_id=data["org_id"],
        system_id=data["system_id"],
        assessment_type=data["assessment_type"],
        status=_parse_enum(FEATAssessmentStatus, data["status"]),
        assessment_date=_parse_datetime(data["assessment_date"]),
        valid_until=_parse_datetime(data.get("valid_until")),
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
        approved_at=_parse_datetime(data.get("approved_at")),
        created_at=_parse_datetime(data["created_at"]),
        updated_at=_parse_datetime(data["updated_at"]),
        created_by=data.get("created_by"),
    )


def kill_switch_from_dict(data: dict[str, Any]) -> KillSwitch:
    """Create KillSwitch from API response dict."""
    # Handle nested response format (trigger/restore return {kill_switch: {...}, message: ...})
    if "kill_switch" in data:
        data = data["kill_switch"]
    return KillSwitch(
        id=data["id"],
        org_id=data["org_id"],
        system_id=data["system_id"],
        status=_parse_enum(KillSwitchStatus, data["status"]),
        accuracy_threshold=data.get("accuracy_threshold"),
        bias_threshold=data.get("bias_threshold"),
        error_rate_threshold=data.get("error_rate_threshold"),
        auto_trigger_enabled=data.get("auto_trigger_enabled", False),
        triggered_at=_parse_datetime(data.get("triggered_at")),
        triggered_by=data.get("triggered_by"),
        triggered_reason=data.get("triggered_reason") or data.get("trigger_reason"),
        restored_at=_parse_datetime(data.get("restored_at")),
        restored_by=data.get("restored_by"),
        created_at=_parse_datetime(data["created_at"]),
        updated_at=_parse_datetime(data["updated_at"]),
    )


def kill_switch_event_from_dict(data: dict[str, Any]) -> KillSwitchEvent:
    """Create KillSwitchEvent from API response dict."""
    return KillSwitchEvent(
        id=data["id"],
        kill_switch_id=data["kill_switch_id"],
        event_type=_parse_enum(KillSwitchEventType, data["event_type"]),
        event_data=data.get("event_data"),
        created_by=data.get("created_by"),
        created_at=_parse_datetime(data["created_at"]),
    )
