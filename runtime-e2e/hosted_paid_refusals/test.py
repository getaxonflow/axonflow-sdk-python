"""Check Hosted payment and monthly-cap refusals against a real staging Agent.

Requires separate seeded Basic credentials: one unpaid tenant and one paid
tenant whose monthly governed-event allowance is already exhausted. This
script makes one synthetic governed request with each credential. It refuses
production URLs and never prints credentials.
"""

import asyncio
import os
import sys
from urllib.parse import urlsplit

os.environ["AXONFLOW_TELEMETRY"] = "off"

from axonflow import AxonFlow  # noqa: E402
from axonflow.exceptions import BudgetExceededError, RateLimitError  # noqa: E402


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.stderr.write(f"{name} is required\n")
        sys.exit(2)
    return value


async def check_refusal(endpoint: str, tenant: str, secret: str, expected: type[Exception]) -> None:
    client = AxonFlow(
        endpoint=endpoint,
        client_id=tenant,
        client_secret=secret,
        cache_enabled=False,
    )
    async with client:
        caught: BudgetExceededError | RateLimitError | None = None
        try:
            await client.proxy_llm_call(
                user_token="hosted-sdk-runtime-e2e",
                query="synthetic hosted SDK refusal probe",
                request_type="chat",
            )
        except expected as error:
            caught = error
        except Exception as error:
            message = f"Expected {expected.__name__}, received {type(error).__name__}"
            raise AssertionError(message) from None
        else:
            message = f"Expected {expected.__name__}, received success"
            raise AssertionError(message)

    if expected is BudgetExceededError:
        assert isinstance(caught, BudgetExceededError)
        assert caught.details["error"] == "hosted_payment_required"
        assert caught.details["checkout_method"] == "POST"
        assert caught.details["tenant_id"] == tenant
        checkout = urlsplit(caught.details["checkout_url"])
        assert checkout.scheme == "https"
        assert checkout.netloc == urlsplit(endpoint).netloc
        assert checkout.path == "/api/v1/billing/checkout"
    else:
        assert isinstance(caught, RateLimitError)
        assert caught.details["error"] == "hosted_monthly_limit_reached"
        assert caught.limit_type == "monthly_governed_events"
        assert caught.details["higher_usage_path"] == "paid_enterprise_poc"


async def main() -> None:
    endpoint = required("AXONFLOW_HOSTED_STAGING_AGENT_URL").rstrip("/")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "try-staging.getaxonflow.com"
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        sys.stderr.write("Only the HTTPS Hosted staging Agent origin is allowed\n")
        sys.exit(2)

    unpaid_tenant = required("AXONFLOW_HOSTED_UNPAID_CLIENT_ID")
    unpaid_secret = required("AXONFLOW_HOSTED_UNPAID_CLIENT_SECRET")
    capped_tenant = required("AXONFLOW_HOSTED_CAPPED_CLIENT_ID")
    capped_secret = required("AXONFLOW_HOSTED_CAPPED_CLIENT_SECRET")
    if unpaid_tenant == capped_tenant:
        sys.stderr.write("Unpaid and capped fixtures must use separate tenants\n")
        sys.exit(2)

    await check_refusal(endpoint, unpaid_tenant, unpaid_secret, BudgetExceededError)
    await check_refusal(endpoint, capped_tenant, capped_secret, RateLimitError)
    print("PASS: real Hosted staging 402 and monthly 429 preserve refusal guidance")


if __name__ == "__main__":
    asyncio.run(main())
