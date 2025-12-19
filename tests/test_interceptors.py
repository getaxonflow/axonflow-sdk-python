"""Tests for LLM provider interceptors."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.exceptions import PolicyViolationError
from axonflow.interceptors.anthropic import (
    AnthropicInterceptor,
    wrap_anthropic_client,
)
from axonflow.interceptors.bedrock import BedrockInterceptor, wrap_bedrock_client
from axonflow.interceptors.gemini import GeminiInterceptor, wrap_gemini_model
from axonflow.interceptors.ollama import OllamaInterceptor, wrap_ollama_client
from axonflow.interceptors.openai import OpenAIInterceptor, wrap_openai_client


class TestOpenAIInterceptor:
    """Test OpenAI interceptor."""

    def test_get_provider_name(self) -> None:
        """Test provider name."""
        client = MagicMock()
        axonflow = MagicMock()
        interceptor = OpenAIInterceptor(axonflow)
        assert interceptor.get_provider_name() == "openai"

    def test_extract_prompt_from_messages(self) -> None:
        """Test prompt extraction from messages."""
        axonflow = MagicMock()
        interceptor = OpenAIInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello world"},
            ]
        )

        assert "You are helpful" in prompt
        assert "Hello world" in prompt

    def test_extract_prompt_empty_messages(self) -> None:
        """Test prompt extraction with empty messages."""
        axonflow = MagicMock()
        interceptor = OpenAIInterceptor(axonflow)

        prompt = interceptor.extract_prompt(messages=[])
        assert prompt == ""

    @pytest.mark.asyncio
    async def test_wrap_async_openai_client(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test wrapping async OpenAI client."""
        # Mock AxonFlow response
        httpx_mock.add_response(
            json={
                "success": True,
                "blocked": False,
                "data": None,
            }
        )

        async with AxonFlow(**config_dict) as axonflow:
            # Create mock OpenAI client
            mock_openai = MagicMock()
            mock_openai.chat.completions.create = AsyncMock(
                return_value={"choices": [{"message": {"content": "Hello!"}}]}
            )

            # Wrap it
            wrapped = wrap_openai_client(mock_openai, axonflow, user_token="test")

            # Call it
            result = await wrapped.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": "Hi"}],
            )

            assert result is not None

    @pytest.mark.asyncio
    async def test_openai_blocked_by_policy(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test OpenAI call blocked by policy."""
        httpx_mock.add_response(
            json={
                "success": False,
                "blocked": True,
                "block_reason": "Sensitive content detected",
            }
        )

        async with AxonFlow(**config_dict) as axonflow:
            mock_openai = MagicMock()
            mock_openai.chat.completions.create = AsyncMock()

            wrapped = wrap_openai_client(mock_openai, axonflow)

            with pytest.raises(PolicyViolationError) as exc_info:
                await wrapped.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": "Test"}],
                )

            assert "Sensitive content" in str(exc_info.value)


class TestAnthropicInterceptor:
    """Test Anthropic interceptor."""

    def test_get_provider_name(self) -> None:
        """Test provider name."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow)
        assert interceptor.get_provider_name() == "anthropic"

    def test_extract_prompt_string_content(self) -> None:
        """Test prompt extraction with string content."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[
                {"role": "user", "content": "Hello Claude"},
            ]
        )

        assert "Hello Claude" in prompt

    def test_extract_prompt_block_content(self) -> None:
        """Test prompt extraction with content blocks."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image", "source": {"type": "url", "url": "..."}},
                    ],
                },
            ]
        )

        assert "What is this" in prompt

    @pytest.mark.asyncio
    async def test_wrap_async_anthropic_client(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test wrapping async Anthropic client."""
        httpx_mock.add_response(
            json={
                "success": True,
                "blocked": False,
                "data": None,
            }
        )

        async with AxonFlow(**config_dict) as axonflow:
            mock_anthropic = MagicMock()
            mock_anthropic.messages.create = AsyncMock(
                return_value={"content": [{"type": "text", "text": "Hello!"}]}
            )

            wrapped = wrap_anthropic_client(mock_anthropic, axonflow, user_token="test")

            result = await wrapped.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=1024,
                messages=[{"role": "user", "content": "Hi"}],
            )

            assert result is not None

    @pytest.mark.asyncio
    async def test_anthropic_blocked_by_policy(
        self,
        config_dict: dict[str, Any],
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test Anthropic call blocked by policy."""
        httpx_mock.add_response(
            json={
                "success": False,
                "blocked": True,
                "block_reason": "Rate limit exceeded",
            }
        )

        async with AxonFlow(**config_dict) as axonflow:
            mock_anthropic = MagicMock()
            mock_anthropic.messages.create = AsyncMock()

            wrapped = wrap_anthropic_client(mock_anthropic, axonflow)

            with pytest.raises(PolicyViolationError) as exc_info:
                await wrapped.messages.create(
                    model="claude-3-sonnet",
                    max_tokens=100,
                    messages=[{"role": "user", "content": "Test"}],
                )

            assert "Rate limit" in str(exc_info.value)


class TestInterceptorUserToken:
    """Test user token handling in interceptors."""

    def test_openai_user_token(self) -> None:
        """Test OpenAI interceptor stores user token."""
        axonflow = MagicMock()
        interceptor = OpenAIInterceptor(axonflow, user_token="user-123")
        assert interceptor.user_token == "user-123"

    def test_anthropic_user_token(self) -> None:
        """Test Anthropic interceptor stores user token."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow, user_token="user-456")
        assert interceptor.user_token == "user-456"


class TestGeminiInterceptor:
    """Test Gemini interceptor."""

    def test_get_provider_name(self) -> None:
        """Test provider name."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow)
        assert interceptor.get_provider_name() == "gemini"

    def test_extract_prompt_string(self) -> None:
        """Test prompt extraction from string."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow)
        prompt = interceptor.extract_prompt("Hello Gemini")
        assert prompt == "Hello Gemini"

    def test_extract_prompt_list(self) -> None:
        """Test prompt extraction from list."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow)
        prompt = interceptor.extract_prompt(["Hello", "World"])
        assert "Hello" in prompt
        assert "World" in prompt

    def test_extract_prompt_empty(self) -> None:
        """Test prompt extraction with no args."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow)
        prompt = interceptor.extract_prompt()
        assert prompt == ""

    def test_wrap_method(self) -> None:
        """Test the wrap method."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow, user_token="test")

        mock_gemini = MagicMock()
        mock_gemini.generate_content = MagicMock()

        # Test that wrap returns something
        result = interceptor.wrap(mock_gemini)
        assert result is not None


class TestOllamaInterceptor:
    """Test Ollama interceptor."""

    def test_get_provider_name(self) -> None:
        """Test provider name."""
        axonflow = MagicMock()
        interceptor = OllamaInterceptor(axonflow)
        assert interceptor.get_provider_name() == "ollama"

    def test_extract_prompt_from_messages(self) -> None:
        """Test prompt extraction from chat messages."""
        axonflow = MagicMock()
        interceptor = OllamaInterceptor(axonflow)
        prompt = interceptor.extract_prompt(
            messages=[
                {"role": "user", "content": "Hello Llama"},
                {"role": "assistant", "content": "Hi there"},
            ]
        )
        assert "Hello Llama" in prompt
        assert "Hi there" in prompt

    def test_extract_prompt_from_prompt_kwarg(self) -> None:
        """Test prompt extraction from prompt kwarg."""
        axonflow = MagicMock()
        interceptor = OllamaInterceptor(axonflow)
        prompt = interceptor.extract_prompt(prompt="Generate text")
        assert prompt == "Generate text"

    def test_extract_prompt_empty(self) -> None:
        """Test prompt extraction with no messages."""
        axonflow = MagicMock()
        interceptor = OllamaInterceptor(axonflow)
        prompt = interceptor.extract_prompt()
        assert prompt == ""

    def test_wrap_method(self) -> None:
        """Test the wrap method."""
        axonflow = MagicMock()
        interceptor = OllamaInterceptor(axonflow, user_token="test")

        mock_ollama = MagicMock()
        mock_ollama.chat = MagicMock()
        mock_ollama.generate = MagicMock()

        result = interceptor.wrap(mock_ollama)
        assert result is not None


class TestBedrockInterceptor:
    """Test Bedrock interceptor."""

    def test_get_provider_name(self) -> None:
        """Test provider name."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)
        assert interceptor.get_provider_name() == "bedrock"

    def test_extract_prompt_from_claude_body(self) -> None:
        """Test prompt extraction from Claude request body."""
        import json

        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = json.dumps({
            "messages": [
                {"role": "user", "content": "Hello Claude on Bedrock"},
            ]
        })

        prompt = interceptor.extract_prompt(body=body)
        assert "Hello Claude on Bedrock" in prompt

    def test_extract_prompt_from_titan_body(self) -> None:
        """Test prompt extraction from Titan request body."""
        import json

        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = json.dumps({
            "inputText": "Hello Titan",
        })

        prompt = interceptor.extract_prompt(body=body)
        assert "Hello Titan" in prompt

    def test_extract_prompt_from_bytes(self) -> None:
        """Test prompt extraction from bytes body."""
        import json

        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = json.dumps({"prompt": "Hello"}).encode("utf-8")
        prompt = interceptor.extract_prompt(body=body)
        assert "Hello" in prompt

    def test_extract_prompt_empty(self) -> None:
        """Test prompt extraction with no body."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)
        prompt = interceptor.extract_prompt()
        assert prompt == ""

    def test_wrap_method(self) -> None:
        """Test the wrap method."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow, user_token="test")

        mock_bedrock = MagicMock()
        mock_bedrock.invoke_model = MagicMock()
        mock_bedrock.invoke_model_with_response_stream = MagicMock()

        result = interceptor.wrap(mock_bedrock)
        assert result is not None


class TestNewInterceptorUserTokens:
    """Test user token handling in new interceptors."""

    def test_gemini_user_token(self) -> None:
        """Test Gemini interceptor stores user token."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow, user_token="gemini-user")
        assert interceptor.user_token == "gemini-user"

    def test_ollama_user_token(self) -> None:
        """Test Ollama interceptor stores user token."""
        axonflow = MagicMock()
        interceptor = OllamaInterceptor(axonflow, user_token="ollama-user")
        assert interceptor.user_token == "ollama-user"

    def test_bedrock_user_token(self) -> None:
        """Test Bedrock interceptor stores user token."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow, user_token="bedrock-user")
        assert interceptor.user_token == "bedrock-user"
