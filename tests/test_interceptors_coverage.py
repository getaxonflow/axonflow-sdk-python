"""Additional tests for LLM provider interceptors to increase coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.exceptions import PolicyViolationError
from axonflow.interceptors.anthropic import (
    AnthropicInterceptor,
    create_anthropic_wrapper,
)
from axonflow.interceptors.bedrock import (
    BedrockInterceptor,
    BedrockModels,
    create_bedrock_wrapper,
)
from axonflow.interceptors.gemini import (
    GeminiInterceptor,
    create_gemini_wrapper,
)
from axonflow.interceptors.ollama import (
    OllamaInterceptor,
    create_ollama_wrapper,
)
from axonflow.interceptors.openai import (
    OpenAIInterceptor,
    create_openai_wrapper,
)


class TestGeminiInterceptorCoverage:
    """Extended tests for Gemini interceptor."""

    def test_gemini_interceptor_wrap_method(self) -> None:
        """Test GeminiInterceptor.wrap method."""
        mock_axonflow = MagicMock()
        interceptor = GeminiInterceptor(mock_axonflow, user_token="user-123")

        mock_model = MagicMock()
        mock_model.generate_content = MagicMock()

        with patch(
            "axonflow.interceptors.gemini.wrap_gemini_model", return_value=mock_model
        ):
            result = interceptor.wrap(mock_model)
            assert result is not None

    def test_extract_prompt_with_content_parts_in_list(self) -> None:
        """Test extracting prompts from list with Content objects."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow)

        # Create mock Content objects
        mock_part = MagicMock()
        mock_part.text = "Hello from part"

        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        prompt = interceptor.extract_prompt([mock_content])
        assert "Hello from part" in prompt

    def test_extract_prompt_string_list(self) -> None:
        """Test extracting prompts from list of strings."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow)

        prompt = interceptor.extract_prompt(["Hello", "World"])
        assert "Hello" in prompt
        assert "World" in prompt

    def test_extract_prompt_empty_list(self) -> None:
        """Test extracting prompts from empty list."""
        axonflow = MagicMock()
        interceptor = GeminiInterceptor(axonflow)

        prompt = interceptor.extract_prompt([])
        assert prompt == ""

    def test_create_gemini_wrapper(self) -> None:
        """Test create_gemini_wrapper factory function."""
        mock_axonflow = MagicMock()
        wrapper = create_gemini_wrapper(mock_axonflow, user_token="test")

        mock_model = MagicMock()
        mock_model.generate_content = MagicMock()

        with patch("axonflow.interceptors.gemini.wrap_gemini_model") as mock_wrap:
            mock_wrap.return_value = mock_model
            result = wrapper(mock_model)
            mock_wrap.assert_called_once()


class TestOllamaInterceptorCoverage:
    """Extended tests for Ollama interceptor."""

    def test_ollama_interceptor_wrap_method(self) -> None:
        """Test OllamaInterceptor.wrap method."""
        mock_axonflow = MagicMock()
        interceptor = OllamaInterceptor(mock_axonflow, user_token="user-123")

        mock_client = MagicMock()

        with patch(
            "axonflow.interceptors.ollama.wrap_ollama_client", return_value=mock_client
        ):
            result = interceptor.wrap(mock_client)
            assert result is not None

    def test_extract_prompt_from_prompt_kwarg_str(self) -> None:
        """Test extracting prompt from prompt kwarg."""
        axonflow = MagicMock()
        interceptor = OllamaInterceptor(axonflow)

        prompt = interceptor.extract_prompt(prompt="Generate something")
        assert prompt == "Generate something"

    def test_extract_prompt_messages_only(self) -> None:
        """Test extracting prompts from messages only."""
        axonflow = MagicMock()
        interceptor = OllamaInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[{"content": "Hello"}, {"content": "World"}]
        )
        assert "Hello" in prompt
        assert "World" in prompt

    def test_create_ollama_wrapper(self) -> None:
        """Test create_ollama_wrapper factory function."""
        mock_axonflow = MagicMock()
        wrapper = create_ollama_wrapper(mock_axonflow, user_token="test")

        mock_client = MagicMock()

        with patch("axonflow.interceptors.ollama.wrap_ollama_client") as mock_wrap:
            mock_wrap.return_value = mock_client
            result = wrapper(mock_client)
            mock_wrap.assert_called_once()


class TestBedrockInterceptorCoverage:
    """Extended tests for Bedrock interceptor."""

    def test_bedrock_models_constants(self) -> None:
        """Test BedrockModels constants are defined."""
        assert BedrockModels.CLAUDE_3_OPUS is not None
        assert BedrockModels.CLAUDE_3_SONNET is not None
        assert BedrockModels.CLAUDE_3_HAIKU is not None
        assert BedrockModels.CLAUDE_2_1 is not None
        assert BedrockModels.CLAUDE_2 is not None
        assert BedrockModels.CLAUDE_INSTANT is not None
        assert BedrockModels.TITAN_TEXT_EXPRESS is not None
        assert BedrockModels.TITAN_TEXT_LITE is not None
        assert BedrockModels.TITAN_TEXT_PREMIER is not None
        assert BedrockModels.LLAMA2_13B is not None
        assert BedrockModels.LLAMA2_70B is not None
        assert BedrockModels.LLAMA3_8B is not None
        assert BedrockModels.LLAMA3_70B is not None

    def test_bedrock_interceptor_wrap_method(self) -> None:
        """Test BedrockInterceptor.wrap method."""
        mock_axonflow = MagicMock()
        interceptor = BedrockInterceptor(mock_axonflow, user_token="user-123")

        mock_client = MagicMock()

        with patch(
            "axonflow.interceptors.bedrock.wrap_bedrock_client",
            return_value=mock_client,
        ):
            result = interceptor.wrap(mock_client)
            assert result is not None

    def test_extract_prompt_from_bytes_body(self) -> None:
        """Test extracting prompt from bytes body."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = b'{"messages": [{"content": "Hello bytes"}]}'
        prompt = interceptor.extract_prompt(body=body)
        assert "Hello bytes" in prompt

    def test_extract_prompt_from_bytearray_body(self) -> None:
        """Test extracting prompt from bytearray body."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = bytearray(b'{"inputText": "Hello bytearray"}')
        prompt = interceptor.extract_prompt(body=body)
        assert prompt == "Hello bytearray"

    def test_extract_prompt_from_llama_body(self) -> None:
        """Test extracting prompt from Llama format body."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = '{"prompt": "Hello from Llama"}'
        prompt = interceptor.extract_prompt(body=body)
        assert prompt == "Hello from Llama"

    def test_extract_prompt_invalid_json(self) -> None:
        """Test extracting prompt from invalid JSON."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        body = "not valid json"
        prompt = interceptor.extract_prompt(body=body)
        assert prompt == ""

    def test_extract_prompt_empty_body(self) -> None:
        """Test extracting prompt from empty body."""
        axonflow = MagicMock()
        interceptor = BedrockInterceptor(axonflow)

        prompt = interceptor.extract_prompt(body="")
        assert prompt == ""

    def test_create_bedrock_wrapper(self) -> None:
        """Test create_bedrock_wrapper factory function."""
        mock_axonflow = MagicMock()
        wrapper = create_bedrock_wrapper(mock_axonflow, user_token="test")

        mock_client = MagicMock()

        with patch("axonflow.interceptors.bedrock.wrap_bedrock_client") as mock_wrap:
            mock_wrap.return_value = mock_client
            result = wrapper(mock_client)
            mock_wrap.assert_called_once()


class TestOpenAIInterceptorCoverage:
    """Extended tests for OpenAI interceptor."""

    def test_openai_interceptor_wrap_method(self) -> None:
        """Test OpenAIInterceptor.wrap method."""
        mock_axonflow = MagicMock()
        interceptor = OpenAIInterceptor(mock_axonflow, user_token="user-123")

        mock_client = MagicMock()

        with patch(
            "axonflow.interceptors.openai.wrap_openai_client", return_value=mock_client
        ):
            result = interceptor.wrap(mock_client)
            assert result is not None

    def test_extract_prompt_system_only(self) -> None:
        """Test extracting prompt with only system message."""
        axonflow = MagicMock()
        interceptor = OpenAIInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[{"role": "system", "content": "System only"}]
        )
        assert "System only" in prompt

    def test_extract_prompt_multiple_messages(self) -> None:
        """Test extracting prompt with multiple messages."""
        axonflow = MagicMock()
        interceptor = OpenAIInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
                {"role": "user", "content": "Goodbye"},
            ]
        )
        assert "Be helpful" in prompt
        assert "Hello" in prompt
        assert "Hi there" in prompt
        assert "Goodbye" in prompt

    def test_create_openai_wrapper(self) -> None:
        """Test create_openai_wrapper factory function."""
        mock_axonflow = MagicMock()
        wrapper = create_openai_wrapper(mock_axonflow, user_token="test")

        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock()

        with patch("axonflow.interceptors.openai.wrap_openai_client") as mock_wrap:
            mock_wrap.return_value = mock_client
            result = wrapper(mock_client)
            mock_wrap.assert_called_once()


class TestAnthropicInterceptorCoverage:
    """Extended tests for Anthropic interceptor."""

    def test_anthropic_interceptor_wrap_method(self) -> None:
        """Test AnthropicInterceptor.wrap method."""
        mock_axonflow = MagicMock()
        interceptor = AnthropicInterceptor(mock_axonflow, user_token="user-123")

        mock_client = MagicMock()

        with patch(
            "axonflow.interceptors.anthropic.wrap_anthropic_client",
            return_value=mock_client,
        ):
            result = interceptor.wrap(mock_client)
            assert result is not None

    def test_extract_prompt_string_content(self) -> None:
        """Test extracting prompt from string content."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow)

        prompt = interceptor.extract_prompt(
            messages=[{"role": "user", "content": "Hello string"}]
        )
        assert "Hello string" in prompt

    def test_extract_prompt_block_content_text(self) -> None:
        """Test extracting prompt with text block content."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow)

        # Content blocks are dicts, not objects
        block = {"type": "text", "text": "Block text"}

        prompt = interceptor.extract_prompt(
            messages=[{"role": "user", "content": [block]}]
        )
        assert "Block text" in prompt

    def test_extract_prompt_non_text_block(self) -> None:
        """Test extracting prompt skips non-text blocks."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow)

        # Non-text block should be skipped
        block = {"type": "image", "source": {"data": "..."}}

        prompt = interceptor.extract_prompt(
            messages=[{"role": "user", "content": [block]}]
        )
        assert prompt == ""

    def test_extract_prompt_empty_messages(self) -> None:
        """Test extracting prompt from empty messages."""
        axonflow = MagicMock()
        interceptor = AnthropicInterceptor(axonflow)

        prompt = interceptor.extract_prompt(messages=[])
        assert prompt == ""

    def test_create_anthropic_wrapper(self) -> None:
        """Test create_anthropic_wrapper factory function."""
        mock_axonflow = MagicMock()
        wrapper = create_anthropic_wrapper(mock_axonflow, user_token="test")

        mock_client = MagicMock()
        mock_client.messages.create = MagicMock()

        with patch(
            "axonflow.interceptors.anthropic.wrap_anthropic_client"
        ) as mock_wrap:
            mock_wrap.return_value = mock_client
            result = wrapper(mock_client)
            mock_wrap.assert_called_once()


class TestBaseInterceptor:
    """Test BaseInterceptor class via subclasses."""

    def test_all_interceptors_have_required_methods(self) -> None:
        """Test all interceptors have the required methods."""
        mock_axonflow = MagicMock()

        interceptors = [
            OpenAIInterceptor(mock_axonflow),
            AnthropicInterceptor(mock_axonflow),
            GeminiInterceptor(mock_axonflow),
            OllamaInterceptor(mock_axonflow),
            BedrockInterceptor(mock_axonflow),
        ]

        for interceptor in interceptors:
            assert hasattr(interceptor, "get_provider_name")
            assert hasattr(interceptor, "extract_prompt")
            assert hasattr(interceptor, "wrap")
            assert hasattr(interceptor, "axonflow")
            assert hasattr(interceptor, "user_token")

    def test_interceptors_provider_names(self) -> None:
        """Test all interceptors return correct provider names."""
        mock_axonflow = MagicMock()

        assert OpenAIInterceptor(mock_axonflow).get_provider_name() == "openai"
        assert AnthropicInterceptor(mock_axonflow).get_provider_name() == "anthropic"
        assert GeminiInterceptor(mock_axonflow).get_provider_name() == "gemini"
        assert OllamaInterceptor(mock_axonflow).get_provider_name() == "ollama"
        assert BedrockInterceptor(mock_axonflow).get_provider_name() == "bedrock"
