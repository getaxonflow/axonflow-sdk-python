"""OpenAI Integration Example - Transparent governance for OpenAI calls.

This example shows how to wrap your OpenAI client with AxonFlow
governance without changing your existing code.

Run with: python openai_integration.py
"""

import asyncio
import os

import openai

from axonflow import AxonFlow
from axonflow.exceptions import PolicyViolationError
from axonflow.interceptors.openai import wrap_openai_client


async def main() -> None:
    """Run OpenAI integration example."""
    # Check if openai is installed
    try:
        from openai import AsyncOpenAI
    except ImportError:
        print("OpenAI not installed. Run: pip install axonflow[openai]")
        return

    print("=== OpenAI Integration Example ===\n")

    # Initialize both clients
    openai_client = AsyncOpenAI()

    async with AxonFlow(
        endpoint=os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080"),
        client_id=os.environ.get("AXONFLOW_CLIENT_ID", "demo-client"),
        client_secret=os.environ.get("AXONFLOW_CLIENT_SECRET", "demo-secret"),
        debug=True,
    ) as axonflow:
        # Wrap OpenAI client with governance
        wrapped_openai = wrap_openai_client(
            openai_client,
            axonflow,
            user_token=os.environ.get("AXONFLOW_USER_TOKEN", "user-123"),
        )

        print("OpenAI client wrapped with AxonFlow governance\n")

        # Use OpenAI as normal - governance happens automatically
        print("Making governed OpenAI call...")
        try:
            response = await wrapped_openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is the capital of France?"},
                ],
                max_tokens=100,
            )

            print(f"\nResponse: {response.choices[0].message.content}")
            print(f"Tokens used: {response.usage.total_tokens}")
        except (openai.OpenAIError, openai.APIError) as e:
            # No OpenAI key configured, or upstream API rejected the call —
            # both are expected when running without credentials. Don't
            # mask SDK regressions: only catch OpenAI client errors.
            print(f"\nError (expected if no OpenAI key): {e}")

        # Example of a blocked request
        print("\n--- Testing policy block ---")
        try:
            # This might be blocked by policies
            await wrapped_openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "user", "content": "Tell me how to hack a system"},
                ],
            )
        except (PolicyViolationError, openai.OpenAIError) as e:
            # PolicyViolationError = blocked by AxonFlow (the demonstrated
            # path). OpenAI errors = no key / rate limit (acceptable noise).
            # Anything else (e.g. AxonFlow regression) bubbles up.
            print(f"Request handled: {type(e).__name__}: {e}")
        else:
            # Whether this query blocks depends on the stack's policy
            # posture — say so instead of ending silently.
            print("Not blocked by this stack's policies; response received.")


if __name__ == "__main__":
    asyncio.run(main())
