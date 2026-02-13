# Copyright 2026 AxonFlow
# SPDX-License-Identifier: Apache-2.0

"""HITL (Human-in-the-Loop) Queue API Types for AxonFlow SDK.

The HITL Queue API provides enterprise-grade approval workflow management
for AI governance. These types define the request/response structures for
listing, inspecting, approving, and rejecting approval requests triggered
by policy enforcement.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HITLApprovalRequest(BaseModel):
    """A pending or resolved HITL approval request.

    Represents a single approval request that was triggered when a policy
    matched with a ``require_approval`` action. Contains all context needed
    for a human reviewer to make an informed decision.
    """

    request_id: str = Field(..., description="Unique identifier for the approval request")
    org_id: str = Field(..., description="Organization ID")
    tenant_id: str = Field(..., description="Tenant ID")
    client_id: str = Field(..., description="Client ID that triggered the request")
    user_id: str | None = Field(default=None, description="User ID (if available)")
    original_query: str = Field(..., description="The original query that triggered the approval")
    request_type: str = Field(..., description="Type of the original request (e.g. chat, code)")
    request_context: dict[str, Any] | None = Field(
        default=None, description="Additional context from the original request"
    )
    triggered_policy_id: str = Field(
        ..., description="ID of the policy that triggered this request"
    )
    triggered_policy_name: str = Field(..., description="Name of the triggering policy")
    trigger_reason: str = Field(..., description="Human-readable reason for the trigger")
    severity: str = Field(..., description="Severity level (critical, high, medium, low)")
    eu_ai_act_article: str | None = Field(
        default=None, description="EU AI Act article reference (if applicable)"
    )
    compliance_framework: str | None = Field(
        default=None, description="Compliance framework (e.g. GDPR, HIPAA, RBI)"
    )
    risk_classification: str | None = Field(
        default=None, description="Risk classification level"
    )
    status: str = Field(..., description="Current status (pending, approved, rejected, expired)")
    reviewer_id: str | None = Field(default=None, description="ID of the reviewer (if reviewed)")
    reviewer_email: str | None = Field(
        default=None, description="Email of the reviewer (if reviewed)"
    )
    review_comment: str | None = Field(
        default=None, description="Comment from the reviewer (if reviewed)"
    )
    reviewed_at: str | None = Field(
        default=None, description="ISO timestamp of when the review occurred"
    )
    expires_at: str = Field(..., description="ISO timestamp of when the request expires")
    created_at: str = Field(..., description="ISO timestamp of when the request was created")
    updated_at: str = Field(..., description="ISO timestamp of when the request was last updated")


class HITLQueueListOptions(BaseModel):
    """Options for filtering and paginating the HITL approval queue."""

    status: str | None = Field(
        default=None, description="Filter by status (pending, approved, rejected, expired)"
    )
    severity: str | None = Field(
        default=None, description="Filter by severity (critical, high, medium, low)"
    )
    limit: int | None = Field(default=None, ge=1, description="Maximum number of results")
    offset: int | None = Field(default=None, ge=0, description="Offset for pagination")


class HITLQueueListResponse(BaseModel):
    """Response from listing HITL queue approval requests."""

    items: list[HITLApprovalRequest] = Field(
        default_factory=list, description="List of approval requests"
    )
    total: int = Field(default=0, ge=0, description="Total count of matching requests")
    has_more: bool = Field(default=False, description="Whether more results are available")


class HITLReviewInput(BaseModel):
    """Input for approving or rejecting an HITL approval request."""

    reviewer_id: str = Field(..., description="ID of the reviewer performing the action")
    reviewer_email: str = Field(..., description="Email of the reviewer")
    reviewer_role: str | None = Field(
        default=None, description="Role of the reviewer (e.g. admin, compliance_officer)"
    )
    comment: str | None = Field(
        default=None, description="Optional comment explaining the decision"
    )


class HITLStats(BaseModel):
    """Dashboard statistics for the HITL approval queue."""

    total_pending: int = Field(default=0, ge=0, description="Total number of pending requests")
    high_priority: int = Field(
        default=0, ge=0, description="Number of high-severity pending requests"
    )
    critical_priority: int = Field(
        default=0, ge=0, description="Number of critical-severity pending requests"
    )
    oldest_pending_hours: float | None = Field(
        default=None, description="Age of the oldest pending request in hours"
    )
