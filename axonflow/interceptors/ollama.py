"""Ollama Interceptor for transparent governance.

Wraps Ollama client to automatically apply AxonFlow governance
without changing application code.

Ollama is a local LLM server that runs on localhost:11434 by default.
No authentication is required.

Example:
    >>> from ollama import Client
    >>> from axonflow import AxonFlow
    >>> from axonflow.interceptors.ollama import wrap_ollama_client
    >>>
    >>> ollama = Client(host='http://localhost:11434')
    >>> axonflow = AxonFlow(...)
    >>>
    >>> # Wrap the client - governance is now automatic
    >>> wrapped = wrap_ollama_client(ollama, axonflow, user_token="user-123")
    >>>
    >>> # Use as normal - governance happens invisibly
    >>> response = wrapped.chat(
    ...     model='llama2',
    ...     messages=[{'role': 'user', 'content': 'Hello!'}]
    ... )
"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from axonflow.exceptions import PolicyViolationError
from axonflow.interceptors.base import BaseInterceptor

if TYPE_CHECKING:
    from axonflow.client import AxonFlow

T = TypeVar("T")


class OllamaInterceptor(BaseInterceptor):
    """Interceptor for Ollama client."""

    def get_provider_name(self) -> str:
        """Get the provider name."""
        return "ollama"

    def extract_prompt(self, *_args: Any, **kwargs: Any) -> str:
        """Extract prompt from chat or generate arguments."""
        # For chat, extract from messages
        messages = kwargs.get("messages", [])
        if messages:
            return " ".join(m.get("content", "") for m in messages if isinstance(m, dict))
        # For generate, extract from prompt
        prompt = kwargs.get("prompt", "")
        if prompt:
            return str(prompt)
        return ""

    def wrap(self, client: Any) -> Any:
        """Wrap Ollama client with governance."""
        return wrap_ollama_client(client, self.axonflow, user_token=self.user_token)


def wrap_ollama_client(
    ollama_client: Any,
    axonflow: AxonFlow,
    *,
    user_token: str = "",
) -> Any:
    """Wrap Ollama client with AxonFlow governance.

    Args:
        ollama_client: Ollama client to wrap
        axonflow: AxonFlow client for governance
        user_token: User token for policy evaluation

    Returns:
        Wrapped Ollama client with automatic governance
    """

    def _extract_chat_prompt(kwargs: dict[str, Any]) -> str:
        """Extract prompt from chat messages."""
        messages = kwargs.get("messages", [])
        return " ".join(m.get("content", "") for m in messages if isinstance(m, dict))

    def _get_loop() -> asyncio.AbstractEventLoop:
        """Get or create event loop."""
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    # Wrap chat method
    if hasattr(ollama_client, "chat"):
        original_chat = ollama_client.chat

        @wraps(original_chat)
        def sync_wrapped_chat(*args: Any, **kwargs: Any) -> Any:
            prompt = _extract_chat_prompt(kwargs)
            model = kwargs.get("model", "llama2")

            loop = _get_loop()
            response = loop.run_until_complete(
                axonflow.execute_query(
                    user_token=user_token,
                    query=prompt,
                    request_type="llm_chat",
                    context={
                        "provider": "ollama",
                        "model": model,
                    },
                )
            )

            if response.blocked:
                raise PolicyViolationError(response.block_reason or "Request blocked by policy")

            return original_chat(*args, **kwargs)

        ollama_client.chat = sync_wrapped_chat

    # Wrap generate method
    if hasattr(ollama_client, "generate"):
        original_generate = ollama_client.generate

        @wraps(original_generate)
        def sync_wrapped_generate(*args: Any, **kwargs: Any) -> Any:
            prompt = kwargs.get("prompt", "")
            model = kwargs.get("model", "llama2")

            loop = _get_loop()
            response = loop.run_until_complete(
                axonflow.execute_query(
                    user_token=user_token,
                    query=prompt,
                    request_type="llm_chat",
                    context={
                        "provider": "ollama",
                        "model": model,
                    },
                )
            )

            if response.blocked:
                raise PolicyViolationError(response.block_reason or "Request blocked by policy")

            return original_generate(*args, **kwargs)

        ollama_client.generate = sync_wrapped_generate

    return ollama_client


async def wrap_ollama_client_async(
    ollama_client: Any,
    axonflow: AxonFlow,
    *,
    user_token: str = "",
) -> Any:
    """Wrap async Ollama client with AxonFlow governance.

    For use with ollama-python's AsyncClient.

    Args:
        ollama_client: Async Ollama client to wrap
        axonflow: AxonFlow client for governance
        user_token: User token for policy evaluation

    Returns:
        Wrapped async Ollama client with automatic governance
    """

    def _extract_chat_prompt(kwargs: dict[str, Any]) -> str:
        messages = kwargs.get("messages", [])
        return " ".join(m.get("content", "") for m in messages if isinstance(m, dict))

    # Wrap async chat method
    if hasattr(ollama_client, "chat"):
        original_chat = ollama_client.chat

        @wraps(original_chat)
        async def async_wrapped_chat(*args: Any, **kwargs: Any) -> Any:
            prompt = _extract_chat_prompt(kwargs)
            model = kwargs.get("model", "llama2")

            response = await axonflow.execute_query(
                user_token=user_token,
                query=prompt,
                request_type="llm_chat",
                context={
                    "provider": "ollama",
                    "model": model,
                },
            )

            if response.blocked:
                raise PolicyViolationError(response.block_reason or "Request blocked by policy")

            return await original_chat(*args, **kwargs)

        ollama_client.chat = async_wrapped_chat

    # Wrap async generate method
    if hasattr(ollama_client, "generate"):
        original_generate = ollama_client.generate

        @wraps(original_generate)
        async def async_wrapped_generate(*args: Any, **kwargs: Any) -> Any:
            prompt = kwargs.get("prompt", "")
            model = kwargs.get("model", "llama2")

            response = await axonflow.execute_query(
                user_token=user_token,
                query=prompt,
                request_type="llm_chat",
                context={
                    "provider": "ollama",
                    "model": model,
                },
            )

            if response.blocked:
                raise PolicyViolationError(response.block_reason or "Request blocked by policy")

            return await original_generate(*args, **kwargs)

        ollama_client.generate = async_wrapped_generate

    return ollama_client


def create_ollama_wrapper(
    axonflow: AxonFlow,
    user_token: str = "",
) -> Callable[[Any], Any]:
    """Create a wrapper function for Ollama client.

    Args:
        axonflow: AxonFlow client for governance
        user_token: User token for policy evaluation

    Returns:
        Wrapper function that takes an Ollama client
    """

    def wrapper(ollama_client: Any) -> Any:
        return wrap_ollama_client(ollama_client, axonflow, user_token=user_token)

    return wrapper
