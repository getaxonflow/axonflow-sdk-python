"""AxonFlow LangChain adapter — governed BaseChatModel wrapper.

Drop-in replacement for any LangChain ``BaseChatModel`` that transparently
applies AxonFlow pre-check (policy enforcement) before every async LLM call
and records an audit entry afterwards.

Example::

    from langchain_anthropic import ChatAnthropic
    from axonflow import AxonFlow
    from axonflow.adapters import AxonFlowChatModel

    async with AxonFlow(endpoint="http://localhost:8080") as client:
        model = AxonFlowChatModel(
            wrapped=ChatAnthropic(model_name="claude-sonnet-4-6"),
            axonflow=client,
        )
        # Use exactly like ChatAnthropic — in graphs, with bind_tools, etc.
        result = await model.ainvoke(
            messages,
            config={"configurable": {"user_token": "user-jwt"}},
        )

Notes:
    - Governance (pre-check + audit) applies to **async** invocations only.
      The synchronous ``invoke`` path delegates directly to the wrapped model
      without any AxonFlow calls; document this gap for callers.
    - ``with_fallbacks`` wraps each fallback in governance automatically.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from axonflow.exceptions import PolicyViolationError
from axonflow.types import TokenUsage

if TYPE_CHECKING:
    from axonflow import AxonFlow

_logger = logging.getLogger("axonflow.adapters.langchain")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PROVIDER_KEYWORDS: list[tuple[str, str]] = [
    ("openai", "openai"),
    ("anthropic", "anthropic"),
    ("gemini", "google"),
    ("vertexai", "google"),
    ("google", "google"),
    ("bedrock", "bedrock"),
    ("ollama", "ollama"),
    ("cohere", "cohere"),
    ("mistral", "mistral"),
]


def _infer_provider(model: Any) -> str:
    """Derive a provider string from the wrapped model's class / module name."""
    combined = f"{type(model).__name__.lower()} {type(model).__module__.lower()}"
    for keyword, provider in _PROVIDER_KEYWORDS:
        if keyword in combined:
            return provider
    name = type(model).__name__.lower().removeprefix("chat").strip("_-")
    return name or "unknown"


def _infer_model_name(model: Any) -> str:
    """Derive a model name string from the wrapped model's attributes."""
    for attr in ("model_name", "model", "model_id"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _messages_to_query(input: Any) -> str:
    """Convert ``LanguageModelInput`` to a plain string for the pre-check."""
    if isinstance(input, str):
        return input
    if hasattr(input, "to_string"):
        return input.to_string()  # type: ignore[no-any-return]
    # Sequence of BaseMessage
    if isinstance(input, list):
        parts: list[str] = []
        for msg in input:
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, str):
                        parts.append(c)
                    elif isinstance(c, dict) and "text" in c:
                        parts.append(c["text"])
        return " ".join(parts)
    return str(input)


def _extract_token_usage(obj: Any) -> TokenUsage:
    """Extract token usage from a ChatResult, AIMessage, or AIMessageChunk."""
    # ChatResult has a real list in .generations
    msg = obj
    gens = getattr(obj, "generations", None)
    if isinstance(gens, list) and gens:
        msg = gens[0].message

    meta = getattr(msg, "usage_metadata", None)
    if isinstance(meta, dict):
        return TokenUsage(
            prompt_tokens=int(meta.get("input_tokens", meta.get("prompt_tokens", 0))),
            completion_tokens=int(meta.get("output_tokens", meta.get("completion_tokens", 0))),
            total_tokens=int(meta.get("total_tokens", 0)),
        )
    return TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)


def _get_content_str(obj: Any) -> str:
    """Return the text content of a message or ChatResult."""
    if obj is None:
        return ""
    # ChatResult has a real list in .generations
    gens = getattr(obj, "generations", None)
    if isinstance(gens, list) and gens:
        obj = gens[0].message
    content = getattr(obj, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c) for c in content if isinstance(c, str))
    return str(content) if content is not None else ""


# ---------------------------------------------------------------------------
# _GovernanceMixin — shared governance logic
# ---------------------------------------------------------------------------


class _GovernanceMixin:
    """Shared governance helpers for AxonFlowChatModel and AxonFlowRunnableBinding.

    Subclasses must set ``_inner``, ``_axonflow``, ``_user_token``,
    ``_provider``, and ``_model_name`` before calling any mixin method.
    """

    _inner: Any
    _axonflow: AxonFlow
    _user_token: str | None
    _provider: str
    _model_name: str

    def _resolve_user_token(self, config: dict[str, Any] | None) -> str:
        cfg = config or {}
        return cfg.get("configurable", {}).get("user_token") or self._user_token or ""

    async def _pre_check(self, user_token: str, query: str) -> Any:
        result = await self._axonflow.pre_check(
            user_token=user_token,
            query=query,
        )
        if not result.approved:
            raise PolicyViolationError(
                result.block_reason or "Request blocked by AxonFlow policy",
                block_reason=result.block_reason,
            )
        return result

    async def _audit(
        self,
        context_id: str,
        response_summary: str,
        token_usage: TokenUsage,
        latency_ms: int,
    ) -> None:
        try:
            await self._axonflow.audit_llm_call(
                context_id=context_id,
                response_summary=response_summary[:200],
                provider=self._provider,
                model=self._model_name,
                token_usage=token_usage,
                latency_ms=latency_ms,
            )
        except Exception:
            _logger.warning(
                "Failed to record audit for context_id=%s",
                context_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Governed async methods
    # ------------------------------------------------------------------

    async def ainvoke(
        self,
        input: Any,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Invoke with AxonFlow pre-check and audit."""
        user_token = self._resolve_user_token(config)
        query = _messages_to_query(input)

        pre_result = await self._pre_check(user_token, query)

        t0 = time.monotonic()
        result = await self._inner.ainvoke(input, config, **kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)

        await self._audit(
            context_id=pre_result.context_id,
            response_summary=_get_content_str(result),
            token_usage=_extract_token_usage(result),
            latency_ms=latency_ms,
        )
        return result

    async def astream(
        self,
        input: Any,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Stream with AxonFlow pre-check; audit fires after the final chunk."""
        user_token = self._resolve_user_token(config)
        query = _messages_to_query(input)

        pre_result = await self._pre_check(user_token, query)

        t0 = time.monotonic()
        accumulated: Any = None
        async for chunk in self._inner.astream(input, config, **kwargs):
            accumulated = chunk if accumulated is None else accumulated + chunk
            yield chunk

        latency_ms = int((time.monotonic() - t0) * 1000)
        await self._audit(
            context_id=pre_result.context_id,
            response_summary=_get_content_str(accumulated),
            token_usage=_extract_token_usage(accumulated),
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Sync path — no governance (async-only APIs)
    # ------------------------------------------------------------------

    def invoke(self, input: Any, config: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """Invoke synchronously — no AxonFlow governance (async-only)."""
        return self._inner.invoke(input, config, **kwargs)

    def stream(
        self, input: Any, config: dict[str, Any] | None = None, **kwargs: Any
    ) -> Iterator[Any]:
        """Stream synchronously — no AxonFlow governance (async-only)."""
        return self._inner.stream(input, config, **kwargs)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Batch — explicit NotImplementedError to prevent silent bypass
    # ------------------------------------------------------------------

    def batch(self, *args: Any, **kwargs: Any) -> Any:
        msg = "batch() is not supported — use ainvoke() or astream() for governed execution"
        raise NotImplementedError(msg)

    async def abatch(self, *args: Any, **kwargs: Any) -> Any:
        msg = "abatch() is not supported — use ainvoke() or astream() for governed execution"
        raise NotImplementedError(msg)

    # ------------------------------------------------------------------
    # Transparent delegation
    # ------------------------------------------------------------------

    def __or__(self, other: Any) -> Any:
        return self._inner.__or__(other)

    def __ror__(self, other: Any) -> Any:
        return self._inner.__ror__(other)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __getstate__(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)


# ---------------------------------------------------------------------------
# AxonFlowRunnableBinding
# ---------------------------------------------------------------------------


class AxonFlowRunnableBinding(_GovernanceMixin):
    """Governs a ``RunnableBinding`` (returned by ``bind_tools`` /
    ``with_structured_output``) with AxonFlow pre-check and audit.

    This class wraps a LangChain ``RunnableBinding`` and is returned by
    :meth:`AxonFlowChatModel.bind_tools` and
    :meth:`AxonFlowChatModel.with_structured_output`.  It is not meant to be
    instantiated directly by callers.

    Governance fires in ``ainvoke`` and ``astream``.  All other ``Runnable``
    methods delegate transparently to the underlying binding.
    """

    def __init__(
        self,
        *,
        bound: Any,
        axonflow: AxonFlow,
        user_token: str | None = None,
        provider: str = "unknown",
        model_name: str = "unknown",
    ) -> None:
        self._inner = bound
        self._axonflow = axonflow
        self._user_token = user_token
        self._provider = provider
        self._model_name = model_name

    def __repr__(self) -> str:
        return f"AxonFlowRunnableBinding(provider={self._provider!r}, model={self._model_name!r})"


# ---------------------------------------------------------------------------
# AxonFlowChatModel
# ---------------------------------------------------------------------------


class AxonFlowChatModel(_GovernanceMixin):
    """A governed ``BaseChatModel`` wrapper that applies AxonFlow policy
    enforcement transparently for every async LLM call.

    LangChain and LangGraph use duck typing for ``Runnable`` protocol
    methods (``ainvoke``, ``astream``, etc.), so this wrapper works as a
    drop-in replacement without subclassing ``BaseChatModel``.

    Args:
        wrapped: Any ``BaseChatModel`` instance (e.g. ``ChatAnthropic``,
            ``ChatOpenAI``).
        axonflow: An authenticated :class:`axonflow.AxonFlow` client.
        user_token: Optional instance-level default user token.  Overridden
            per-invocation via ``config={"configurable": {"user_token": ...}}``.

    Notes:
        - **Async only**: governance fires in ``ainvoke`` / ``astream``.
          Synchronous ``invoke`` / ``stream`` delegate to the wrapped model
          without any AxonFlow calls.
        - **Serialization**: ``__getstate__`` / ``__setstate__`` are provided
          for safe pickling. LangGraph checkpoint serialisation should work
          but round-tripping requires a live ``AxonFlow`` client on restore.
        - **Fallbacks**: ``with_fallbacks`` wraps each fallback model in
          governance automatically.
    """

    def __init__(
        self,
        *,
        wrapped: Any,
        axonflow: AxonFlow,
        user_token: str | None = None,
    ) -> None:
        from langchain_core.language_models import BaseChatModel  # type: ignore[import-not-found]

        if not isinstance(wrapped, BaseChatModel):
            msg = f"wrapped must be a BaseChatModel instance, got {type(wrapped)}"
            raise TypeError(msg)

        self._inner: Any = wrapped
        self._axonflow = axonflow
        self._user_token = user_token
        self._provider = _infer_provider(wrapped)
        self._model_name = _infer_model_name(wrapped)

    def __repr__(self) -> str:
        return f"AxonFlowChatModel(provider={self._provider!r}, model={self._model_name!r})"

    # ------------------------------------------------------------------
    # Governed ainvoke with docstring override
    # ------------------------------------------------------------------

    async def ainvoke(
        self,
        input: Any,
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Invoke the wrapped model with AxonFlow pre-check and audit.

        Pass ``user_token`` per-invocation via ``config``::

            await model.ainvoke(
                messages,
                config={"configurable": {"user_token": "user-jwt"}},
            )
        """
        return await super().ainvoke(input, config, **kwargs)

    # ------------------------------------------------------------------
    # Tool / structured-output binding — returns AxonFlowRunnableBinding
    # ------------------------------------------------------------------

    def bind_tools(self, tools: Any, **kwargs: Any) -> AxonFlowRunnableBinding:
        """Bind tools and wrap the result in a governed ``AxonFlowRunnableBinding``.

        The returned binding applies pre-check and audit on every async
        invocation, keeping governance outside the tool-calling loop.
        """
        bound = self._inner.bind_tools(tools, **kwargs)
        return AxonFlowRunnableBinding(
            bound=bound,
            axonflow=self._axonflow,
            user_token=self._user_token,
            provider=self._provider,
            model_name=self._model_name,
        )

    def with_structured_output(self, schema: Any, **kwargs: Any) -> AxonFlowRunnableBinding:
        """Return a governed runnable for structured output.

        Wraps the entire runnable returned by the underlying model's
        ``with_structured_output`` (model call + output parser) in an
        ``AxonFlowRunnableBinding`` so that pre-check fires before the model
        is called and audit fires after the parser completes.
        """
        result = self._inner.with_structured_output(schema, **kwargs)
        return AxonFlowRunnableBinding(
            bound=result,
            axonflow=self._axonflow,
            user_token=self._user_token,
            provider=self._provider,
            model_name=self._model_name,
        )

    def with_retry(self, **kwargs: Any) -> AxonFlowRunnableBinding:
        """Wrap the model in a retry loop with governance hoisted outside it.

        Pre-check and audit fire once per user invocation regardless of how
        many retries occur internally.
        """
        retrying = self._inner.with_retry(**kwargs)
        return AxonFlowRunnableBinding(
            bound=retrying,
            axonflow=self._axonflow,
            user_token=self._user_token,
            provider=self._provider,
            model_name=self._model_name,
        )

    def with_fallbacks(self, fallbacks: list[Any], **kwargs: Any) -> Any:
        """Wrap with fallbacks, ensuring each fallback is also governed.

        Unlike the default ``with_fallbacks``, this method wraps each
        fallback model in ``AxonFlowChatModel`` so that governance is
        enforced even when the primary model fails and a fallback is used.
        """
        wrapped_fallbacks = [
            AxonFlowChatModel(
                wrapped=fb,
                axonflow=self._axonflow,
                user_token=self._user_token,
            )
            if not isinstance(fb, AxonFlowChatModel)
            else fb
            for fb in fallbacks
        ]
        return self._inner.with_fallbacks(wrapped_fallbacks, **kwargs)
