"""Unit tests for AxonFlowChatModel and AxonFlowRunnableBinding."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from axonflow.adapters.langchain import (
    AxonFlowChatModel,
    AxonFlowRunnableBinding,
    _extract_token_usage,
    _get_content_str,
    _infer_model_name,
    _infer_provider,
    _messages_to_query,
)
from axonflow.exceptions import PolicyViolationError
from axonflow.types import AuditResult, PolicyApprovalResult, TokenUsage

# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _make_pre_check_result(
    approved: bool = True,
    block_reason: str | None = None,
    context_id: str = "ctx-123",
) -> PolicyApprovalResult:
    return PolicyApprovalResult(
        context_id=context_id,
        approved=approved,
        requires_redaction=False,
        approved_data={},
        policies=[],
        rate_limit_info=None,
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        block_reason=block_reason,
    )


def _make_audit_result() -> AuditResult:
    return AuditResult(success=True, audit_id="audit-456")


def _make_axonflow_client(
    approved: bool = True,
    block_reason: str | None = None,
) -> Any:
    client = MagicMock()
    client.pre_check = AsyncMock(
        return_value=_make_pre_check_result(approved=approved, block_reason=block_reason)
    )
    client.audit_llm_call = AsyncMock(return_value=_make_audit_result())
    return client


def _make_ai_message(content: str = "Hello!", usage: dict | None = None) -> Any:
    msg = MagicMock()
    msg.content = content
    msg.usage_metadata = usage or {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    return msg


def _make_wrapped_model(response: Any = None) -> Any:
    """Return a mock BaseChatModel that responds with *response*."""
    try:
        from langchain_core.language_models import BaseChatModel

        base = BaseChatModel
    except ImportError:
        base = object

    model = MagicMock(spec=base)
    model.__class__.__name__ = "ChatAnthropic"
    model.__class__.__module__ = "langchain_anthropic"
    model.model_name = "claude-sonnet-4-6"

    ai_msg = response or _make_ai_message()
    model.ainvoke = AsyncMock(return_value=ai_msg)
    model.invoke = MagicMock(return_value=ai_msg)

    async def _astream(*args: Any, **kwargs: Any):
        chunk = MagicMock()
        chunk.content = "Hello"
        chunk.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        # Support chunk accumulation via +
        chunk.__add__ = lambda self, other: other
        yield chunk

    model.astream = _astream

    # bind_tools returns a RunnableBinding mock
    try:
        from langchain_core.runnables import RunnableBinding

        binding_cls = RunnableBinding
    except ImportError:
        binding_cls = MagicMock

    binding = MagicMock(spec=binding_cls)
    binding.ainvoke = AsyncMock(return_value=ai_msg)
    binding.astream = _astream

    model.bind_tools = MagicMock(return_value=binding)

    # with_structured_output returns a RunnableSequence-like object
    try:
        from langchain_core.runnables import RunnableBinding, RunnableSequence

        step0 = MagicMock(spec=RunnableBinding)
        step0.ainvoke = AsyncMock(return_value=ai_msg)
        parser = MagicMock()
        seq = MagicMock(spec=RunnableSequence)
        seq.steps = [step0, parser]
        seq.ainvoke = AsyncMock(return_value={"result": "parsed"})
        model.with_structured_output = MagicMock(return_value=seq)
    except ImportError:
        model.with_structured_output = MagicMock(return_value=binding)

    # with_retry returns a Runnable-like object
    retry_binding = MagicMock(spec=binding_cls)
    retry_binding.ainvoke = AsyncMock(return_value=ai_msg)
    model.with_retry = MagicMock(return_value=retry_binding)

    # with_fallbacks returns a RunnableWithFallbacks-like object
    model.with_fallbacks = MagicMock(return_value=MagicMock())

    return model


def _make_chat_model(
    approved: bool = True,
    block_reason: str | None = None,
) -> AxonFlowChatModel:
    try:
        from langchain_core.language_models import BaseChatModel
    except ImportError:
        pytest.skip("langchain-core not installed")

    client = _make_axonflow_client(approved=approved, block_reason=block_reason)
    wrapped = _make_wrapped_model()
    # Bypass the isinstance guard (MagicMock(spec=BaseChatModel) satisfies it)
    model = object.__new__(AxonFlowChatModel)
    model._inner = wrapped
    model._axonflow = client
    model._user_token = None
    model._provider = "anthropic"
    model._model_name = "claude-sonnet-4-6"
    return model


# ---------------------------------------------------------------------------
# Tests: helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_infer_provider_anthropic(self):
        class ChatAnthropic:
            pass

        assert _infer_provider(ChatAnthropic()) == "anthropic"

    def test_infer_provider_openai(self):
        class ChatOpenAI:
            pass

        assert _infer_provider(ChatOpenAI()) == "openai"

    def test_infer_provider_google(self):
        class ChatGoogleGenerativeAI:
            pass

        assert _infer_provider(ChatGoogleGenerativeAI()) == "google"

    def test_infer_provider_unknown(self):
        class SomeChatModel:
            pass

        provider = _infer_provider(SomeChatModel())
        assert isinstance(provider, str)

    def test_infer_model_name_from_model_name_attr(self):
        m = MagicMock()
        m.model_name = "gpt-4o"
        del m.model  # ensure fallback doesn't fire first
        assert _infer_model_name(m) == "gpt-4o"

    def test_infer_model_name_from_model_attr(self):
        m = MagicMock(spec=[])
        m.model = "claude-opus-4-6"
        assert _infer_model_name(m) == "claude-opus-4-6"

    def test_infer_model_name_fallback(self):
        m = MagicMock(spec=[])
        assert _infer_model_name(m) == "unknown"

    def test_messages_to_query_string(self):
        assert _messages_to_query("hello") == "hello"

    def test_messages_to_query_list(self):
        msg = MagicMock()
        msg.content = "What is the weather?"
        assert _messages_to_query([msg]) == "What is the weather?"

    def test_messages_to_query_prompt_value(self):
        pv = MagicMock()
        pv.to_string = MagicMock(return_value="prompt string")
        assert _messages_to_query(pv) == "prompt string"

    def test_messages_to_query_multimodal_content(self):
        msg = MagicMock()
        msg.content = [
            {"type": "text", "text": "What is in this image?"},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ]
        assert _messages_to_query([msg]) == "What is in this image?"

    def test_extract_token_usage_from_message(self):
        # Use spec=[] so MagicMock doesn't auto-create a 'generations' attribute
        msg = MagicMock(spec=[])
        msg.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        usage = _extract_token_usage(msg)
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 5
        assert usage.total_tokens == 15

    def test_extract_token_usage_openai_format(self):
        msg = MagicMock(spec=[])
        msg.usage_metadata = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
        usage = _extract_token_usage(msg)
        assert usage.prompt_tokens == 20
        assert usage.completion_tokens == 8
        assert usage.total_tokens == 28

    def test_extract_token_usage_no_metadata(self):
        msg = MagicMock(spec=[])
        usage = _extract_token_usage(msg)
        assert usage == TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)

    def test_get_content_str_string(self):
        # Use spec=[] so MagicMock doesn't auto-create a 'generations' attribute
        msg = MagicMock(spec=[])
        msg.content = "Hello world"
        assert _get_content_str(msg) == "Hello world"

    def test_get_content_str_none(self):
        assert _get_content_str(None) == ""


# ---------------------------------------------------------------------------
# AxonFlowChatModel
# ---------------------------------------------------------------------------


class TestAxonFlowChatModel:
    @pytest.fixture
    def model(self) -> AxonFlowChatModel:
        return _make_chat_model()

    @pytest.fixture
    def blocked_model(self) -> AxonFlowChatModel:
        return _make_chat_model(approved=False, block_reason="PII detected")

    @pytest.mark.asyncio
    async def test_ainvoke_calls_pre_check(self, model: AxonFlowChatModel):
        await model.ainvoke("What is 2+2?")
        model._axonflow.pre_check.assert_awaited_once()
        call_kwargs = model._axonflow.pre_check.call_args
        assert call_kwargs.kwargs["query"] == "What is 2+2?"

    @pytest.mark.asyncio
    async def test_ainvoke_calls_wrapped_model(self, model: AxonFlowChatModel):
        await model.ainvoke("Hello")
        model._inner.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ainvoke_calls_audit(self, model: AxonFlowChatModel):
        await model.ainvoke("Hello")
        model._axonflow.audit_llm_call.assert_awaited_once()
        call_kwargs = model._axonflow.audit_llm_call.call_args.kwargs
        assert call_kwargs["context_id"] == "ctx-123"
        assert call_kwargs["provider"] == "anthropic"
        assert call_kwargs["model"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_ainvoke_blocked_raises_policy_violation(self, blocked_model: AxonFlowChatModel):
        with pytest.raises(PolicyViolationError, match="PII detected"):
            await blocked_model.ainvoke("Tell me about John's SSN")

    @pytest.mark.asyncio
    async def test_ainvoke_blocked_does_not_call_wrapped(self, blocked_model: AxonFlowChatModel):
        with pytest.raises(PolicyViolationError):
            await blocked_model.ainvoke("Tell me about John's SSN")
        blocked_model._inner.ainvoke.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ainvoke_blocked_does_not_audit(self, blocked_model: AxonFlowChatModel):
        with pytest.raises(PolicyViolationError):
            await blocked_model.ainvoke("Tell me about John's SSN")
        blocked_model._axonflow.audit_llm_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ainvoke_user_token_from_config(self, model: AxonFlowChatModel):
        await model.ainvoke("Hi", config={"configurable": {"user_token": "tok-xyz"}})
        call_kwargs = model._axonflow.pre_check.call_args.kwargs
        assert call_kwargs["user_token"] == "tok-xyz"

    @pytest.mark.asyncio
    async def test_ainvoke_user_token_from_instance(self, model: AxonFlowChatModel):
        model._user_token = "instance-tok"
        await model.ainvoke("Hi")
        call_kwargs = model._axonflow.pre_check.call_args.kwargs
        assert call_kwargs["user_token"] == "instance-tok"

    @pytest.mark.asyncio
    async def test_ainvoke_config_token_overrides_instance(self, model: AxonFlowChatModel):
        model._user_token = "instance-tok"
        await model.ainvoke("Hi", config={"configurable": {"user_token": "config-tok"}})
        call_kwargs = model._axonflow.pre_check.call_args.kwargs
        assert call_kwargs["user_token"] == "config-tok"

    @pytest.mark.asyncio
    async def test_astream_calls_pre_check(self, model: AxonFlowChatModel):
        chunks = []
        async for chunk in model.astream("Stream this"):
            chunks.append(chunk)
        model._axonflow.pre_check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_astream_yields_chunks(self, model: AxonFlowChatModel):
        chunks = []
        async for chunk in model.astream("Stream this"):
            chunks.append(chunk)
        assert len(chunks) > 0

    @pytest.mark.asyncio
    async def test_astream_calls_audit_after_last_chunk(self, model: AxonFlowChatModel):
        async for _ in model.astream("Stream this"):
            pass
        model._axonflow.audit_llm_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_astream_blocked_raises(self, blocked_model: AxonFlowChatModel):
        with pytest.raises(PolicyViolationError):
            async for _ in blocked_model.astream("Sensitive data"):
                pass

    def test_invoke_delegates_without_governance(self, model: AxonFlowChatModel):
        model.invoke("Hello sync")
        model._inner.invoke.assert_called_once()
        model._axonflow.pre_check.assert_not_awaited()

    def test_bind_tools_returns_axonflow_binding(self, model: AxonFlowChatModel):
        tools = [MagicMock()]
        result = model.bind_tools(tools)
        assert isinstance(result, AxonFlowRunnableBinding)

    def test_bind_tools_passes_provider_and_model(self, model: AxonFlowChatModel):
        result = model.bind_tools([])
        assert result._provider == "anthropic"
        assert result._model_name == "claude-sonnet-4-6"

    def test_with_retry_returns_axonflow_binding(self, model: AxonFlowChatModel):
        result = model.with_retry(stop_after_attempt=3)
        assert isinstance(result, AxonFlowRunnableBinding)

    def test_getattr_delegates_to_wrapped(self, model: AxonFlowChatModel):
        model._inner.temperature = 0.7
        assert model.temperature == 0.7

    def test_with_structured_output_returns_governed(self, model: AxonFlowChatModel):
        try:
            from langchain_core.runnables import RunnableSequence  # noqa: F401
        except ImportError:
            pytest.skip("langchain-core not installed")
        result = model.with_structured_output({"type": "object"})
        assert result is not None

    def test_repr(self, model: AxonFlowChatModel):
        assert repr(model) == "AxonFlowChatModel(provider='anthropic', model='claude-sonnet-4-6')"

    def test_batch_raises_not_implemented(self, model: AxonFlowChatModel):
        with pytest.raises(NotImplementedError, match="batch"):
            model.batch(["Hello"])

    @pytest.mark.asyncio
    async def test_abatch_raises_not_implemented(self, model: AxonFlowChatModel):
        with pytest.raises(NotImplementedError, match="abatch"):
            await model.abatch(["Hello"])

    def test_with_fallbacks_returns_governed_binding(self, model: AxonFlowChatModel):
        fallback = _make_wrapped_model()
        result = model.with_fallbacks([fallback])
        # Result should be a governed AxonFlowRunnableBinding wrapping the composed runnable
        assert isinstance(result, AxonFlowRunnableBinding)
        assert result._provider == "anthropic"
        assert result._model_name == "claude-sonnet-4-6"
        # with_fallbacks was called on the inner model
        model._inner.with_fallbacks.assert_called_once()

    @pytest.mark.asyncio
    async def test_audit_failure_logs_warning(self, model: AxonFlowChatModel):
        model._axonflow.audit_llm_call = AsyncMock(side_effect=RuntimeError("audit down"))
        with patch("axonflow.adapters.langchain._logger") as mock_logger:
            result = await model.ainvoke("Hello")
            assert result is not None
            mock_logger.warning.assert_called_once()

    def test_getstate_setstate(self, model: AxonFlowChatModel):
        state = model.__getstate__()
        assert "_inner" in state
        assert "_axonflow" in state
        new_model = object.__new__(AxonFlowChatModel)
        new_model.__setstate__(state)
        assert new_model._provider == "anthropic"


# ---------------------------------------------------------------------------
# AxonFlowRunnableBinding
# ---------------------------------------------------------------------------


class TestAxonFlowRunnableBinding:
    @pytest.fixture
    def binding(self) -> AxonFlowRunnableBinding:
        client = _make_axonflow_client()
        ai_msg = _make_ai_message()
        bound = MagicMock()
        bound.ainvoke = AsyncMock(return_value=ai_msg)

        async def _astream(*args, **kwargs):
            chunk = MagicMock()
            chunk.content = "chunk"
            chunk.usage_metadata = {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}
            chunk.__add__ = lambda self, other: other
            yield chunk

        bound.astream = _astream

        return AxonFlowRunnableBinding(
            bound=bound,
            axonflow=client,
            user_token=None,
            provider="openai",
            model_name="gpt-4o",
        )

    @pytest.mark.asyncio
    async def test_ainvoke_calls_pre_check(self, binding: AxonFlowRunnableBinding):
        await binding.ainvoke("Hello")
        binding._axonflow.pre_check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ainvoke_calls_bound(self, binding: AxonFlowRunnableBinding):
        await binding.ainvoke("Hello")
        binding._inner.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ainvoke_calls_audit(self, binding: AxonFlowRunnableBinding):
        await binding.ainvoke("Hello")
        binding._axonflow.audit_llm_call.assert_awaited_once()
        kwargs = binding._axonflow.audit_llm_call.call_args.kwargs
        assert kwargs["provider"] == "openai"
        assert kwargs["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_ainvoke_blocked_raises(self, binding: AxonFlowRunnableBinding):
        binding._axonflow.pre_check = AsyncMock(
            return_value=_make_pre_check_result(approved=False, block_reason="Blocked")
        )
        with pytest.raises(PolicyViolationError):
            await binding.ainvoke("Hello")

    @pytest.mark.asyncio
    async def test_ainvoke_user_token_from_config(self, binding: AxonFlowRunnableBinding):
        await binding.ainvoke("Hi", config={"configurable": {"user_token": "tok-99"}})
        kwargs = binding._axonflow.pre_check.call_args.kwargs
        assert kwargs["user_token"] == "tok-99"

    @pytest.mark.asyncio
    async def test_astream_pre_check_before_first_chunk(self, binding: AxonFlowRunnableBinding):
        chunks = []
        async for chunk in binding.astream("Stream"):
            chunks.append(chunk)
        binding._axonflow.pre_check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_astream_audit_after_last_chunk(self, binding: AxonFlowRunnableBinding):
        async for _ in binding.astream("Stream"):
            pass
        binding._axonflow.audit_llm_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_propagate(self, binding: AxonFlowRunnableBinding):
        binding._axonflow.audit_llm_call = AsyncMock(side_effect=RuntimeError("audit down"))
        # Should not raise
        result = await binding.ainvoke("Hello")
        assert result is not None

    def test_getattr_delegates_to_bound(self, binding: AxonFlowRunnableBinding):
        binding._inner.some_custom_attr = "value"
        assert binding.some_custom_attr == "value"

    def test_repr(self, binding: AxonFlowRunnableBinding):
        assert repr(binding) == "AxonFlowRunnableBinding(provider='openai', model='gpt-4o')"

    def test_batch_raises_not_implemented(self, binding: AxonFlowRunnableBinding):
        with pytest.raises(NotImplementedError, match="batch"):
            binding.batch(["Hello"])
