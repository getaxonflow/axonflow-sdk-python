"""Tests for Indonesia PII category constant and cross-border audit fields."""

from __future__ import annotations

from datetime import datetime, timezone

from axonflow.policies import PolicyCategory
from axonflow.types import (
    TRANSFER_BASIS_ADEQUACY,
    TRANSFER_BASIS_CONSENT,
    TRANSFER_BASIS_PASAL_56B_DPA,
    TRANSFER_BASIS_SAFEGUARDS,
    AuditLogEntry,
)


class TestPIIIndonesiaCategory:
    def test_constant_value(self) -> None:
        assert PolicyCategory.PII_INDONESIA.value == "pii-indonesia"

    def test_is_valid_enum_member(self) -> None:
        assert PolicyCategory("pii-indonesia") is PolicyCategory.PII_INDONESIA

    def test_string_representation(self) -> None:
        assert str(PolicyCategory.PII_INDONESIA) == "PolicyCategory.PII_INDONESIA"

    def test_alongside_other_pii_categories(self) -> None:
        pii_categories = [
            PolicyCategory.PII_GLOBAL,
            PolicyCategory.PII_US,
            PolicyCategory.PII_EU,
            PolicyCategory.PII_INDIA,
            PolicyCategory.PII_SINGAPORE,
            PolicyCategory.PII_INDONESIA,
        ]
        values = [c.value for c in pii_categories]
        assert "pii-indonesia" in values
        assert len(set(values)) == len(values)


class TestAuditLogEntryCrossBorderFields:
    def test_fields_populated(self) -> None:
        entry = AuditLogEntry(
            id="aud-001",
            request_id="req-001",
            timestamp=datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
            user_email="analyst@bank.co.id",
            data_residency="ID",
            transfer_basis="adequacy",
        )
        assert entry.data_residency == "ID"
        assert entry.transfer_basis == "adequacy"

    def test_fields_from_dict(self) -> None:
        data = {
            "id": "aud-002",
            "timestamp": "2026-05-26T10:00:00Z",
            "data_residency": "ID",
            "transfer_basis": "safeguards",
        }
        entry = AuditLogEntry.model_validate(data)
        assert entry.data_residency == "ID"
        assert entry.transfer_basis == "safeguards"

    def test_backward_compat_fields_absent(self) -> None:
        data = {
            "id": "aud-003",
            "timestamp": "2026-05-26T10:00:00Z",
            "user_email": "user@company.com",
            "success": True,
            "blocked": False,
        }
        entry = AuditLogEntry.model_validate(data)
        assert entry.data_residency is None
        assert entry.transfer_basis is None

    def test_serialization_omits_none(self) -> None:
        entry = AuditLogEntry(
            id="aud-004",
            timestamp=datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
        )
        data = entry.model_dump(exclude_none=True)
        assert "data_residency" not in data
        assert "transfer_basis" not in data

    def test_serialization_includes_when_set(self) -> None:
        entry = AuditLogEntry(
            id="aud-005",
            timestamp=datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
            data_residency="SG",
            transfer_basis="consent",
        )
        data = entry.model_dump()
        assert data["data_residency"] == "SG"
        assert data["transfer_basis"] == "consent"

    def test_json_round_trip(self) -> None:
        entry = AuditLogEntry(
            id="aud-006",
            timestamp=datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc),
            data_residency="ID",
            transfer_basis="adequacy",
        )
        json_str = entry.model_dump_json()
        restored = AuditLogEntry.model_validate_json(json_str)
        assert restored.data_residency == "ID"
        assert restored.transfer_basis == "adequacy"


class TestTransferBasisPasal56b:
    """v8.4.0 (platform #2513): pasal_56b_dpa accepted, existing values kept."""

    def test_constants_wire_values(self) -> None:
        assert TRANSFER_BASIS_ADEQUACY == "adequacy"
        assert TRANSFER_BASIS_SAFEGUARDS == "safeguards"
        assert TRANSFER_BASIS_PASAL_56B_DPA == "pasal_56b_dpa"
        assert TRANSFER_BASIS_CONSENT == "consent"

    def test_pasal_56b_dpa_round_trips_verbatim(self) -> None:
        entry = AuditLogEntry(
            id="aud-56b",
            timestamp=datetime(2026, 5, 30, 10, 0, 0, tzinfo=timezone.utc),
            data_residency="ID",
            transfer_basis=TRANSFER_BASIS_PASAL_56B_DPA,
        )
        restored = AuditLogEntry.model_validate_json(entry.model_dump_json())
        # never auto-translated to "safeguards"
        assert restored.transfer_basis == "pasal_56b_dpa"

    def test_pasal_56b_dpa_from_wire(self) -> None:
        entry = AuditLogEntry.model_validate(
            {
                "id": "aud-56b-wire",
                "timestamp": "2026-05-30T10:00:00Z",
                "transfer_basis": "pasal_56b_dpa",
            }
        )
        assert entry.transfer_basis == "pasal_56b_dpa"

    def test_backward_compat_safeguards_still_parses(self) -> None:
        # Existing v8.3.0-shaped rows using "safeguards" are unaffected by the widening.
        entry = AuditLogEntry.model_validate(
            {
                "id": "aud-sg",
                "timestamp": "2026-05-26T10:00:00Z",
                "transfer_basis": "safeguards",
            }
        )
        assert entry.transfer_basis == TRANSFER_BASIS_SAFEGUARDS
