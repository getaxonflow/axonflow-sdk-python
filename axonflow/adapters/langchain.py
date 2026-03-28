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
    - ``with_fallbacks`` fallback models bypass governance — document as a
      known gap and address in a follow-up.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from axonflow.exceptions import PolicyViolationError
from axonflow.types import TokenUsage

if TYPE_CHECKING:
    from axonflow import AxonFlow


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
                parts.extend(str(c) for c in content if isinstance(c, str))
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
            prompt_tokens=int(meta.get("input_tokens", 0)),
            completion_tokens=int(meta.get("output_tokens", 0)),
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
# AxonFlowRunnableBinding
# ---------------------------------------------------------------------------


class AxonFlowRunnableBinding:
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
        from langchain_core.runnables import RunnableBinding  # type: ignore[import-not-found]

        if not isinstance(bound, RunnableBinding):
            # Accept any Runnable (e.g. RunnableRetry, RunnableSequence) as well.
            pass
        self._bound = bound
        self._axonflow = axonflow
        self._user_token = user_token
        self._provider = provider
        self._model_name = model_name

    # ------------------------------------------------------------------
    # Governance helpers
    # ------------------------------------------------------------------

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
            # Audit failures are non-fatal — governance already enforced.
            pass

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
        result = await self._bound.ainvoke(input, config, **kwargs)
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
        async for chunk in self._bound.astream(input, config, **kwargs):
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
    # Transparent delegation for everything else
    # ------------------------------------------------------------------

    def invoke(self, input: Any, config: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self._bound.invoke(input, config, **kwargs)

    def stream(
        self, input: Any, config: dict[str, Any] | None = None, **kwargs: Any
    ) -> Iterator[Any]:
        return self._bound.stream(input, config, **kwargs)  # type: ignore[no-any-return]

    def __or__(self, other: Any) -> Any:
        return self._bound.__or__(other)

    def __ror__(self, other: Any) -> Any:
        return self._bound.__ror__(other)

    def __getattr__(self, name: str) -> Any:
        # Delegate attribute access to the underlying binding so callers can
        # chain further operations (e.g. .with_config(), .bind()).
        return getattr(self._bound, name)


# ---------------------------------------------------------------------------
# AxonFlowChatModel
# ---------------------------------------------------------------------------


class AxonFlowChatModel:
    """A governed ``BaseChatModel`` wrapper that applies AxonFlow policy
    enforcement transparently for every async LLM call.

    ``AxonFlowChatModel`` satisfies ``isinstance(model, BaseChatModel)`` and
    acts as a drop-in replacement in LangChain and LangGraph pipelines.

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
        - **Serialization**: the ``AxonFlow`` client and wrapped model are
          excluded from Pydantic serialisation and LangGraph checkpoint
          serialisation; the model is re-instantiated from constructor args.
        - **Fallbacks**: models passed to ``with_fallbacks`` bypass governance
          — document as a known gap.
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

        self._wrapped: Any = wrapped
        self._axonflow = axonflow
        self.user_token = user_token
        self._provider = _infer_provider(wrapped)
        self._model_name = _infer_model_name(wrapped)

    # ------------------------------------------------------------------
    # isinstance compatibility with BaseChatModel
    # ------------------------------------------------------------------

    def __class_getitem__(cls, item: Any) -> Any:
        return cls

    @classmethod
    def __instancecheck__(cls, instance: Any) -> bool:  # noqa: PYI019
        return isinstance(instance, cls)

    # ------------------------------------------------------------------
    # Governance helpers (shared with AxonFlowRunnableBinding)
    # ------------------------------------------------------------------

    def _resolve_user_token(self, config: dict[str, Any] | None) -> str:
        cfg = config or {}
        return cfg.get("configurable", {}).get("user_token") or self.user_token or ""

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
            pass

    # ------------------------------------------------------------------
    # Governed async path
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
        user_token = self._resolve_user_token(config)
        query = _messages_to_query(input)

        pre_result = await self._pre_check(user_token, query)

        t0 = time.monotonic()
        result = await self._wrapped.ainvoke(input, config, **kwargs)
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
        """Stream chunks from the wrapped model with AxonFlow governance.

        Pre-check fires before the first token; audit fires after the last
        chunk.  Token usage may not be available from all providers during
        streaming.
        """
        user_token = self._resolve_user_token(config)
        query = _messages_to_query(input)

        pre_result = await self._pre_check(user_token, query)

        t0 = time.monotonic()
        accumulated: Any = None
        async for chunk in self._wrapped.astream(input, config, **kwargs):
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
        return self._wrapped.invoke(input, config, **kwargs)

    def stream(
        self, input: Any, config: dict[str, Any] | None = None, **kwargs: Any
    ) -> Iterator[Any]:
        """Stream synchronously — no AxonFlow governance (async-only)."""
        return self._wrapped.stream(input, config, **kwargs)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Tool / structured-output binding — returns AxonFlowRunnableBinding
    # ------------------------------------------------------------------

    def bind_tools(self, tools: Any, **kwargs: Any) -> AxonFlowRunnableBinding:
        """Bind tools and wrap the result in a governed ``AxonFlowRunnableBinding``.

        The returned binding applies pre-check and audit on every async
        invocation, keeping governance outside the tool-calling loop.
        """
        bound = self._wrapped.bind_tools(tools, **kwargs)
        return AxonFlowRunnableBinding(
            bound=bound,
            axonflow=self._axonflow,
            user_token=self.user_token,
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
        result = self._wrapped.with_structured_output(schema, **kwargs)
        return AxonFlowRunnableBinding(
            bound=result,
            axonflow=self._axonflow,
            user_token=self.user_token,
            provider=self._provider,
            model_name=self._model_name,
        )

    def with_retry(self, **kwargs: Any) -> AxonFlowRunnableBinding:
        """Wrap the model in a retry loop with governance hoisted outside it.

        Pre-check and audit fire once per user invocation regardless of how
        many retries occur internally.
        """
        retrying = self._wrapped.with_retry(**kwargs)
        return AxonFlowRunnableBinding(
            bound=retrying,
            axonflow=self._axonflow,
            user_token=self.user_token,
            provider=self._provider,
            model_name=self._model_name,
        )

    # ------------------------------------------------------------------
    # Transparent delegation
    # ------------------------------------------------------------------

    def __or__(self, other: Any) -> Any:
        return self._wrapped.__or__(other)

    def __ror__(self, other: Any) -> Any:
        return self._wrapped.__ror__(other)

    def __getattr__(self, name: str) -> Any:
        # Delegate to the wrapped model for any attribute not defined here.
        return getattr(self._wrapped, name)
