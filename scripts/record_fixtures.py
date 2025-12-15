#!/usr/bin/env python3
"""Record real API responses from staging for contract testing.

This script makes actual API calls to staging and saves the responses
as JSON fixtures for use in contract tests.

Run: python scripts/record_fixtures.py
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

# Staging configuration
AGENT_URL = os.environ.get("AXONFLOW_AGENT_URL", "https://staging-eu.getaxonflow.com")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID", "demo-client")
CLIENT_SECRET = os.environ.get("AXONFLOW_CLIENT_SECRET", "demo-secret")

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


async def record_health_response(client: httpx.AsyncClient) -> Optional[dict]:
    """Record health check response."""
    try:
        response = await client.get(f"{AGENT_URL}/health")
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  Health check failed: {e}")
        return None


async def record_successful_query(client: httpx.AsyncClient) -> Optional[dict]:
    """Record a successful query response."""
    try:
        response = await client.post(
            f"{AGENT_URL}/api/request",
            json={
                "query": "What is the capital of France?",
                "user_token": "demo-user",
                "client_id": CLIENT_ID,
                "request_type": "chat",
                "context": {},
            },
        )
        return response.json()
    except Exception as e:
        print(f"  Successful query failed: {e}")
        return None


async def record_blocked_query_pii(client: httpx.AsyncClient) -> Optional[dict]:
    """Record a blocked query response (PII detection)."""
    try:
        response = await client.post(
            f"{AGENT_URL}/api/request",
            json={
                "query": "My SSN is 123-45-6789 and credit card is 4111-1111-1111-1111",
                "user_token": "demo-user",
                "client_id": CLIENT_ID,
                "request_type": "chat",
                "context": {},
            },
        )
        return response.json()
    except Exception as e:
        print(f"  Blocked query (PII) failed: {e}")
        return None


async def record_plan_generation(client: httpx.AsyncClient) -> Optional[dict]:
    """Record a plan generation response."""
    try:
        response = await client.post(
            f"{AGENT_URL}/api/request",
            json={
                "query": "Book a flight from NYC to LA and find a hotel",
                "user_token": "demo-user",
                "client_id": CLIENT_ID,
                "request_type": "multi-agent-plan",
                "context": {"domain": "travel"},
            },
        )
        return response.json()
    except Exception as e:
        print(f"  Plan generation failed: {e}")
        return None


async def record_policy_context(client: httpx.AsyncClient) -> Optional[dict]:
    """Record Gateway Mode policy pre-check response."""
    try:
        response = await client.post(
            f"{AGENT_URL}/api/policy/pre-check",
            json={
                "user_token": "demo-user",
                "client_id": CLIENT_ID,
                "query": "Find patients with recent lab results",
                "data_sources": ["postgres"],
                "context": {"department": "cardiology"},
            },
        )
        return response.json()
    except Exception as e:
        print(f"  Policy pre-check failed: {e}")
        return None


async def record_connector_list(client: httpx.AsyncClient) -> Optional[list]:
    """Record connector list response."""
    try:
        response = await client.get(f"{AGENT_URL}/api/connectors")
        return response.json()
    except Exception as e:
        print(f"  Connector list failed: {e}")
        return None


def save_fixture(name: str, data: Optional[Any]) -> None:
    """Save fixture to JSON file."""
    if data is None:
        print(f"  Skipping {name} - no data")
        return

    filepath = FIXTURES_DIR / f"{name}.json"
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  Saved: {filepath}")


async def main() -> None:
    """Record all fixtures from staging."""
    print(f"\n{'=' * 60}")
    print("Recording API Fixtures from Staging")
    print(f"{'=' * 60}")
    print(f"Agent URL: {AGENT_URL}")
    print(f"Client ID: {CLIENT_ID}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()

    # Ensure fixtures directory exists
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    headers = {
        "Content-Type": "application/json",
        "X-Client-Secret": CLIENT_SECRET,
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # 1. Health check
        print("1. Recording health response...")
        health = await record_health_response(client)
        save_fixture("health_response", health)

        # 2. Successful query
        print("2. Recording successful query response...")
        success = await record_successful_query(client)
        save_fixture("successful_query_response", success)

        # 3. Blocked query (PII)
        print("3. Recording blocked query (PII) response...")
        blocked = await record_blocked_query_pii(client)
        save_fixture("blocked_query_pii_response", blocked)

        # 4. Plan generation
        print("4. Recording plan generation response...")
        plan = await record_plan_generation(client)
        save_fixture("plan_generation_response", plan)

        # 5. Policy context (Gateway Mode)
        print("5. Recording policy context response...")
        policy_ctx = await record_policy_context(client)
        save_fixture("policy_context_response", policy_ctx)

        # 6. Connector list
        print("6. Recording connector list response...")
        connectors = await record_connector_list(client)
        save_fixture("connector_list_response", connectors)

    print(f"\n{'=' * 60}")
    print("Fixture recording complete!")
    print(f"Fixtures saved to: {FIXTURES_DIR}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    asyncio.run(main())
