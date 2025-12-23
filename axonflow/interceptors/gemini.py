"""Google Gemini Interceptor for transparent governance.

Wraps Google Gemini GenerativeModel to automatically apply AxonFlow governance
without changing application code.

Example:
    >>> import google.generativeai as genai
    >>> from axonflow import AxonFlow
    >>> from axonflow.interceptors.gemini import wrap_gemini_model
    >>>
    >>> genai.configure(api_key="your-api-key")
    >>> model = genai.GenerativeModel('gemini-pro')
    >>> axonflow = AxonFlow(...)
    >>>
    >>> # Wrap the model - governance is now automatic
    >>> wrapped = wrap_gemini_model(model, axonflow, user_token="user-123")
    >>>
    >>> # Use as normal - governance happens invisibly
    >>> response = wrapped.generate_content("What is AI governance?")
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


class GeminiInterceptor(BaseInterceptor):
    """Interceptor for Google Gemini GenerativeModel."""

    def get_provider_name(self) -> str:
        """Get the provider name."""
        return "gemini"

    def extract_prompt(self, *args: Any, **_kwargs: Any) -> str:
        """Extract prompt from generate_content arguments."""
        # First positional argument is usually the prompt or contents
        if args:
            content = args[0]
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # List of Content objects or strings
                texts = []
                for item in content:
                    if isinstance(item, str):
                        texts.append(item)
                    elif hasattr(item, "parts"):
                        for part in item.parts:
                            if hasattr(part, "text"):
                                texts.append(part.text)
                return " ".join(texts)
        return ""

    def wrap(self, model: Any) -> Any:
        """Wrap Gemini model with governance."""
        return wrap_gemini_model(model, self.axonflow, user_token=self.user_token)


def wrap_gemini_model(
    gemini_model: Any,
    axonflow: AxonFlow,
    *,
    user_token: str = "",
    model_name: str = "gemini-pro",
) -> Any:
    """Wrap Gemini GenerativeModel with AxonFlow governance.

    Args:
        gemini_model: Gemini GenerativeModel to wrap
        axonflow: AxonFlow client for governance
        user_token: User token for policy evaluation
        model_name: Model name for audit logging

    Returns:
        Wrapped Gemini model with automatic governance
    """
    original_generate = gemini_model.generate_content
    original_generate_async = getattr(gemini_model, "generate_content_async", None)

    def _extract_prompt(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        """Extract prompt from arguments."""
        if args:
            content = args[0]
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = []
                for item in content:
                    if isinstance(item, str):
                        texts.append(item)
                    elif hasattr(item, "parts"):
                        for part in item.parts:
                            if hasattr(part, "text"):
                                texts.append(part.text)
                return " ".join(texts)
        # Check kwargs for 'contents' or 'prompt'
        contents = kwargs.get("contents", kwargs.get("prompt", ""))
        if isinstance(contents, str):
            return contents
        return ""

    def _get_loop() -> asyncio.AbstractEventLoop:
        """Get or create event loop."""
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    @wraps(original_generate)
    def sync_wrapped_generate(*args: Any, **kwargs: Any) -> Any:
        prompt = _extract_prompt(args, kwargs)

        # Check with AxonFlow (sync)
        loop = _get_loop()
        response = loop.run_until_complete(
            axonflow.execute_query(
                user_token=user_token,
                query=prompt,
                request_type="llm_chat",
                context={
                    "provider": "gemini",
                    "model": model_name,
                },
            )
        )

        if response.blocked:
            raise PolicyViolationError(response.block_reason or "Request blocked by policy")

        # Call original
        return original_generate(*args, **kwargs)

    gemini_model.generate_content = sync_wrapped_generate

    # Also wrap async version if available
    if original_generate_async:

        @wraps(original_generate_async)
        async def async_wrapped_generate(*args: Any, **kwargs: Any) -> Any:
            prompt = _extract_prompt(args, kwargs)

            # Check with AxonFlow
            response = await axonflow.execute_query(
                user_token=user_token,
                query=prompt,
                request_type="llm_chat",
                context={
                    "provider": "gemini",
                    "model": model_name,
                },
            )

            if response.blocked:
                raise PolicyViolationError(response.block_reason or "Request blocked by policy")

            # Call original
            return await original_generate_async(*args, **kwargs)

        gemini_model.generate_content_async = async_wrapped_generate

    # Wrap start_chat to return a wrapped ChatSession
    if hasattr(gemini_model, "start_chat"):
        original_start_chat = gemini_model.start_chat

        @wraps(original_start_chat)
        def wrapped_start_chat(*args: Any, **kwargs: Any) -> Any:
            chat = original_start_chat(*args, **kwargs)
            return _wrap_chat_session(chat, axonflow, user_token, model_name)

        gemini_model.start_chat = wrapped_start_chat

    return gemini_model


def _wrap_chat_session(
    chat_session: Any,
    axonflow: AxonFlow,
    user_token: str,
    model_name: str,
) -> Any:
    """Wrap a Gemini ChatSession with governance."""
    if not hasattr(chat_session, "send_message"):
        return chat_session

    original_send = chat_session.send_message
    original_send_async = getattr(chat_session, "send_message_async", None)

    def _get_loop() -> asyncio.AbstractEventLoop:
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    @wraps(original_send)
    def sync_wrapped_send(content: Any, **kwargs: Any) -> Any:
        prompt = content if isinstance(content, str) else str(content)

        loop = _get_loop()
        response = loop.run_until_complete(
            axonflow.execute_query(
                user_token=user_token,
                query=prompt,
                request_type="llm_chat",
                context={
                    "provider": "gemini",
                    "model": model_name,
                    "chat_session": True,
                },
            )
        )

        if response.blocked:
            raise PolicyViolationError(response.block_reason or "Request blocked by policy")

        return original_send(content, **kwargs)

    chat_session.send_message = sync_wrapped_send

    if original_send_async:

        @wraps(original_send_async)
        async def async_wrapped_send(content: Any, **kwargs: Any) -> Any:
            prompt = content if isinstance(content, str) else str(content)

            response = await axonflow.execute_query(
                user_token=user_token,
                query=prompt,
                request_type="llm_chat",
                context={
                    "provider": "gemini",
                    "model": model_name,
                    "chat_session": True,
                },
            )

            if response.blocked:
                raise PolicyViolationError(response.block_reason or "Request blocked by policy")

            return await original_send_async(content, **kwargs)

        chat_session.send_message_async = async_wrapped_send

    return chat_session


def create_gemini_wrapper(
    axonflow: AxonFlow,
    user_token: str = "",
    model_name: str = "gemini-pro",
) -> Callable[[Any], Any]:
    """Create a wrapper function for Gemini model.

    Args:
        axonflow: AxonFlow client for governance
        user_token: User token for policy evaluation
        model_name: Model name for audit logging

    Returns:
        Wrapper function that takes a Gemini model
    """

    def wrapper(gemini_model: Any) -> Any:
        return wrap_gemini_model(
            gemini_model, axonflow, user_token=user_token, model_name=model_name
        )

    return wrapper
