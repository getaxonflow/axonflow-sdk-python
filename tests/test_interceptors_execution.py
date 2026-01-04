"""Tests for LLM provider interceptor execution paths.

These tests cover the actual execution of wrapped interceptor methods,
focusing on the sync/async call paths and governance enforcement.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.exceptions import PolicyViolationError
from axonflow.interceptors.bedrock import (
    BedrockModels,
    create_bedrock_wrapper,
    wrap_bedrock_client,
)
from axonflow.interceptors.gemini import (
    _wrap_chat_session,
    create_gemini_wrapper,
    wrap_gemini_model,
)
from axonflow.interceptors.ollama import (
    create_ollama_wrapper,
    wrap_ollama_client,
    wrap_ollama_client_async,
)
from axonflow.interceptors.openai import (
    create_openai_wrapper,
    wrap_openai_client,
)


class TestBedrockWrappedExecution:
    """Test Bedrock interceptor wrapped method execution."""

    def test_bedrock_invoke_model_allowed(self) -> None:
        """Test Bedrock invoke_model allowed by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock(return_value={"body": b"response"})
        mock_bedrock.invoke_model_with_response_stream = MagicMock()

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow, user_token="test-user")

        body = json.dumps({"messages": [{"role": "user", "content": "Hello"}]})
        result = wrapped.invoke_model(modelId="anthropic.claude-3-sonnet", body=body)

        assert result is not None
        assert result == {"body": b"response"}

    def test_bedrock_invoke_model_blocked(self) -> None:
        """Test Bedrock invoke_model blocked by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="PII detected")
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock()

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)

        body = json.dumps({"messages": [{"content": "My SSN is 123-45-6789"}]})
        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.invoke_model(modelId="anthropic.claude-3-sonnet", body=body)

        assert "PII detected" in str(exc_info.value)

    def test_bedrock_stream_allowed(self) -> None:
        """Test Bedrock invoke_model_with_response_stream allowed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock()
        mock_bedrock.invoke_model_with_response_stream = MagicMock(
            return_value={"body": iter([b"chunk1", b"chunk2"])}
        )

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow, user_token="user")

        body = json.dumps({"inputText": "Generate a story"})
        result = wrapped.invoke_model_with_response_stream(
            modelId="amazon.titan-text-express-v1", body=body
        )

        assert result is not None
        assert "body" in result

    def test_bedrock_stream_blocked(self) -> None:
        """Test Bedrock streaming blocked by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Rate limit")
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model_with_response_stream = MagicMock()

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)

        body = json.dumps({"prompt": "Hello"})
        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.invoke_model_with_response_stream(modelId="model", body=body)

        assert "Rate limit" in str(exc_info.value)

    def test_bedrock_invoke_model_default_block_reason(self) -> None:
        """Test Bedrock invoke_model with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock()

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)

        body = json.dumps({"prompt": "Test"})
        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.invoke_model(modelId="model", body=body)

        assert "blocked by policy" in str(exc_info.value)

    def test_bedrock_stream_default_block_reason(self) -> None:
        """Test Bedrock stream with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model_with_response_stream = MagicMock()

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)

        body = b'{"prompt": "Test"}'
        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.invoke_model_with_response_stream(modelId="model", body=body)

        assert "blocked by policy" in str(exc_info.value)

    def test_bedrock_extract_prompt_from_bytes(self) -> None:
        """Test prompt extraction from bytes in wrapped client."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock(return_value={"body": b"response"})

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)

        # Use bytes body
        body = b'{"messages": [{"content": "Hello bytes"}]}'
        result = wrapped.invoke_model(modelId="model", body=body)
        assert result is not None

    def test_bedrock_client_no_invoke_model(self) -> None:
        """Test wrapping client without invoke_model method."""
        mock_axonflow = MagicMock()
        mock_bedrock = MagicMock(spec=[])  # No methods

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)
        # Should not fail, just won't wrap anything
        assert wrapped is mock_bedrock


class TestGeminiWrappedExecution:
    """Test Gemini interceptor wrapped method execution."""

    def test_gemini_generate_content_allowed(self) -> None:
        """Test Gemini generate_content allowed by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock(return_value={"text": "response"})

        wrapped = wrap_gemini_model(
            mock_gemini, mock_axonflow, user_token="user", model_name="gemini-pro"
        )

        result = wrapped.generate_content("Hello Gemini")
        assert result is not None

    def test_gemini_generate_content_blocked(self) -> None:
        """Test Gemini generate_content blocked by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Content policy")
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.generate_content("Generate harmful content")

        assert "Content policy" in str(exc_info.value)

    def test_gemini_generate_content_blocked_default_reason(self) -> None:
        """Test Gemini generate_content blocked with no reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.generate_content("Test")

        assert "blocked by policy" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_gemini_generate_content_async_allowed(self) -> None:
        """Test Gemini async generate_content allowed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()
        mock_gemini.generate_content_async = AsyncMock(return_value={"text": "async response"})

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow, model_name="gemini-1.5-pro")

        result = await wrapped.generate_content_async("Hello async")
        assert result is not None

    @pytest.mark.asyncio
    async def test_gemini_generate_content_async_blocked(self) -> None:
        """Test Gemini async generate_content blocked."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Async blocked")
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()
        mock_gemini.generate_content_async = AsyncMock()

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            await wrapped.generate_content_async("Test async")

        assert "Async blocked" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_gemini_generate_content_async_default_block_reason(self) -> None:
        """Test Gemini async with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()
        mock_gemini.generate_content_async = AsyncMock()

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            await wrapped.generate_content_async("Test")

        assert "blocked by policy" in str(exc_info.value)

    def test_gemini_start_chat_send_message_allowed(self) -> None:
        """Test Gemini chat session send_message allowed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_chat = MagicMock()
        mock_chat.send_message = MagicMock(return_value={"text": "chat response"})

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()
        mock_gemini.start_chat = MagicMock(return_value=mock_chat)

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow, user_token="chat-user")
        chat = wrapped.start_chat()

        result = chat.send_message("Hello chat")
        assert result is not None

    def test_gemini_chat_send_message_blocked(self) -> None:
        """Test Gemini chat session send_message blocked."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Chat blocked")
        )

        mock_chat = MagicMock()
        mock_chat.send_message = MagicMock()

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()
        mock_gemini.start_chat = MagicMock(return_value=mock_chat)

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)
        chat = wrapped.start_chat()

        with pytest.raises(PolicyViolationError) as exc_info:
            chat.send_message("Blocked message")

        assert "Chat blocked" in str(exc_info.value)

    def test_gemini_chat_send_message_default_block_reason(self) -> None:
        """Test Gemini chat with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_chat = MagicMock()
        mock_chat.send_message = MagicMock()

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()
        mock_gemini.start_chat = MagicMock(return_value=mock_chat)

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)
        chat = wrapped.start_chat()

        with pytest.raises(PolicyViolationError) as exc_info:
            chat.send_message("Test")

        assert "blocked by policy" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_gemini_chat_send_message_async_allowed(self) -> None:
        """Test Gemini chat async send_message allowed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_chat = MagicMock()
        mock_chat.send_message = MagicMock()
        mock_chat.send_message_async = AsyncMock(return_value={"text": "async chat"})

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()
        mock_gemini.start_chat = MagicMock(return_value=mock_chat)

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow, model_name="gemini-pro")
        chat = wrapped.start_chat()

        result = await chat.send_message_async("Hello async chat")
        assert result is not None

    @pytest.mark.asyncio
    async def test_gemini_chat_send_message_async_blocked(self) -> None:
        """Test Gemini chat async send_message blocked."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Async chat blocked")
        )

        mock_chat = MagicMock()
        mock_chat.send_message = MagicMock()
        mock_chat.send_message_async = AsyncMock()

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()
        mock_gemini.start_chat = MagicMock(return_value=mock_chat)

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)
        chat = wrapped.start_chat()

        with pytest.raises(PolicyViolationError) as exc_info:
            await chat.send_message_async("Blocked async")

        assert "Async chat blocked" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_gemini_chat_async_default_block_reason(self) -> None:
        """Test Gemini async chat with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_chat = MagicMock()
        mock_chat.send_message = MagicMock()
        mock_chat.send_message_async = AsyncMock()

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()
        mock_gemini.start_chat = MagicMock(return_value=mock_chat)

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)
        chat = wrapped.start_chat()

        with pytest.raises(PolicyViolationError) as exc_info:
            await chat.send_message_async("Test")

        assert "blocked by policy" in str(exc_info.value)

    def test_gemini_wrap_chat_session_no_send_message(self) -> None:
        """Test wrapping chat session without send_message method."""
        mock_axonflow = MagicMock()
        mock_chat = MagicMock(spec=[])  # No methods

        result = _wrap_chat_session(mock_chat, mock_axonflow, "user", "model")
        # Should return as-is if no send_message
        assert result is mock_chat

    def test_gemini_extract_prompt_from_kwargs_contents(self) -> None:
        """Test Gemini prompt extraction from contents kwarg."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock(return_value={"text": "response"})

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)

        # Call with contents in kwargs instead of positional
        result = wrapped.generate_content(contents="Hello from contents")
        assert result is not None

    def test_gemini_extract_prompt_from_list_with_content_objects(self) -> None:
        """Test Gemini prompt extraction from list with Content objects."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock(return_value={"text": "response"})

        # Create mock Content object with parts
        mock_part = MagicMock()
        mock_part.text = "Content from parts"
        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)
        result = wrapped.generate_content([mock_content])
        assert result is not None

    def test_gemini_chat_send_message_non_string_content(self) -> None:
        """Test chat session with non-string content."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_chat = MagicMock()
        mock_chat.send_message = MagicMock(return_value={"text": "response"})

        wrapped_chat = _wrap_chat_session(mock_chat, mock_axonflow, "user", "model")

        # Pass a non-string content (will be str() converted)
        result = wrapped_chat.send_message(123)
        assert result is not None


class TestOllamaWrappedExecution:
    """Test Ollama interceptor wrapped method execution."""

    def test_ollama_chat_allowed(self) -> None:
        """Test Ollama chat allowed by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.chat = MagicMock(return_value={"message": {"content": "Hi"}})

        wrapped = wrap_ollama_client(mock_ollama, mock_axonflow, user_token="user")

        result = wrapped.chat(model="llama2", messages=[{"role": "user", "content": "Hello"}])
        assert result is not None

    def test_ollama_chat_blocked(self) -> None:
        """Test Ollama chat blocked by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Ollama blocked")
        )

        mock_ollama = MagicMock()
        mock_ollama.chat = MagicMock()

        wrapped = wrap_ollama_client(mock_ollama, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.chat(model="llama2", messages=[{"content": "Bad content"}])

        assert "Ollama blocked" in str(exc_info.value)

    def test_ollama_chat_default_block_reason(self) -> None:
        """Test Ollama chat with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.chat = MagicMock()

        wrapped = wrap_ollama_client(mock_ollama, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.chat(model="llama2", messages=[{"content": "Test"}])

        assert "blocked by policy" in str(exc_info.value)

    def test_ollama_generate_allowed(self) -> None:
        """Test Ollama generate allowed by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.generate = MagicMock(return_value={"response": "Generated text"})

        wrapped = wrap_ollama_client(mock_ollama, mock_axonflow, user_token="gen-user")

        result = wrapped.generate(model="llama2", prompt="Generate something")
        assert result is not None

    def test_ollama_generate_blocked(self) -> None:
        """Test Ollama generate blocked by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Generate blocked")
        )

        mock_ollama = MagicMock()
        mock_ollama.generate = MagicMock()

        wrapped = wrap_ollama_client(mock_ollama, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.generate(model="llama2", prompt="Bad prompt")

        assert "Generate blocked" in str(exc_info.value)

    def test_ollama_generate_default_block_reason(self) -> None:
        """Test Ollama generate with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.generate = MagicMock()

        wrapped = wrap_ollama_client(mock_ollama, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.generate(model="llama2", prompt="Test")

        assert "blocked by policy" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_ollama_async_chat_allowed(self) -> None:
        """Test Ollama async chat allowed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.chat = AsyncMock(return_value={"message": {"content": "async"}})

        wrapped = await wrap_ollama_client_async(mock_ollama, mock_axonflow, user_token="user")

        result = await wrapped.chat(
            model="llama2", messages=[{"role": "user", "content": "Hello async"}]
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_ollama_async_chat_blocked(self) -> None:
        """Test Ollama async chat blocked."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Async chat blocked")
        )

        mock_ollama = MagicMock()
        mock_ollama.chat = AsyncMock()

        wrapped = await wrap_ollama_client_async(mock_ollama, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            await wrapped.chat(model="llama2", messages=[{"content": "Bad"}])

        assert "Async chat blocked" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_ollama_async_chat_default_block_reason(self) -> None:
        """Test Ollama async chat with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.chat = AsyncMock()

        wrapped = await wrap_ollama_client_async(mock_ollama, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            await wrapped.chat(model="llama2", messages=[{"content": "Test"}])

        assert "blocked by policy" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_ollama_async_generate_allowed(self) -> None:
        """Test Ollama async generate allowed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.generate = AsyncMock(return_value={"response": "async generated"})

        wrapped = await wrap_ollama_client_async(mock_ollama, mock_axonflow, user_token="user")

        result = await wrapped.generate(model="llama2", prompt="Generate async")
        assert result is not None

    @pytest.mark.asyncio
    async def test_ollama_async_generate_blocked(self) -> None:
        """Test Ollama async generate blocked."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Async gen blocked")
        )

        mock_ollama = MagicMock()
        mock_ollama.generate = AsyncMock()

        wrapped = await wrap_ollama_client_async(mock_ollama, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            await wrapped.generate(model="llama2", prompt="Bad async")

        assert "Async gen blocked" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_ollama_async_generate_default_block_reason(self) -> None:
        """Test Ollama async generate with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.generate = AsyncMock()

        wrapped = await wrap_ollama_client_async(mock_ollama, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            await wrapped.generate(model="llama2", prompt="Test")

        assert "blocked by policy" in str(exc_info.value)

    def test_ollama_client_no_chat_method(self) -> None:
        """Test wrapping client without chat method."""
        mock_axonflow = MagicMock()
        mock_ollama = MagicMock(spec=[])

        wrapped = wrap_ollama_client(mock_ollama, mock_axonflow)
        assert wrapped is mock_ollama


class TestOpenAIWrappedExecution:
    """Test OpenAI interceptor wrapped method execution."""

    def test_openai_sync_create_allowed(self) -> None:
        """Test OpenAI sync create allowed by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(
            return_value={"choices": [{"message": {"content": "Hi"}}]}
        )

        wrapped = wrap_openai_client(mock_openai, mock_axonflow, user_token="user")

        result = wrapped.chat.completions.create(
            model="gpt-4", messages=[{"role": "user", "content": "Hello"}]
        )
        assert result is not None

    def test_openai_sync_create_blocked(self) -> None:
        """Test OpenAI sync create blocked by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="OpenAI blocked")
        )

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock()

        wrapped = wrap_openai_client(mock_openai, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.chat.completions.create(
                model="gpt-4", messages=[{"role": "user", "content": "Bad"}]
            )

        assert "OpenAI blocked" in str(exc_info.value)

    def test_openai_sync_create_default_block_reason(self) -> None:
        """Test OpenAI sync create with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock()

        wrapped = wrap_openai_client(mock_openai, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            wrapped.chat.completions.create(
                model="gpt-4", messages=[{"role": "user", "content": "Test"}]
            )

        assert "blocked by policy" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_openai_async_create_allowed(self) -> None:
        """Test OpenAI async create allowed by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(
            return_value={"choices": [{"message": {"content": "Hi async"}}]}
        )

        wrapped = wrap_openai_client(mock_openai, mock_axonflow, user_token="user")

        result = await wrapped.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "Hello async"}]
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_openai_async_create_blocked(self) -> None:
        """Test OpenAI async create blocked by policy."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason="Async blocked")
        )

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock()

        wrapped = wrap_openai_client(mock_openai, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            await wrapped.chat.completions.create(
                model="gpt-4", messages=[{"role": "user", "content": "Bad async"}]
            )

        assert "Async blocked" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_openai_async_create_default_block_reason(self) -> None:
        """Test OpenAI async create with no block_reason provided."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=True, block_reason=None)
        )

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = AsyncMock()

        wrapped = wrap_openai_client(mock_openai, mock_axonflow)

        with pytest.raises(PolicyViolationError) as exc_info:
            await wrapped.chat.completions.create(
                model="gpt-4", messages=[{"role": "user", "content": "Test"}]
            )

        assert "blocked by policy" in str(exc_info.value)


class TestEventLoopHandling:
    """Test event loop handling in sync interceptors."""

    def test_bedrock_creates_event_loop_if_needed(self) -> None:
        """Test Bedrock creates event loop when needed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock(return_value={"body": b"response"})

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)

        # This should work even if there's no event loop
        body = json.dumps({"prompt": "Test"})
        result = wrapped.invoke_model(modelId="model", body=body)
        assert result is not None

    def test_ollama_creates_event_loop_if_needed(self) -> None:
        """Test Ollama creates event loop when needed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.chat = MagicMock(return_value={"message": {"content": "Hi"}})

        wrapped = wrap_ollama_client(mock_ollama, mock_axonflow)

        result = wrapped.chat(model="llama2", messages=[{"content": "Hi"}])
        assert result is not None

    def test_gemini_creates_event_loop_if_needed(self) -> None:
        """Test Gemini creates event loop when needed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock(return_value={"text": "response"})

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)

        result = wrapped.generate_content("Test")
        assert result is not None

    def test_openai_creates_event_loop_if_needed(self) -> None:
        """Test OpenAI creates event loop when needed."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(return_value={"choices": []})

        wrapped = wrap_openai_client(mock_openai, mock_axonflow)

        result = wrapped.chat.completions.create(model="gpt-4", messages=[{"content": "Hi"}])
        assert result is not None


class TestBedrockModelsConstants:
    """Test Bedrock model constants coverage."""

    def test_all_model_constants_defined(self) -> None:
        """Test all Bedrock model constants are defined."""
        assert BedrockModels.CLAUDE_3_OPUS == "anthropic.claude-3-opus-20240229-v1:0"
        assert BedrockModels.CLAUDE_3_SONNET == "anthropic.claude-3-sonnet-20240229-v1:0"
        assert BedrockModels.CLAUDE_3_HAIKU == "anthropic.claude-3-haiku-20240307-v1:0"
        assert BedrockModels.CLAUDE_2_1 == "anthropic.claude-v2:1"
        assert BedrockModels.CLAUDE_2 == "anthropic.claude-v2"
        assert BedrockModels.CLAUDE_INSTANT == "anthropic.claude-instant-v1"
        assert BedrockModels.TITAN_TEXT_EXPRESS == "amazon.titan-text-express-v1"
        assert BedrockModels.TITAN_TEXT_LITE == "amazon.titan-text-lite-v1"
        assert BedrockModels.TITAN_TEXT_PREMIER == "amazon.titan-text-premier-v1:0"
        assert BedrockModels.LLAMA2_13B == "meta.llama2-13b-chat-v1"
        assert BedrockModels.LLAMA2_70B == "meta.llama2-70b-chat-v1"
        assert BedrockModels.LLAMA3_8B == "meta.llama3-8b-instruct-v1:0"
        assert BedrockModels.LLAMA3_70B == "meta.llama3-70b-instruct-v1:0"


class TestPromptExtractionEdgeCases:
    """Test edge cases in prompt extraction during wrapped calls."""

    def test_bedrock_extract_titan_format(self) -> None:
        """Test Bedrock extracts prompt from Titan format."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock(return_value={"body": b"response"})

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)

        body = json.dumps({"inputText": "Titan format prompt"})
        result = wrapped.invoke_model(modelId="amazon.titan-text", body=body)
        assert result is not None

    def test_bedrock_extract_generic_prompt_format(self) -> None:
        """Test Bedrock extracts prompt from generic format."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock(return_value={"body": b"response"})

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)

        body = json.dumps({"prompt": "Generic prompt format"})
        result = wrapped.invoke_model(modelId="meta.llama2", body=body)
        assert result is not None

    def test_bedrock_invalid_json_body(self) -> None:
        """Test Bedrock handles invalid JSON body gracefully."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock(return_value={"body": b"response"})

        wrapped = wrap_bedrock_client(mock_bedrock, mock_axonflow)

        # Invalid JSON - should still work, just with empty prompt
        result = wrapped.invoke_model(modelId="model", body="not-json")
        assert result is not None

    def test_gemini_extract_from_prompt_kwarg(self) -> None:
        """Test Gemini extracts from prompt kwarg."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock(return_value={"text": "response"})

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)

        # Call with prompt kwarg
        result = wrapped.generate_content(prompt="Hello from prompt kwarg")
        assert result is not None


class TestGeminiChatSessionEdgeCases:
    """Test edge cases for Gemini chat session wrapping."""

    def test_wrap_chat_session_without_async(self) -> None:
        """Test wrapping chat session without async send_message."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_chat = MagicMock()
        mock_chat.send_message = MagicMock(return_value={"text": "response"})
        # No send_message_async attribute

        del mock_chat.send_message_async

        wrapped = _wrap_chat_session(mock_chat, mock_axonflow, "user", "model")

        result = wrapped.send_message("Hello")
        assert result is not None

    def test_gemini_model_without_async_generate(self) -> None:
        """Test wrapping Gemini model without async generate_content."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock(return_value={"text": "response"})
        # No generate_content_async
        del mock_gemini.generate_content_async

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)

        result = wrapped.generate_content("Hello")
        assert result is not None

    def test_gemini_model_without_start_chat(self) -> None:
        """Test wrapping Gemini model without start_chat method."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock(return_value={"text": "response"})
        # No start_chat
        del mock_gemini.start_chat

        wrapped = wrap_gemini_model(mock_gemini, mock_axonflow)

        result = wrapped.generate_content("Hello")
        assert result is not None


class TestOllamaAsyncEdgeCases:
    """Test edge cases for async Ollama wrapping."""

    @pytest.mark.asyncio
    async def test_ollama_async_without_chat_method(self) -> None:
        """Test async wrapping client without chat method."""
        mock_axonflow = MagicMock()
        mock_ollama = MagicMock(spec=[])  # No methods

        wrapped = await wrap_ollama_client_async(mock_ollama, mock_axonflow)
        assert wrapped is mock_ollama

    @pytest.mark.asyncio
    async def test_ollama_async_without_generate_method(self) -> None:
        """Test async wrapping client without generate method."""
        mock_axonflow = MagicMock()
        mock_axonflow.execute_query = AsyncMock(
            return_value=MagicMock(blocked=False, block_reason=None)
        )

        mock_ollama = MagicMock()
        mock_ollama.chat = AsyncMock(return_value={"message": {"content": "Hi"}})
        # Remove generate method
        del mock_ollama.generate

        wrapped = await wrap_ollama_client_async(mock_ollama, mock_axonflow)

        result = await wrapped.chat(model="llama2", messages=[{"content": "Hi"}])
        assert result is not None
