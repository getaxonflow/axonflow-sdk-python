"""AxonFlow SDK Policy Types and Methods.

Policy CRUD types and methods for the Unified Policy Architecture v2.0.0.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

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
    PII_SINGAPORE = "pii-singapore"
    PII_INDONESIA = "pii-indonesia"

    # Static policy categories - Code Governance
    CODE_SECRETS = "code-secrets"
    CODE_UNSAFE = "code-unsafe"
    CODE_COMPLIANCE = "code-compliance"

    # Sensitive data category
    SENSITIVE_DATA = "sensitive-data"

    # Dynamic policy categories
    DYNAMIC_RISK = "dynamic-risk"
    DYNAMIC_COMPLIANCE = "dynamic-compliance"
    DYNAMIC_SECURITY = "dynamic-security"
    DYNAMIC_COST = "dynamic-cost"
    DYNAMIC_ACCESS = "dynamic-access"

    # Media governance categories
    MEDIA_SAFETY = "media-safety"
    MEDIA_BIOMETRIC = "media-biometric"
    MEDIA_DOCUMENT = "media-document"
    MEDIA_PII = "media-pii"


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

    id: str | None = Field(
        default=None, description="Override identifier (required to revoke a specific override)."
    )
    policy_id: str
    action_override: OverrideAction
    override_reason: str
    created_by: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    enabled_override: bool | None = Field(
        default=None,
        description="Override enabled status — canonical wire field.",
    )
    active: bool = Field(
        default=True,
        description=(
            "DEPRECATED: the wire emits `enabled_override`, not `active`. "
            "Use `enabled_override`. Removed in v7."
        ),
    )


class StaticPolicy(BaseModel):
    """Static policy definition."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    policy_id: str | None = Field(
        default=None,
        description=(
            "Human-readable policy identifier (e.g. `sys_sqli_union_select`). "
            "Distinct from `id` (the UUID)."
        ),
    )
    name: str
    description: str | None = None
    category: PolicyCategory
    tier: PolicyTier
    pattern: str
    severity: PolicySeverity = PolicySeverity.MEDIUM
    enabled: bool = True
    action: PolicyAction = PolicyAction.BLOCK
    priority: int | None = Field(
        default=None, description="Evaluation order — lower values run first."
    )
    organization_id: str | None = Field(
        default=None, validation_alias=AliasChoices("organization_id", "organizationId")
    )
    tenant_id: str | None = Field(
        default=None, validation_alias=AliasChoices("tenant_id", "tenantId")
    )
    created_at: datetime = Field(..., validation_alias=AliasChoices("created_at", "createdAt"))
    updated_at: datetime = Field(..., validation_alias=AliasChoices("updated_at", "updatedAt"))
    version: int | None = None
    has_override: bool | None = Field(
        default=None, validation_alias=AliasChoices("has_override", "hasOverride")
    )
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
    priority: int | None = Field(
        default=None, description="Evaluation order — lower values run first."
    )
    tags: list[str] | None = Field(
        default=None, description="Free-form tags for grouping and filtering."
    )

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
    priority: int | None = Field(default=None, description="Updated evaluation priority.")
    tags: list[str] | None = Field(default=None, description="Updated tag set.")


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

    For provider restrictions (GDPR, HIPAA, RBI compliance), use action config:
        actions=[DynamicPolicyAction(type="route",
            config={"allowed_providers": ["ollama", "azure-eu"]})]
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str | None = None
    type: str | None = None  # "risk", "content", "user", "cost"
    category: str | None = None  # "dynamic-risk", "dynamic-compliance", etc.
    tier: PolicyTier | None = None
    organization_id: str | None = Field(default=None, alias="organization_id")
    conditions: list[DynamicPolicyCondition] | None = None
    actions: list[DynamicPolicyAction] | None = None
    priority: int = 0
    enabled: bool = True
    created_at: datetime | None = Field(default=None, alias="created_at")
    updated_at: datetime | None = Field(default=None, alias="updated_at")


class ListDynamicPoliciesOptions(BaseModel):
    """Options for listing dynamic policies."""

    type: str | None = None  # Filter by policy type
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


class CreateDynamicPolicyRequest(BaseModel):
    """Request to create a dynamic policy.

    For provider restrictions, use action config with "allowed_providers" key.
    """

    name: str = Field(..., min_length=1)
    description: str | None = None
    type: str = "risk"  # "risk", "content", "user", "cost"
    category: str = "dynamic-risk"  # Must start with "dynamic-" for dynamic policies
    tier: PolicyTier = PolicyTier.TENANT
    organization_id: str | None = Field(
        default=None,
        alias="organization_id",
        description="Organization ID for organization-tier policies",
    )
    conditions: list[DynamicPolicyCondition] | None = None
    actions: list[DynamicPolicyAction] | None = None
    priority: int = 0
    enabled: bool = True

    model_config = ConfigDict(populate_by_name=True)


class UpdateDynamicPolicyRequest(BaseModel):
    """Request to update a dynamic policy.

    For provider restrictions, use action config with "allowed_providers" key.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    type: str | None = None
    category: str | None = None  # Must start with "dynamic-" if specified
    tier: PolicyTier | None = None
    organization_id: str | None = Field(
        default=None,
        alias="organization_id",
        description="Organization ID for organization-tier policies",
    )
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
    """Policy version history entry.

    The wire shape is an immutable snapshot, not a before/after diff.
    The legacy ``changeDescription``, ``previousValues``, ``newValues``
    aliases are kept for source-compat but the server actually emits
    ``change_summary`` and a single ``snapshot`` object. Use those.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, description="Snapshot identifier (UUID).")
    policy_id: str | None = Field(default=None, description="Policy this snapshot belongs to.")
    version: int
    changed_by: str | None = Field(
        default=None, validation_alias=AliasChoices("changed_by", "changedBy")
    )
    changed_at: datetime = Field(..., validation_alias=AliasChoices("changed_at", "changedAt"))
    change_type: str = Field(..., validation_alias=AliasChoices("change_type", "changeType"))
    change_summary: str | None = Field(
        default=None, description="Summary of the change (canonical wire field)."
    )
    snapshot: dict[str, Any] | None = Field(
        default=None, description="Complete policy state at this version (canonical wire field)."
    )
    change_description: str | None = Field(
        default=None,
        validation_alias=AliasChoices("change_description", "changeDescription"),
        description=(
            "DEPRECATED: the wire field is `change_summary`. Always reads None. Removed in v7."
        ),
    )
    previous_values: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("previous_values", "previousValues"),
        description=(
            "DEPRECATED: the wire emits a single `snapshot`, not before/after diffs. Removed in v7."
        ),
    )
    new_values: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("new_values", "newValues"),
        description="DEPRECATED: same as `previous_values`. Use `snapshot`. Removed in v7.",
    )


# ============================================================================
# Effective Policies Types
# ============================================================================


class EffectivePoliciesOptions(BaseModel):
    """Options for getting effective policies."""

    category: PolicyCategory | None = None
    include_disabled: bool = False
    include_overridden: bool = False
