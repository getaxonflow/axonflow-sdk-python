"""AWS Bedrock Interceptor for transparent governance.

Wraps AWS Bedrock Runtime client to automatically apply AxonFlow governance
without changing application code.

Bedrock uses AWS IAM authentication (no API keys required).
Supports multiple model providers: Anthropic Claude, Amazon Titan, Meta Llama, etc.

Example:
    >>> import boto3
    >>> from axonflow import AxonFlow
    >>> from axonflow.interceptors.bedrock import wrap_bedrock_client
    >>>
    >>> bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')
    >>> axonflow = AxonFlow(...)
    >>>
    >>> # Wrap the client - governance is now automatic
    >>> wrapped = wrap_bedrock_client(bedrock, axonflow, user_token="user-123")
    >>>
    >>> # Use as normal - governance happens invisibly
    >>> response = wrapped.invoke_model(
    ...     modelId='anthropic.claude-3-sonnet-20240229-v1:0',
    ...     body=json.dumps({...})
    ... )
"""

from __future__ import annotations

import asyncio
import json
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from axonflow.exceptions import PolicyViolationError
from axonflow.interceptors.base import BaseInterceptor

if TYPE_CHECKING:
    from axonflow.client import AxonFlow

T = TypeVar("T")


# Common Bedrock model IDs
class BedrockModels:
    """Common Bedrock model identifiers."""

    # Anthropic Claude models
    CLAUDE_3_OPUS = "anthropic.claude-3-opus-20240229-v1:0"
    CLAUDE_3_SONNET = "anthropic.claude-3-sonnet-20240229-v1:0"
    CLAUDE_3_HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    CLAUDE_2_1 = "anthropic.claude-v2:1"
    CLAUDE_2 = "anthropic.claude-v2"
    CLAUDE_INSTANT = "anthropic.claude-instant-v1"

    # Amazon Titan models
    TITAN_TEXT_EXPRESS = "amazon.titan-text-express-v1"
    TITAN_TEXT_LITE = "amazon.titan-text-lite-v1"
    TITAN_TEXT_PREMIER = "amazon.titan-text-premier-v1:0"

    # Meta Llama models
    LLAMA2_13B = "meta.llama2-13b-chat-v1"
    LLAMA2_70B = "meta.llama2-70b-chat-v1"
    LLAMA3_8B = "meta.llama3-8b-instruct-v1:0"
    LLAMA3_70B = "meta.llama3-70b-instruct-v1:0"


class BedrockInterceptor(BaseInterceptor):
    """Interceptor for AWS Bedrock Runtime client."""

    def get_provider_name(self) -> str:
        """Get the provider name."""
        return "bedrock"

    def extract_prompt(self, *_args: Any, **kwargs: Any) -> str:
        """Extract prompt from invoke_model arguments."""
        body = kwargs.get("body", "")
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8")
        if isinstance(body, str):
            try:
                parsed = json.loads(body)
                # Claude format
                if "messages" in parsed:
                    return " ".join(
                        m.get("content", "") for m in parsed["messages"] if isinstance(m, dict)
                    )
                # Titan format
                if "inputText" in parsed:
                    return str(parsed["inputText"])
                # Generic prompt
                if "prompt" in parsed:
                    return str(parsed["prompt"])
            except json.JSONDecodeError:
                pass
        return ""

    def wrap(self, client: Any) -> Any:
        """Wrap Bedrock client with governance."""
        return wrap_bedrock_client(client, self.axonflow, user_token=self.user_token)


def wrap_bedrock_client(
    bedrock_client: Any,
    axonflow: AxonFlow,
    *,
    user_token: str = "",
) -> Any:
    """Wrap AWS Bedrock Runtime client with AxonFlow governance.

    Args:
        bedrock_client: AWS Bedrock Runtime client (boto3.client('bedrock-runtime'))
        axonflow: AxonFlow client for governance
        user_token: User token for policy evaluation

    Returns:
        Wrapped Bedrock client with automatic governance
    """

    def _extract_prompt(body: Any, _model_id: str) -> str:
        """Extract prompt from request body."""
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8")
        if isinstance(body, str):
            try:
                parsed = json.loads(body)
                # Claude format
                if "messages" in parsed:
                    return " ".join(
                        m.get("content", "") for m in parsed["messages"] if isinstance(m, dict)
                    )
                # Titan format
                if "inputText" in parsed:
                    return str(parsed["inputText"])
                # Generic
                if "prompt" in parsed:
                    return str(parsed["prompt"])
            except json.JSONDecodeError:
                pass
        return ""

    def _get_loop() -> asyncio.AbstractEventLoop:
        """Get or create event loop."""
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    # Wrap invoke_model
    if hasattr(bedrock_client, "invoke_model"):
        original_invoke = bedrock_client.invoke_model

        @wraps(original_invoke)
        def sync_wrapped_invoke(*args: Any, **kwargs: Any) -> Any:
            model_id = kwargs.get("modelId", "unknown")
            body = kwargs.get("body", "")
            prompt = _extract_prompt(body, model_id)

            loop = _get_loop()
            response = loop.run_until_complete(
                axonflow.execute_query(
                    user_token=user_token,
                    query=prompt,
                    request_type="llm_chat",
                    context={
                        "provider": "bedrock",
                        "model": model_id,
                    },
                )
            )

            if response.blocked:
                raise PolicyViolationError(response.block_reason or "Request blocked by policy")

            return original_invoke(*args, **kwargs)

        bedrock_client.invoke_model = sync_wrapped_invoke

    # Wrap invoke_model_with_response_stream
    if hasattr(bedrock_client, "invoke_model_with_response_stream"):
        original_stream = bedrock_client.invoke_model_with_response_stream

        @wraps(original_stream)
        def sync_wrapped_stream(*args: Any, **kwargs: Any) -> Any:
            model_id = kwargs.get("modelId", "unknown")
            body = kwargs.get("body", "")
            prompt = _extract_prompt(body, model_id)

            loop = _get_loop()
            response = loop.run_until_complete(
                axonflow.execute_query(
                    user_token=user_token,
                    query=prompt,
                    request_type="llm_chat",
                    context={
                        "provider": "bedrock",
                        "model": model_id,
                        "streaming": True,
                    },
                )
            )

            if response.blocked:
                raise PolicyViolationError(response.block_reason or "Request blocked by policy")

            return original_stream(*args, **kwargs)

        bedrock_client.invoke_model_with_response_stream = sync_wrapped_stream

    return bedrock_client


def create_bedrock_wrapper(
    axonflow: AxonFlow,
    user_token: str = "",
) -> Callable[[Any], Any]:
    """Create a wrapper function for Bedrock client.

    Args:
        axonflow: AxonFlow client for governance
        user_token: User token for policy evaluation

    Returns:
        Wrapper function that takes a Bedrock client
    """

    def wrapper(bedrock_client: Any) -> Any:
        return wrap_bedrock_client(bedrock_client, axonflow, user_token=user_token)

    return wrapper
