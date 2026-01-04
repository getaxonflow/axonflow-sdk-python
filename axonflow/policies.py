"""AxonFlow SDK Policy Types and Methods.

Policy CRUD types and methods for the Unified Policy Architecture v2.0.0.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# Policy Categories and Tiers
# ============================================================================


class PolicyCategory(str, Enum):
    """Policy categories for organization and filtering."""

    # Static policy categories - Security
    SECURITY_SQLI = "security-sqli"
    SECURITY_ADMIN = "security-admin"

    # Static policy categories - PII Detection
    PII_GLOBAL = "pii-global"
    PII_US = "pii-us"
    PII_EU = "pii-eu"
    PII_INDIA = "pii-india"

    # Static policy categories - Code Governance
    CODE_SECRETS = "code-secrets"
    CODE_UNSAFE = "code-unsafe"
    CODE_COMPLIANCE = "code-compliance"

    # Dynamic policy categories
    DYNAMIC_RISK = "dynamic-risk"
    DYNAMIC_COMPLIANCE = "dynamic-compliance"
    DYNAMIC_SECURITY = "dynamic-security"
    DYNAMIC_COST = "dynamic-cost"
    DYNAMIC_ACCESS = "dynamic-access"


class PolicyTier(str, Enum):
    """Policy tiers determine where policies apply."""

    SYSTEM = "system"
    ORGANIZATION = "organization"
    TENANT = "tenant"


class OverrideAction(str, Enum):
    """Override action for policy overrides.

    - BLOCK: Immediately block the request
    - REQUIRE_APPROVAL: Pause for human approval (HITL)
    - REDACT: Mask sensitive content
    - WARN: Log warning, allow request
    - LOG: Audit only
    """

    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"
    REDACT = "redact"
    WARN = "warn"
    LOG = "log"


class PolicyAction(str, Enum):
    """Action to take when policy matches.

    - BLOCK: Immediately block the request
    - REQUIRE_APPROVAL: Pause for human approval (HITL)
    - REDACT: Mask sensitive content
    - WARN: Log warning, allow request
    - LOG: Audit only
    - ALLOW: Explicitly allow (for overrides)
    """

    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"
    REDACT = "redact"
    WARN = "warn"
    LOG = "log"
    ALLOW = "allow"


class PolicySeverity(str, Enum):
    """Policy severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ============================================================================
# Static Policy Types
# ============================================================================


class PolicyOverride(BaseModel):
    """Policy override configuration."""

    policy_id: str
    action_override: OverrideAction
    override_reason: str
    created_by: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    active: bool = True


class StaticPolicy(BaseModel):
    """Static policy definition."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str | None = None
    category: PolicyCategory
    tier: PolicyTier
    pattern: str
    severity: PolicySeverity = PolicySeverity.MEDIUM
    enabled: bool = True
    action: PolicyAction = PolicyAction.BLOCK
    organization_id: str | None = Field(default=None, alias="organizationId")
    tenant_id: str | None = Field(default=None, alias="tenantId")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    version: int | None = None
    has_override: bool | None = Field(default=None, alias="hasOverride")
    override: PolicyOverride | None = None


class ListStaticPoliciesOptions(BaseModel):
    """Options for listing static policies."""

    category: PolicyCategory | None = None
    tier: PolicyTier | None = None
    organization_id: str | None = Field(
        default=None,
        description="Filter by organization ID (Enterprise)",
    )
    enabled: bool | None = None
    limit: int | None = Field(default=None, ge=1)
    offset: int | None = Field(default=None, ge=0)
    sort_by: str | None = None
    sort_order: str | None = None
    search: str | None = None


class CreateStaticPolicyRequest(BaseModel):
    """Request to create a new static policy."""

    name: str = Field(..., min_length=1)
    description: str | None = None
    category: PolicyCategory
    tier: PolicyTier = PolicyTier.TENANT  # Default to tenant tier for custom policies
    organization_id: str | None = Field(
        default=None,
        alias="organization_id",
        description="Organization ID for organization-tier policies (Enterprise)",
    )
    pattern: str = Field(..., min_length=1)
    severity: PolicySeverity = PolicySeverity.MEDIUM
    enabled: bool = True
    action: PolicyAction = PolicyAction.BLOCK

    model_config = ConfigDict(populate_by_name=True)


class UpdateStaticPolicyRequest(BaseModel):
    """Request to update an existing static policy."""

    name: str | None = None
    description: str | None = None
    category: PolicyCategory | None = None
    pattern: str | None = None
    severity: PolicySeverity | None = None
    enabled: bool | None = None
    action: PolicyAction | None = None


class CreatePolicyOverrideRequest(BaseModel):
    """Request to create a policy override."""

    action_override: OverrideAction
    override_reason: str = Field(..., min_length=1)
    expires_at: datetime | None = None


# ============================================================================
# Dynamic Policy Types
# ============================================================================


class DynamicPolicyCondition(BaseModel):
    """Condition for dynamic policy evaluation."""

    field: str
    operator: str
    value: Any


class DynamicPolicyAction(BaseModel):
    """Action to take when dynamic policy conditions are met."""

    type: str  # "block", "alert", "redact", "log", "route", "modify_risk"
    config: dict[str, Any] = Field(default_factory=dict)


class DynamicPolicy(BaseModel):
    """Dynamic policy definition.

    Dynamic policies are LLM-powered policies that can evaluate complex,
    context-aware rules that can't be expressed with simple regex patterns.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str | None = None
    type: str  # "risk", "content", "user", "cost"
    conditions: list[DynamicPolicyCondition] | None = None
    actions: list[DynamicPolicyAction] | None = None
    priority: int = 0
    enabled: bool = True
    created_at: datetime = Field(..., alias="created_at")
    updated_at: datetime = Field(..., alias="updated_at")


class ListDynamicPoliciesOptions(BaseModel):
    """Options for listing dynamic policies."""

    type: str | None = None  # Filter by policy type
    enabled: bool | None = None
    limit: int | None = Field(default=None, ge=1)
    offset: int | None = Field(default=None, ge=0)
    sort_by: str | None = None
    sort_order: str | None = None
    search: str | None = None


class CreateDynamicPolicyRequest(BaseModel):
    """Request to create a dynamic policy."""

    name: str = Field(..., min_length=1)
    description: str | None = None
    type: str  # "risk", "content", "user", "cost"
    conditions: list[DynamicPolicyCondition] | None = None
    actions: list[DynamicPolicyAction] | None = None
    priority: int = 0
    enabled: bool = True


class UpdateDynamicPolicyRequest(BaseModel):
    """Request to update a dynamic policy."""

    name: str | None = None
    description: str | None = None
    type: str | None = None
    conditions: list[DynamicPolicyCondition] | None = None
    actions: list[DynamicPolicyAction] | None = None
    priority: int | None = None
    enabled: bool | None = None


# ============================================================================
# Pattern Testing Types
# ============================================================================


class TestPatternMatch(BaseModel):
    """Individual pattern match result."""

    model_config = ConfigDict(populate_by_name=True)

    input: str
    matched: bool
    groups: list[str] | None = None


class TestPatternResult(BaseModel):
    """Result of testing a regex pattern."""

    valid: bool
    error: str | None = None
    pattern: str = ""
    inputs: list[str] = Field(default_factory=list)
    matches: list[TestPatternMatch] = Field(default_factory=list)


# ============================================================================
# Policy Version Types
# ============================================================================


class PolicyVersion(BaseModel):
    """Policy version history entry."""

    model_config = ConfigDict(populate_by_name=True)

    version: int
    changed_by: str | None = Field(default=None, alias="changedBy")
    changed_at: datetime = Field(..., alias="changedAt")
    change_type: str = Field(..., alias="changeType")
    change_description: str | None = Field(default=None, alias="changeDescription")
    previous_values: dict[str, Any] | None = Field(default=None, alias="previousValues")
    new_values: dict[str, Any] | None = Field(default=None, alias="newValues")


# ============================================================================
# Effective Policies Types
# ============================================================================


class EffectivePoliciesOptions(BaseModel):
    """Options for getting effective policies."""

    category: PolicyCategory | None = None
    include_disabled: bool = False
    include_overridden: bool = False
