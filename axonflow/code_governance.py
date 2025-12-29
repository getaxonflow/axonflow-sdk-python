"""Code Governance types for enterprise Git provider integration.

This module provides types for:
- Git provider configuration (GitHub, GitLab, Bitbucket)
- Pull request creation from LLM-generated code
- PR tracking and status synchronization
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class GitProviderType(str, Enum):
    """Supported Git providers."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"


class FileAction(str, Enum):
    """File action for PR files."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


# ============================================================================
# Git Provider Types
# ============================================================================


class ConfigureGitProviderRequest(BaseModel):
    """Request to configure a Git provider."""

    type: GitProviderType = Field(..., description="Provider type")
    token: str | None = Field(default=None, description="Access token")
    base_url: str | None = Field(default=None, description="Base URL for self-hosted")
    app_id: int | None = Field(default=None, description="GitHub App ID")
    installation_id: int | None = Field(default=None, description="GitHub App Installation ID")
    private_key: str | None = Field(default=None, description="GitHub App private key (PEM)")


class ValidateGitProviderRequest(BaseModel):
    """Request to validate Git provider credentials."""

    type: GitProviderType = Field(..., description="Provider type")
    token: str | None = Field(default=None, description="Access token")
    base_url: str | None = Field(default=None, description="Base URL for self-hosted")
    app_id: int | None = Field(default=None, description="GitHub App ID")
    installation_id: int | None = Field(default=None, description="GitHub App Installation ID")
    private_key: str | None = Field(default=None, description="GitHub App private key (PEM)")


class ValidateGitProviderResponse(BaseModel):
    """Response from Git provider validation."""

    valid: bool = Field(..., description="Whether credentials are valid")
    message: str = Field(..., description="Validation message")


class ConfigureGitProviderResponse(BaseModel):
    """Response from Git provider configuration."""

    message: str = Field(..., description="Success message")
    type: str = Field(..., description="Configured provider type")


class GitProviderInfo(BaseModel):
    """Basic info about a configured provider."""

    type: GitProviderType = Field(..., description="Provider type")


class ListGitProvidersResponse(BaseModel):
    """Response listing configured providers."""

    providers: list[GitProviderInfo] = Field(default_factory=list)
    count: int = Field(default=0)


# ============================================================================
# PR/MR Types
# ============================================================================


class CodeFile(BaseModel):
    """A code file to include in a PR."""

    path: str = Field(..., description="File path relative to repo root")
    content: str = Field(..., description="File content")
    language: str | None = Field(default=None, description="Programming language")
    action: FileAction = Field(..., description="File action")


class CreatePRRequest(BaseModel):
    """Request to create a PR from LLM-generated code."""

    owner: str = Field(..., description="Repository owner")
    repo: str = Field(..., description="Repository name")
    title: str = Field(..., description="PR title")
    description: str | None = Field(default=None, description="PR description")
    base_branch: str | None = Field(default=None, description="Base branch")
    branch_name: str | None = Field(default=None, description="Head branch name")
    draft: bool = Field(default=False, description="Create as draft")
    files: list[CodeFile] = Field(..., description="Files to include")
    agent_request_id: str | None = Field(
        default=None, description="Agent request ID for traceability"
    )
    model: str | None = Field(default=None, description="LLM model used")
    policies_checked: list[str] | None = Field(default=None, description="Policies checked")
    secrets_detected: int | None = Field(default=None, description="Secrets detected count")
    unsafe_patterns: int | None = Field(default=None, description="Unsafe patterns count")


class CreatePRResponse(BaseModel):
    """Response from PR creation."""

    pr_id: str = Field(..., description="Internal PR record ID")
    pr_number: int = Field(..., description="PR number on Git provider")
    pr_url: str = Field(..., description="PR URL")
    state: str = Field(..., description="PR state")
    head_branch: str = Field(..., description="Head branch name")
    created_at: datetime = Field(..., description="Creation timestamp")


class PRRecord(BaseModel):
    """A PR record in the system."""

    id: str = Field(..., description="Internal PR record ID")
    pr_number: int = Field(..., description="PR number on Git provider")
    pr_url: str = Field(..., description="PR URL")
    title: str = Field(..., description="PR title")
    state: str = Field(..., description="PR state")
    owner: str = Field(..., description="Repository owner")
    repo: str = Field(..., description="Repository name")
    head_branch: str = Field(..., description="Head branch")
    base_branch: str = Field(..., description="Base branch")
    files_count: int = Field(..., description="Number of files")
    secrets_detected: int = Field(..., description="Secrets detected")
    unsafe_patterns: int = Field(..., description="Unsafe patterns")
    created_at: datetime = Field(..., description="Creation timestamp")
    created_by: str | None = Field(default=None, description="Creator")
    provider_type: str | None = Field(default=None, description="Provider type")


class ListPRsOptions(BaseModel):
    """Options for listing PRs."""

    limit: int | None = Field(default=None, description="Max PRs to return")
    offset: int | None = Field(default=None, description="Pagination offset")
    state: str | None = Field(default=None, description="Filter by state")


class ListPRsResponse(BaseModel):
    """Response listing PRs."""

    prs: list[PRRecord] = Field(default_factory=list, description="PR records")
    count: int = Field(default=0, description="Total count")
