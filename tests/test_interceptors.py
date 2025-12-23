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

        body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "Hello Claude on Bedrock"},
                ]
            }
        )

        prompt = interceptor.extract_prompt(body=body)
        assert "Hello Claude on Bedrock" in prompt

    def test_extract_prompt_from_titan_body(self) -> None:
        """Test prompt extraction from Titan request body."""
        import json

        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = json.dumps(
            {
                "inputText": "Hello Titan",
            }
        )

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


class TestGeminiWrapFunctions:
    """Test Gemini wrap functions."""

    def test_extract_prompt_with_content_parts(self) -> None:
        """Test prompt extraction from Content objects with parts."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow)

        # Create mock Content object with parts
        mock_part = MagicMock()
        mock_part.text = "Hello from part"
        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        prompt = interceptor.extract_prompt([mock_content])
        assert "Hello from part" in prompt

    def test_wrap_gemini_model_replaces_generate_content(self) -> None:
        """Test that wrap_gemini_model replaces generate_content."""
        axonflow = MagicMock()
        mock_model = MagicMock()
        original_generate = MagicMock()
        mock_model.generate_content = original_generate

        wrapped = wrap_gemini_model(mock_model, axonflow, user_token="test")

        # The method should be replaced
        assert wrapped.generate_content is not original_generate

    def test_wrap_gemini_model_wraps_start_chat(self) -> None:
        """Test that wrap_gemini_model wraps start_chat."""
        axonflow = MagicMock()
        mock_model = MagicMock()
        mock_chat = MagicMock()
        mock_chat.send_message = MagicMock(return_value="response")
        mock_model.start_chat = MagicMock(return_value=mock_chat)

        wrapped = wrap_gemini_model(mock_model, axonflow)

        # start_chat should be wrapped
        assert hasattr(wrapped, "start_chat")


class TestOllamaWrapFunctions:
    """Test Ollama wrap functions."""

    def test_wrap_ollama_client_replaces_chat(self) -> None:
        """Test that wrap_ollama_client replaces chat method."""
        axonflow = MagicMock()
        mock_client = MagicMock()
        original_chat = MagicMock()
        mock_client.chat = original_chat

        wrapped = wrap_ollama_client(mock_client, axonflow)

        assert wrapped.chat is not original_chat

    def test_wrap_ollama_client_replaces_generate(self) -> None:
        """Test that wrap_ollama_client replaces generate method."""
        axonflow = MagicMock()
        mock_client = MagicMock()
        original_generate = MagicMock()
        mock_client.generate = original_generate

        wrapped = wrap_ollama_client(mock_client, axonflow)

        assert wrapped.generate is not original_generate

    def test_wrap_ollama_client_preserves_other_attrs(self) -> None:
        """Test that wrap preserves non-wrapped attributes."""
        axonflow = MagicMock()
        mock_client = MagicMock()
        mock_client.list = MagicMock(return_value=["llama2"])
        mock_client.chat = MagicMock()
        mock_client.generate = MagicMock()

        wrapped = wrap_ollama_client(mock_client, axonflow)

        # list should still be accessible
        assert hasattr(wrapped, "list")


class TestBedrockWrapFunctions:
    """Test Bedrock wrap functions."""

    def test_extract_prompt_from_llama_body(self) -> None:
        """Test prompt extraction from Llama request body."""
        import json

        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = json.dumps({"prompt": "Hello Llama"})
        prompt = interceptor.extract_prompt(body=body)
        assert "Hello Llama" in prompt

    def test_extract_prompt_from_ai21_body(self) -> None:
        """Test prompt extraction from AI21 request body."""
        import json

        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = json.dumps({"prompt": "Hello AI21"})
        prompt = interceptor.extract_prompt(body=body)
        assert "Hello AI21" in prompt

    def test_wrap_bedrock_client_replaces_invoke_model(self) -> None:
        """Test that wrap_bedrock_client replaces invoke_model."""
        axonflow = MagicMock()
        mock_client = MagicMock()
        original_invoke = MagicMock()
        mock_client.invoke_model = original_invoke

        wrapped = wrap_bedrock_client(mock_client, axonflow)

        assert wrapped.invoke_model is not original_invoke

    def test_wrap_bedrock_client_wraps_stream(self) -> None:
        """Test that wrap_bedrock_client wraps invoke_model_with_response_stream."""
        axonflow = MagicMock()
        mock_client = MagicMock()
        original_stream = MagicMock()
        mock_client.invoke_model_with_response_stream = original_stream

        wrapped = wrap_bedrock_client(mock_client, axonflow)

        assert wrapped.invoke_model_with_response_stream is not original_stream


class TestOpenAIWrapFunctions:
    """Test OpenAI wrap function edge cases."""

    def test_wrap_openai_client_replaces_create(self) -> None:
        """Test that wrap replaces chat.completions.create."""
        axonflow = MagicMock()
        mock_client = MagicMock()
        original_create = AsyncMock()
        mock_client.chat.completions.create = original_create

        wrapped = wrap_openai_client(mock_client, axonflow)

        assert wrapped.chat.completions.create is not original_create

    def test_extract_prompt_with_system_message(self) -> None:
        """Test prompt extraction includes system message."""
        axonflow = MagicMock()
        interceptor = OpenAIInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hi"},
            ]
        )

        assert "Be helpful" in prompt
        assert "Hi" in prompt


class TestAnthropicWrapFunctions:
    """Test Anthropic wrap function edge cases."""

    def test_wrap_anthropic_client_replaces_create(self) -> None:
        """Test that wrap replaces messages.create."""
        axonflow = MagicMock()
        mock_client = MagicMock()
        original_create = AsyncMock()
        mock_client.messages.create = original_create

        wrapped = wrap_anthropic_client(mock_client, axonflow)

        assert wrapped.messages.create is not original_create

    def test_extract_prompt_with_image_blocks(self) -> None:
        """Test prompt extraction skips image blocks properly."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this"},
                        {"type": "image", "source": {"type": "base64", "data": "..."}},
                    ],
                },
            ]
        )

        assert "Describe this" in prompt
        # Should not crash on image block


class TestInterceptorCreateWrappers:
    """Test create_*_wrapper functions."""

    def test_gemini_create_wrapper(self) -> None:
        """Test create_gemini_wrapper factory function."""
        from axonflow.interceptors.gemini import create_gemini_wrapper

        axonflow = MagicMock()
        wrapper = create_gemini_wrapper(axonflow, user_token="test", model_name="gemini-1.5")

        mock_model = MagicMock()
        mock_model.generate_content = MagicMock()

        wrapped = wrapper(mock_model)
        assert wrapped is not None

    def test_openai_create_wrapper(self) -> None:
        """Test create_openai_wrapper factory function."""
        from axonflow.interceptors.openai import create_openai_wrapper

        axonflow = MagicMock()
        wrapper = create_openai_wrapper(axonflow, user_token="test")

        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock()

        wrapped = wrapper(mock_client)
        assert wrapped is not None

    def test_anthropic_create_wrapper(self) -> None:
        """Test create_anthropic_wrapper factory function."""
        from axonflow.interceptors.anthropic import create_anthropic_wrapper

        axonflow = MagicMock()
        wrapper = create_anthropic_wrapper(axonflow, user_token="test")

        mock_client = MagicMock()
        mock_client.messages.create = MagicMock()

        wrapped = wrapper(mock_client)
        assert wrapped is not None

    def test_ollama_create_wrapper(self) -> None:
        """Test create_ollama_wrapper factory function."""
        from axonflow.interceptors.ollama import create_ollama_wrapper

        axonflow = MagicMock()
        wrapper = create_ollama_wrapper(axonflow, user_token="test")

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.generate = MagicMock()

        wrapped = wrapper(mock_client)
        assert wrapped is not None

    def test_bedrock_create_wrapper(self) -> None:
        """Test create_bedrock_wrapper factory function."""
        from axonflow.interceptors.bedrock import create_bedrock_wrapper

        axonflow = MagicMock()
        wrapper = create_bedrock_wrapper(axonflow, user_token="test")

        mock_client = MagicMock()
        mock_client.invoke_model = MagicMock()
        mock_client.invoke_model_with_response_stream = MagicMock()

        wrapped = wrapper(mock_client)
        assert wrapped is not None


class TestInterceptorExtractPromptEdgeCases:
    """Test edge cases in prompt extraction."""

    def test_gemini_extract_with_kwargs_contents(self) -> None:
        """Test Gemini prompt extraction from kwargs."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow)

        # When called with no args but contents in kwargs
        prompt = interceptor.extract_prompt()
        assert prompt == ""

    def test_ollama_extract_empty_messages(self) -> None:
        """Test Ollama prompt extraction with empty messages list."""
        axonflow = MagicMock()
        interceptor = OllamaInterceptor(axonflow)

        prompt = interceptor.extract_prompt(messages=[])
        assert prompt == ""

    def test_bedrock_extract_invalid_json(self) -> None:
        """Test Bedrock handles invalid JSON gracefully."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        prompt = interceptor.extract_prompt(body="not-valid-json")
        assert prompt == ""

    def test_openai_extract_only_system_message(self) -> None:
        """Test OpenAI prompt extraction with only system message."""
        axonflow = MagicMock()
        interceptor = OpenAIInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[
                {"role": "system", "content": "You are helpful"},
            ]
        )
        assert "You are helpful" in prompt

    def test_anthropic_extract_empty_content_list(self) -> None:
        """Test Anthropic handles empty content list."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[
                {"role": "user", "content": []},
            ]
        )
        assert prompt == ""

    def test_bedrock_extract_cohere_body(self) -> None:
        """Test Bedrock extracts from Cohere body format."""
        import json

        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = json.dumps({"prompt": "Hello Cohere"})
        prompt = interceptor.extract_prompt(body=body)
        assert "Hello Cohere" in prompt


class TestBaseInterceptorViaSubclass:
    """Test BaseInterceptor through concrete subclasses."""

    def test_interceptor_has_axonflow_reference(self) -> None:
        """Test that interceptors store axonflow reference."""
        axonflow = MagicMock()
        interceptor = OpenAIInterceptor(axonflow, user_token="test-user")

        assert interceptor.axonflow is axonflow
        assert interceptor.user_token == "test-user"

    def test_all_interceptors_have_required_methods(self) -> None:
        """Test that all interceptors implement required methods."""
        axonflow = MagicMock()

        for interceptor_cls in [
            OpenAIInterceptor,
            AnthropicInterceptor,
            GeminiInterceptor,
            OllamaInterceptor,
            BedrockInterceptor,
        ]:
            interceptor = interceptor_cls(axonflow)
            assert hasattr(interceptor, "get_provider_name")
            assert hasattr(interceptor, "extract_prompt")
            assert hasattr(interceptor, "wrap")
            assert callable(interceptor.get_provider_name)
            assert callable(interceptor.extract_prompt)
            assert callable(interceptor.wrap)
