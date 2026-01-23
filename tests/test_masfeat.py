"""Tests for MAS FEAT compliance module.

This module tests the MAS FEAT (Monetary Authority of Singapore FEAT compliance)
types and client methods.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.masfeat import (
    AISystemRegistry,
    AISystemUseCase,
    FEATAssessment,
    FEATAssessmentStatus,
    FEATPillar,
    Finding,
    FindingSeverity,
    FindingStatus,
    KillSwitch,
    KillSwitchEvent,
    KillSwitchEventType,
    KillSwitchStatus,
    MaterialityClassification,
    RegistrySummary,
    SystemStatus,
    _parse_datetime,
    _parse_enum,
    _parse_findings,
    ai_system_registry_from_dict,
    feat_assessment_from_dict,
    finding_from_dict,
    finding_to_dict,
    kill_switch_event_from_dict,
    kill_switch_from_dict,
    registry_summary_from_dict,
)

# ============================================================================
# Enum Tests
# ============================================================================


class TestMaterialityClassification:
    """Test MaterialityClassification enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert MaterialityClassification.HIGH.value == "high"
        assert MaterialityClassification.MEDIUM.value == "medium"
        assert MaterialityClassification.LOW.value == "low"

    def test_from_string(self) -> None:
        """Test creating enum from string."""
        assert MaterialityClassification("high") == MaterialityClassification.HIGH
        assert MaterialityClassification("medium") == MaterialityClassification.MEDIUM
        assert MaterialityClassification("low") == MaterialityClassification.LOW


class TestSystemStatus:
    """Test SystemStatus enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert SystemStatus.DRAFT.value == "draft"
        assert SystemStatus.ACTIVE.value == "active"
        assert SystemStatus.SUSPENDED.value == "suspended"
        assert SystemStatus.RETIRED.value == "retired"

    def test_from_string(self) -> None:
        """Test creating enum from string."""
        assert SystemStatus("active") == SystemStatus.ACTIVE


class TestFEATAssessmentStatus:
    """Test FEATAssessmentStatus enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert FEATAssessmentStatus.PENDING.value == "pending"
        assert FEATAssessmentStatus.IN_PROGRESS.value == "in_progress"
        assert FEATAssessmentStatus.COMPLETED.value == "completed"
        assert FEATAssessmentStatus.APPROVED.value == "approved"
        assert FEATAssessmentStatus.REJECTED.value == "rejected"


class TestKillSwitchStatus:
    """Test KillSwitchStatus enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert KillSwitchStatus.ENABLED.value == "enabled"
        assert KillSwitchStatus.DISABLED.value == "disabled"
        assert KillSwitchStatus.TRIGGERED.value == "triggered"


class TestFEATPillar:
    """Test FEATPillar enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert FEATPillar.FAIRNESS.value == "fairness"
        assert FEATPillar.ETHICS.value == "ethics"
        assert FEATPillar.ACCOUNTABILITY.value == "accountability"
        assert FEATPillar.TRANSPARENCY.value == "transparency"


class TestAISystemUseCase:
    """Test AISystemUseCase enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert AISystemUseCase.CREDIT_SCORING.value == "credit_scoring"
        assert AISystemUseCase.ROBO_ADVISORY.value == "robo_advisory"
        assert AISystemUseCase.INSURANCE_UNDERWRITING.value == "insurance_underwriting"
        assert AISystemUseCase.TRADING_ALGORITHM.value == "trading_algorithm"
        assert AISystemUseCase.AML_CFT.value == "aml_cft"
        assert AISystemUseCase.CUSTOMER_SERVICE.value == "customer_service"
        assert AISystemUseCase.FRAUD_DETECTION.value == "fraud_detection"
        assert AISystemUseCase.OTHER.value == "other"


class TestKillSwitchEventType:
    """Test KillSwitchEventType enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert KillSwitchEventType.CREATED.value == "created"
        assert KillSwitchEventType.ENABLED.value == "enabled"
        assert KillSwitchEventType.DISABLED.value == "disabled"
        assert KillSwitchEventType.TRIGGERED.value == "triggered"
        assert KillSwitchEventType.RESTORED.value == "restored"
        assert KillSwitchEventType.CONFIGURED.value == "configured"


# ============================================================================
# Helper Function Tests
# ============================================================================


class TestParseDatetime:
    """Test _parse_datetime helper."""

    def test_none_value(self) -> None:
        """Test with None input."""
        assert _parse_datetime(None) is None

    def test_datetime_passthrough(self) -> None:
        """Test with datetime input."""
        dt = datetime(2026, 1, 23, 12, 0, 0, tzinfo=timezone.utc)
        assert _parse_datetime(dt) == dt

    def test_z_suffix(self) -> None:
        """Test ISO format with Z suffix."""
        result = _parse_datetime("2026-01-23T12:00:00Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 23

    def test_offset_format(self) -> None:
        """Test ISO format with offset."""
        result = _parse_datetime("2026-01-23T12:00:00+05:30")
        assert result is not None
        assert result.year == 2026

    def test_nanoseconds_truncation(self) -> None:
        """Test nanosecond precision truncation."""
        result = _parse_datetime("2026-01-23T12:00:00.123456789+00:00")
        assert result is not None
        assert result.microsecond == 123456

    def test_short_fractional_seconds_padding(self) -> None:
        """Test padding of short fractional seconds."""
        result = _parse_datetime("2026-01-23T12:00:00.123+00:00")
        assert result is not None
        assert result.microsecond == 123000

    def test_non_string_non_datetime(self) -> None:
        """Test with non-string, non-datetime input."""
        assert _parse_datetime(12345) is None


class TestParseEnum:
    """Test _parse_enum helper."""

    def test_none_value(self) -> None:
        """Test with None input."""
        assert _parse_enum(SystemStatus, None) is None

    def test_enum_passthrough(self) -> None:
        """Test with enum input."""
        assert _parse_enum(SystemStatus, SystemStatus.ACTIVE) == SystemStatus.ACTIVE

    def test_string_conversion(self) -> None:
        """Test with string input."""
        assert _parse_enum(SystemStatus, "active") == SystemStatus.ACTIVE


# ============================================================================
# Dataclass Tests
# ============================================================================


class TestAISystemRegistry:
    """Test AISystemRegistry dataclass."""

    def test_required_fields(self) -> None:
        """Test creation with required fields."""
        now = datetime.now(timezone.utc)
        registry = AISystemRegistry(
            id="sys-123",
            org_id="org-456",
            system_id="credit-model-v1",
            system_name="Credit Scoring Model",
            use_case=AISystemUseCase.CREDIT_SCORING,
            owner_team="data-science",
            customer_impact=3,
            model_complexity=2,
            human_reliance=1,
            materiality=MaterialityClassification.HIGH,
            status=SystemStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        assert registry.id == "sys-123"
        assert registry.system_name == "Credit Scoring Model"
        assert registry.materiality == MaterialityClassification.HIGH

    def test_optional_fields(self) -> None:
        """Test optional fields default to None."""
        now = datetime.now(timezone.utc)
        registry = AISystemRegistry(
            id="sys-123",
            org_id="org-456",
            system_id="model-v1",
            system_name="Test Model",
            use_case=AISystemUseCase.OTHER,
            owner_team="team",
            customer_impact=1,
            model_complexity=1,
            human_reliance=1,
            materiality=MaterialityClassification.LOW,
            status=SystemStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        assert registry.description is None
        assert registry.technical_owner is None
        assert registry.business_owner is None
        assert registry.metadata is None
        assert registry.created_by is None


class TestRegistrySummary:
    """Test RegistrySummary dataclass."""

    def test_creation(self) -> None:
        """Test summary creation."""
        summary = RegistrySummary(
            total_systems=10,
            active_systems=8,
            high_materiality_count=2,
            medium_materiality_count=5,
            low_materiality_count=3,
        )
        assert summary.total_systems == 10
        assert summary.active_systems == 8
        assert summary.by_use_case == {}
        assert summary.by_status == {}


class TestFEATAssessment:
    """Test FEATAssessment dataclass."""

    def test_creation(self) -> None:
        """Test assessment creation."""
        now = datetime.now(timezone.utc)
        assessment = FEATAssessment(
            id="assess-123",
            org_id="org-456",
            system_id="sys-789",
            assessment_type="annual",
            status=FEATAssessmentStatus.PENDING,
            assessment_date=now,
            created_at=now,
            updated_at=now,
        )
        assert assessment.id == "assess-123"
        assert assessment.status == FEATAssessmentStatus.PENDING
        assert assessment.fairness_score is None


class TestKillSwitch:
    """Test KillSwitch dataclass."""

    def test_creation(self) -> None:
        """Test kill switch creation."""
        now = datetime.now(timezone.utc)
        ks = KillSwitch(
            id="ks-123",
            org_id="org-456",
            system_id="sys-789",
            status=KillSwitchStatus.ENABLED,
            auto_trigger_enabled=True,
            created_at=now,
            updated_at=now,
        )
        assert ks.id == "ks-123"
        assert ks.status == KillSwitchStatus.ENABLED
        assert ks.auto_trigger_enabled is True


class TestKillSwitchEvent:
    """Test KillSwitchEvent dataclass."""

    def test_creation(self) -> None:
        """Test event creation."""
        now = datetime.now(timezone.utc)
        event = KillSwitchEvent(
            id="event-123",
            kill_switch_id="ks-456",
            event_type=KillSwitchEventType.TRIGGERED,
            created_at=now,
        )
        assert event.id == "event-123"
        assert event.event_type == KillSwitchEventType.TRIGGERED


# ============================================================================
# From Dict Conversion Tests
# ============================================================================


class TestAISystemRegistryFromDict:
    """Test ai_system_registry_from_dict conversion."""

    def test_basic_conversion(self) -> None:
        """Test basic dict to dataclass conversion."""
        data = {
            "id": "sys-123",
            "org_id": "org-456",
            "system_id": "model-v1",
            "system_name": "Test Model",
            "use_case": "credit_scoring",
            "owner_team": "team",
            "customer_impact": 3,
            "model_complexity": 2,
            "human_reliance": 1,
            "materiality": "high",
            "status": "active",
            "created_at": "2026-01-23T12:00:00Z",
            "updated_at": "2026-01-23T12:00:00Z",
        }
        result = ai_system_registry_from_dict(data)
        assert result.id == "sys-123"
        assert result.use_case == AISystemUseCase.CREDIT_SCORING
        assert result.materiality == MaterialityClassification.HIGH
        assert result.status == SystemStatus.ACTIVE

    def test_alternate_field_names(self) -> None:
        """Test handling of alternate field names from API."""
        data = {
            "id": "sys-123",
            "org_id": "org-456",
            "system_id": "model-v1",
            "system_name": "Test Model",
            "use_case": "credit_scoring",
            "owner_team": "team",
            "risk_rating_impact": 3,
            "risk_rating_complexity": 2,
            "risk_rating_reliance": 1,
            "materiality_classification": "medium",
            "status": "active",
            "owner_email": "owner@example.com",
            "created_at": "2026-01-23T12:00:00Z",
            "updated_at": "2026-01-23T12:00:00Z",
        }
        result = ai_system_registry_from_dict(data)
        assert result.customer_impact == 3
        assert result.model_complexity == 2
        assert result.human_reliance == 1
        assert result.materiality == MaterialityClassification.MEDIUM
        assert result.business_owner == "owner@example.com"


class TestRegistrySummaryFromDict:
    """Test registry_summary_from_dict conversion."""

    def test_basic_conversion(self) -> None:
        """Test basic dict to dataclass conversion."""
        data = {
            "total_systems": 10,
            "active_systems": 8,
            "high_materiality_count": 2,
            "medium_materiality_count": 5,
            "low_materiality_count": 3,
            "by_use_case": {"credit_scoring": 4, "fraud_detection": 6},
            "by_status": {"active": 8, "draft": 2},
        }
        result = registry_summary_from_dict(data)
        assert result.total_systems == 10
        assert result.by_use_case["credit_scoring"] == 4

    def test_alternate_field_names(self) -> None:
        """Test handling of alternate field names."""
        data = {
            "total_systems": 10,
            "active_systems": 8,
            "high_materiality": 2,
            "medium_materiality": 5,
            "low_materiality": 3,
        }
        result = registry_summary_from_dict(data)
        assert result.high_materiality_count == 2
        assert result.medium_materiality_count == 5


class TestFEATAssessmentFromDict:
    """Test feat_assessment_from_dict conversion."""

    def test_basic_conversion(self) -> None:
        """Test basic dict to dataclass conversion."""
        data = {
            "id": "assess-123",
            "org_id": "org-456",
            "system_id": "sys-789",
            "assessment_type": "annual",
            "status": "completed",
            "assessment_date": "2026-01-23T12:00:00Z",
            "created_at": "2026-01-23T12:00:00Z",
            "updated_at": "2026-01-23T12:00:00Z",
            "fairness_score": 85,
            "ethics_score": 90,
            "accountability_score": 88,
            "transparency_score": 92,
            "overall_score": 89,
        }
        result = feat_assessment_from_dict(data)
        assert result.id == "assess-123"
        assert result.status == FEATAssessmentStatus.COMPLETED
        assert result.overall_score == 89


class TestKillSwitchFromDict:
    """Test kill_switch_from_dict conversion."""

    def test_basic_conversion(self) -> None:
        """Test basic dict to dataclass conversion."""
        data = {
            "id": "ks-123",
            "org_id": "org-456",
            "system_id": "sys-789",
            "status": "enabled",
            "auto_trigger_enabled": True,
            "accuracy_threshold": 0.95,
            "bias_threshold": 0.10,
            "error_rate_threshold": 0.05,
            "created_at": "2026-01-23T12:00:00Z",
            "updated_at": "2026-01-23T12:00:00Z",
        }
        result = kill_switch_from_dict(data)
        assert result.id == "ks-123"
        assert result.status == KillSwitchStatus.ENABLED
        assert result.accuracy_threshold == 0.95

    def test_nested_response_format(self) -> None:
        """Test handling of nested API response format."""
        data = {
            "kill_switch": {
                "id": "ks-123",
                "org_id": "org-456",
                "system_id": "sys-789",
                "status": "triggered",
                "auto_trigger_enabled": True,
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
            "message": "Kill switch triggered successfully",
        }
        result = kill_switch_from_dict(data)
        assert result.id == "ks-123"
        assert result.status == KillSwitchStatus.TRIGGERED

    def test_trigger_reason_field(self) -> None:
        """Test handling of trigger_reason field name."""
        data = {
            "id": "ks-123",
            "org_id": "org-456",
            "system_id": "sys-789",
            "status": "triggered",
            "auto_trigger_enabled": True,
            "trigger_reason": "Bias threshold exceeded",
            "created_at": "2026-01-23T12:00:00Z",
            "updated_at": "2026-01-23T12:00:00Z",
        }
        result = kill_switch_from_dict(data)
        assert result.triggered_reason == "Bias threshold exceeded"


class TestKillSwitchEventFromDict:
    """Test kill_switch_event_from_dict conversion."""

    def test_basic_conversion(self) -> None:
        """Test basic dict to dataclass conversion."""
        data = {
            "id": "event-123",
            "kill_switch_id": "ks-456",
            "event_type": "triggered",
            "event_data": {"reason": "Manual trigger"},
            "created_by": "user@example.com",
            "created_at": "2026-01-23T12:00:00Z",
        }
        result = kill_switch_event_from_dict(data)
        assert result.id == "event-123"
        assert result.event_type == KillSwitchEventType.TRIGGERED
        assert result.event_data["reason"] == "Manual trigger"

    def test_alternate_field_names(self) -> None:
        """Test handling of alternate field names from API."""
        data = {
            "id": "event-123",
            "kill_switch_id": "ks-456",
            "action": "triggered",
            "performed_by": "user@example.com",
            "performed_at": "2026-01-23T12:00:00Z",
            "previous_status": "enabled",
            "new_status": "triggered",
            "reason": "Test trigger",
        }
        result = kill_switch_event_from_dict(data)
        assert result.id == "event-123"
        assert result.event_type == KillSwitchEventType.TRIGGERED
        assert result.created_by == "user@example.com"
        assert result.event_data is not None
        assert result.event_data["reason"] == "Test trigger"


class TestFindingFromDict:
    """Test finding_from_dict conversion."""

    def test_basic_conversion(self) -> None:
        """Test basic dict to dataclass conversion."""
        data = {
            "id": "f-1",
            "pillar": "fairness",
            "severity": "major",
            "category": "bias",
            "description": "Potential bias in model",
            "status": "open",
        }
        result = finding_from_dict(data)
        assert result.id == "f-1"
        assert result.pillar == FEATPillar.FAIRNESS
        assert result.severity == FindingSeverity.MAJOR
        assert result.status == FindingStatus.OPEN

    def test_with_optional_fields(self) -> None:
        """Test with optional fields."""
        data = {
            "id": "f-1",
            "pillar": "ethics",
            "severity": "minor",
            "category": "documentation",
            "description": "Missing documentation",
            "status": "resolved",
            "remediation": "Added documentation",
            "due_date": "2026-02-01T12:00:00Z",
        }
        result = finding_from_dict(data)
        assert result.remediation == "Added documentation"
        assert result.due_date is not None


class TestFindingToDict:
    """Test finding_to_dict conversion."""

    def test_basic_conversion(self) -> None:
        """Test basic finding to dict conversion."""
        finding = Finding(
            id="f-1",
            pillar=FEATPillar.FAIRNESS,
            severity=FindingSeverity.MAJOR,
            category="bias",
            description="Test finding",
            status=FindingStatus.OPEN,
        )
        result = finding_to_dict(finding)
        assert result["id"] == "f-1"
        assert result["pillar"] == "fairness"
        assert result["severity"] == "major"
        assert result["status"] == "open"

    def test_with_optional_fields(self) -> None:
        """Test with optional fields."""
        finding = Finding(
            id="f-1",
            pillar=FEATPillar.ETHICS,
            severity=FindingSeverity.MINOR,
            category="docs",
            description="Test",
            status=FindingStatus.RESOLVED,
            remediation="Fixed",
            due_date=datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        result = finding_to_dict(finding)
        assert result["remediation"] == "Fixed"
        assert "due_date" in result


class TestParseFindings:
    """Test _parse_findings helper."""

    def test_none_value(self) -> None:
        """Test with None input."""
        assert _parse_findings(None) is None

    def test_empty_list(self) -> None:
        """Test with empty list."""
        result = _parse_findings([])
        assert result == []

    def test_list_of_findings(self) -> None:
        """Test with list of finding dicts."""
        data = [
            {
                "id": "f-1",
                "pillar": "fairness",
                "severity": "major",
                "category": "bias",
                "description": "Finding 1",
                "status": "open",
            },
            {
                "id": "f-2",
                "pillar": "ethics",
                "severity": "minor",
                "category": "docs",
                "description": "Finding 2",
                "status": "resolved",
            },
        ]
        result = _parse_findings(data)
        assert result is not None
        assert len(result) == 2
        assert result[0].id == "f-1"
        assert result[1].id == "f-2"


# ============================================================================
# Client Method Tests
# ============================================================================


class TestMASFEATClientMethods:
    """Test MAS FEAT client methods."""

    @pytest.mark.asyncio
    async def test_register_system(self, httpx_mock: HTTPXMock) -> None:
        """Test masfeat_register_system method."""
        httpx_mock.add_response(
            method="POST",
            url="https://test.axonflow.com/api/v1/masfeat/registry",
            json={
                "id": "sys-123",
                "org_id": "org-456",
                "system_id": "model-v1",
                "system_name": "Test Model",
                "use_case": "credit_scoring",
                "owner_team": "team",
                "customer_impact": 3,
                "model_complexity": 2,
                "human_reliance": 1,
                "materiality": "high",
                "status": "draft",
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_register_system(
                system_id="model-v1",
                system_name="Test Model",
                use_case="credit_scoring",
                owner_team="team",
                customer_impact=3,
                model_complexity=2,
                human_reliance=1,
            )
            assert result.id == "sys-123"
            assert result.system_name == "Test Model"

    @pytest.mark.asyncio
    async def test_get_system(self, httpx_mock: HTTPXMock) -> None:
        """Test masfeat_get_system method."""
        httpx_mock.add_response(
            method="GET",
            url="https://test.axonflow.com/api/v1/masfeat/registry/sys-123",
            json={
                "id": "sys-123",
                "org_id": "org-456",
                "system_id": "model-v1",
                "system_name": "Test Model",
                "use_case": "credit_scoring",
                "owner_team": "team",
                "customer_impact": 3,
                "model_complexity": 2,
                "human_reliance": 1,
                "materiality": "high",
                "status": "active",
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_get_system("sys-123")
            assert result.id == "sys-123"

    @pytest.mark.asyncio
    async def test_list_systems(self, httpx_mock: HTTPXMock) -> None:
        """Test masfeat_list_systems method."""
        httpx_mock.add_response(
            method="GET",
            url="https://test.axonflow.com/api/v1/masfeat/registry",
            json=[
                {
                    "id": "sys-1",
                    "org_id": "org-456",
                    "system_id": "model-1",
                    "system_name": "Model 1",
                    "use_case": "credit_scoring",
                    "owner_team": "team",
                    "customer_impact": 3,
                    "model_complexity": 2,
                    "human_reliance": 1,
                    "materiality": "high",
                    "status": "active",
                    "created_at": "2026-01-23T12:00:00Z",
                    "updated_at": "2026-01-23T12:00:00Z",
                },
            ],
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            results = await client.masfeat_list_systems()
            assert len(results) == 1
            assert results[0].id == "sys-1"

    @pytest.mark.asyncio
    async def test_get_registry_summary(self, httpx_mock: HTTPXMock) -> None:
        """Test masfeat_get_registry_summary method."""
        httpx_mock.add_response(
            method="GET",
            url="https://test.axonflow.com/api/v1/masfeat/registry/summary",
            json={
                "total_systems": 10,
                "active_systems": 8,
                "high_materiality_count": 2,
                "medium_materiality_count": 5,
                "low_materiality_count": 3,
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_get_registry_summary()
            assert result.total_systems == 10

    @pytest.mark.asyncio
    async def test_create_assessment(self, httpx_mock: HTTPXMock) -> None:
        """Test masfeat_create_assessment method."""
        httpx_mock.add_response(
            method="POST",
            url="https://test.axonflow.com/api/v1/masfeat/assessments",
            json={
                "id": "assess-123",
                "org_id": "org-456",
                "system_id": "sys-789",
                "assessment_type": "annual",
                "status": "pending",
                "assessment_date": "2026-01-23T12:00:00Z",
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_create_assessment(
                system_id="sys-789",
                assessment_type="annual",
            )
            assert result.id == "assess-123"
            assert result.status == FEATAssessmentStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_kill_switch(self, httpx_mock: HTTPXMock) -> None:
        """Test masfeat_get_kill_switch method."""
        httpx_mock.add_response(
            method="GET",
            url="https://test.axonflow.com/api/v1/masfeat/killswitch/sys-123",
            json={
                "id": "ks-123",
                "org_id": "org-456",
                "system_id": "sys-123",
                "status": "enabled",
                "auto_trigger_enabled": True,
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_get_kill_switch("sys-123")
            assert result.id == "ks-123"
            assert result.status == KillSwitchStatus.ENABLED

    @pytest.mark.asyncio
    async def test_trigger_kill_switch(self, httpx_mock: HTTPXMock) -> None:
        """Test masfeat_trigger_kill_switch method."""
        httpx_mock.add_response(
            method="POST",
            url="https://test.axonflow.com/api/v1/masfeat/killswitch/sys-123/trigger",
            json={
                "kill_switch": {
                    "id": "ks-123",
                    "org_id": "org-456",
                    "system_id": "sys-123",
                    "status": "triggered",
                    "auto_trigger_enabled": True,
                    "triggered_reason": "Manual trigger for testing",
                    "created_at": "2026-01-23T12:00:00Z",
                    "updated_at": "2026-01-23T12:00:00Z",
                },
                "message": "Kill switch triggered",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_trigger_kill_switch(
                "sys-123", reason="Manual trigger for testing"
            )
            assert result.status == KillSwitchStatus.TRIGGERED
            assert result.triggered_reason == "Manual trigger for testing"

    @pytest.mark.asyncio
    async def test_get_kill_switch_history(self, httpx_mock: HTTPXMock) -> None:
        """Test masfeat_get_kill_switch_history method."""
        httpx_mock.add_response(
            method="GET",
            url="https://test.axonflow.com/api/v1/masfeat/killswitch/sys-123/history",
            json=[
                {
                    "id": "event-1",
                    "kill_switch_id": "ks-123",
                    "event_type": "enabled",
                    "created_at": "2026-01-23T12:00:00Z",
                },
                {
                    "id": "event-2",
                    "kill_switch_id": "ks-123",
                    "event_type": "triggered",
                    "event_data": {"reason": "Test"},
                    "created_at": "2026-01-23T13:00:00Z",
                },
            ],
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            results = await client.masfeat_get_kill_switch_history("sys-123")
            assert len(results) == 2
            assert results[0].event_type == KillSwitchEventType.ENABLED
            assert results[1].event_type == KillSwitchEventType.TRIGGERED

    @pytest.mark.asyncio
    async def test_activate_system(self, httpx_mock: HTTPXMock) -> None:
        """Test activating an AI system."""
        # activate_system uses PUT to update status, not POST to /activate
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/registry/sys-123",
            method="PUT",
            json={
                "id": "sys-123",
                "org_id": "org-456",
                "system_id": "test-model",
                "system_name": "Test Model",
                "use_case": "credit_scoring",
                "owner_team": "team",
                "materiality": "high",
                "status": "active",
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_activate_system("sys-123")
            assert result.status == SystemStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_retire_system(self, httpx_mock: HTTPXMock) -> None:
        """Test retiring an AI system."""
        # retire_system uses DELETE, not POST to /retire
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/registry/sys-123",
            method="DELETE",
            json={
                "id": "sys-123",
                "org_id": "org-456",
                "system_id": "test-model",
                "system_name": "Test Model",
                "use_case": "credit_scoring",
                "owner_team": "team",
                "materiality": "high",
                "status": "retired",
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_retire_system("sys-123")
            assert result.status == SystemStatus.RETIRED

    @pytest.mark.asyncio
    async def test_get_assessment(self, httpx_mock: HTTPXMock) -> None:
        """Test getting a FEAT assessment."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/assessments/assess-123",
            method="GET",
            json={
                "id": "assess-123",
                "org_id": "org-456",
                "system_id": "sys-789",
                "assessment_type": "annual",
                "status": "completed",
                "assessment_date": "2026-01-23T12:00:00Z",
                "overall_score": 85,
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_get_assessment("assess-123")
            assert result.id == "assess-123"
            assert result.overall_score == 85

    @pytest.mark.asyncio
    async def test_submit_assessment(self, httpx_mock: HTTPXMock) -> None:
        """Test submitting a FEAT assessment."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/assessments/assess-123/submit",
            method="POST",
            json={
                "id": "assess-123",
                "org_id": "org-456",
                "system_id": "sys-789",
                "assessment_type": "annual",
                "status": "completed",
                "assessment_date": "2026-01-23T12:00:00Z",
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_submit_assessment("assess-123")
            assert result.status == FEATAssessmentStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_approve_assessment(self, httpx_mock: HTTPXMock) -> None:
        """Test approving a FEAT assessment."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/assessments/assess-123/approve",
            method="POST",
            json={
                "id": "assess-123",
                "org_id": "org-456",
                "system_id": "sys-789",
                "assessment_type": "annual",
                "status": "approved",
                "assessment_date": "2026-01-23T12:00:00Z",
                "approved_by": "admin@example.com",
                "approved_at": "2026-01-23T13:00:00Z",
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T13:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_approve_assessment(
                "assess-123", approved_by="admin@example.com"
            )
            assert result.status == FEATAssessmentStatus.APPROVED
            assert result.approved_by == "admin@example.com"

    @pytest.mark.asyncio
    async def test_configure_kill_switch(self, httpx_mock: HTTPXMock) -> None:
        """Test configuring a kill switch."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/killswitch/sys-123/configure",
            method="POST",
            json={
                "id": "ks-123",
                "org_id": "org-456",
                "system_id": "sys-123",
                "status": "enabled",
                "auto_trigger_enabled": True,
                "accuracy_threshold": 0.95,
                "bias_threshold": 0.1,
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_configure_kill_switch(
                "sys-123",
                accuracy_threshold=0.95,
                bias_threshold=0.1,
                auto_trigger_enabled=True,
            )
            assert result.accuracy_threshold == 0.95
            assert result.auto_trigger_enabled is True

    @pytest.mark.asyncio
    async def test_restore_kill_switch(self, httpx_mock: HTTPXMock) -> None:
        """Test restoring a kill switch."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/killswitch/sys-123/restore",
            method="POST",
            json={
                "kill_switch": {
                    "id": "ks-123",
                    "org_id": "org-456",
                    "system_id": "sys-123",
                    "status": "enabled",
                    "auto_trigger_enabled": True,
                    "created_at": "2026-01-23T12:00:00Z",
                    "updated_at": "2026-01-23T12:00:00Z",
                },
                "message": "Kill switch restored",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_restore_kill_switch("sys-123", reason="Issue resolved")
            assert result.status == KillSwitchStatus.ENABLED

    @pytest.mark.asyncio
    async def test_enable_kill_switch(self, httpx_mock: HTTPXMock) -> None:
        """Test enabling a kill switch."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/killswitch/sys-123/enable",
            method="POST",
            json={
                "id": "ks-123",
                "org_id": "org-456",
                "system_id": "sys-123",
                "status": "enabled",
                "auto_trigger_enabled": True,
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_enable_kill_switch("sys-123")
            assert result.status == KillSwitchStatus.ENABLED

    @pytest.mark.asyncio
    async def test_disable_kill_switch(self, httpx_mock: HTTPXMock) -> None:
        """Test disabling a kill switch."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/killswitch/sys-123/disable",
            method="POST",
            json={
                "id": "ks-123",
                "org_id": "org-456",
                "system_id": "sys-123",
                "status": "disabled",
                "auto_trigger_enabled": False,
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_disable_kill_switch("sys-123")
            assert result.status == KillSwitchStatus.DISABLED

    @pytest.mark.asyncio
    async def test_update_system(self, httpx_mock: HTTPXMock) -> None:
        """Test updating an AI system."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/registry/sys-123",
            method="PUT",
            json={
                "id": "sys-123",
                "org_id": "org-456",
                "system_id": "test-model",
                "system_name": "Updated Model Name",
                "use_case": "credit_scoring",
                "owner_team": "new-team",
                "materiality": "high",
                "status": "active",
                "customer_impact": 4,
                "model_complexity": 3,
                "human_reliance": 2,
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T13:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_update_system(
                "sys-123",
                system_name="Updated Model Name",
                owner_team="new-team",
            )
            assert result.system_name == "Updated Model Name"

    @pytest.mark.asyncio
    async def test_list_assessments(self, httpx_mock: HTTPXMock) -> None:
        """Test listing FEAT assessments."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/assessments",
            method="GET",
            json=[
                {
                    "id": "assess-1",
                    "org_id": "org-456",
                    "system_id": "sys-789",
                    "assessment_type": "annual",
                    "status": "completed",
                    "assessment_date": "2026-01-23T12:00:00Z",
                    "created_at": "2026-01-23T12:00:00Z",
                    "updated_at": "2026-01-23T12:00:00Z",
                },
            ],
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            results = await client.masfeat_list_assessments()
            assert len(results) == 1
            assert results[0].id == "assess-1"

    @pytest.mark.asyncio
    async def test_update_assessment(self, httpx_mock: HTTPXMock) -> None:
        """Test updating a FEAT assessment."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/assessments/assess-123",
            method="PUT",
            json={
                "id": "assess-123",
                "org_id": "org-456",
                "system_id": "sys-789",
                "assessment_type": "annual",
                "status": "in_progress",
                "assessment_date": "2026-01-23T12:00:00Z",
                "fairness_score": 85,
                "ethics_score": 90,
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T13:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_update_assessment(
                "assess-123",
                fairness_score=85,
                ethics_score=90,
            )
            assert result.fairness_score == 85
            assert result.ethics_score == 90

    @pytest.mark.asyncio
    async def test_check_kill_switch(self, httpx_mock: HTTPXMock) -> None:
        """Test checking a kill switch with metrics."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/killswitch/sys-123/check",
            method="POST",
            json={
                "id": "ks-123",
                "org_id": "org-456",
                "system_id": "sys-123",
                "status": "enabled",
                "auto_trigger_enabled": True,
                "accuracy_threshold": 0.95,
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T12:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_check_kill_switch(
                "sys-123",
                accuracy=0.92,
                bias_score=0.08,
            )
            assert result.status == KillSwitchStatus.ENABLED

    @pytest.mark.asyncio
    async def test_reject_assessment(self, httpx_mock: HTTPXMock) -> None:
        """Test rejecting a FEAT assessment."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/masfeat/assessments/assess-123/reject",
            method="POST",
            json={
                "id": "assess-123",
                "org_id": "org-456",
                "system_id": "sys-789",
                "assessment_type": "annual",
                "status": "rejected",
                "assessment_date": "2026-01-23T12:00:00Z",
                "created_at": "2026-01-23T12:00:00Z",
                "updated_at": "2026-01-23T13:00:00Z",
            },
        )

        async with AxonFlow(
            endpoint="https://test.axonflow.com",
            client_id="test-client",
            client_secret="test-secret",
        ) as client:
            result = await client.masfeat_reject_assessment(
                "assess-123",
                rejected_by="reviewer@example.com",
                reason="Insufficient documentation",
            )
            assert result.status == FEATAssessmentStatus.REJECTED
