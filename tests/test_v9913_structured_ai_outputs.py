from __future__ import annotations

import json
from decimal import Decimal

import pytest

from services.ai_trading import (
    AIOutputMode, AIProviderCapabilities, AIProviderError, AIProviderRequest,
    ChatCompletionsAIProvider, OpenAIResponsesAIProvider, RESPONSE_SCHEMA,
    SCHEMA_CHECKSUM, SCHEMA_VERSION, checksum, resolve_output_mode,
)
from version import APP_VERSION, RELEASE_NAME


def _request(mode=AIOutputMode.STRICT_JSON_SCHEMA):
    return AIProviderRequest("system", "prompt", {"symbol": "BTCUSDT"}, RESPONSE_SCHEMA, 500,
                             AIOutputMode.AUTO, mode, SCHEMA_VERSION, SCHEMA_CHECKSUM)


def test_release_and_schema_checksum_are_stable():
    assert APP_VERSION == "10.2.0" and RELEASE_NAME == "Multilingual Autonomous Intelligence Platform"
    assert SCHEMA_CHECKSUM == checksum(RESPONSE_SCHEMA)
    assert RESPONSE_SCHEMA["additionalProperties"] is False
    assert set(RESPONSE_SCHEMA["required"]) <= set(RESPONSE_SCHEMA["properties"])
    assert RESPONSE_SCHEMA["properties"]["recommended_risk_multiplier"]["maximum"] == 1


def test_strict_chat_completions_request_sends_schema(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MODEL", "gpt-test")
    provider = ChatCompletionsAIProvider()
    body = provider.build_request(_request())
    structured = body["response_format"]
    assert structured["type"] == "json_schema"
    assert structured["json_schema"]["strict"] is True
    assert structured["json_schema"]["schema"] == RESPONSE_SCHEMA
    assert body["max_tokens"] == 500 and body["temperature"] == 0


def test_chat_model_parameter_capabilities(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "compatible")
    monkeypatch.setenv("AI_MODEL", "reasoning-model")
    monkeypatch.setenv("AI_SUPPORTS_MAX_TOKENS", "false")
    monkeypatch.setenv("AI_SUPPORTS_MAX_COMPLETION_TOKENS", "true")
    monkeypatch.setenv("AI_SUPPORTS_TEMPERATURE", "false")
    body = ChatCompletionsAIProvider().build_request(_request())
    assert body["max_completion_tokens"] == 500
    assert "max_tokens" not in body and "temperature" not in body


def test_unsupported_token_parameter_is_non_retryable(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "compatible")
    monkeypatch.setenv("AI_MODEL", "model")
    monkeypatch.setenv("AI_SUPPORTS_MAX_TOKENS", "false")
    monkeypatch.setenv("AI_SUPPORTS_MAX_COMPLETION_TOKENS", "false")
    with pytest.raises(AIProviderError) as error:
        ChatCompletionsAIProvider().build_request(_request())
    assert error.value.code == "MODEL_PARAMETER_UNSUPPORTED" and not error.value.retryable


def test_responses_request_uses_native_structured_text_format(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MODEL", "responses-model")
    provider = OpenAIResponsesAIProvider()
    body = provider.build_request(_request())
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"] == RESPONSE_SCHEMA
    assert body["max_output_tokens"] == 500 and "messages" not in body


def test_output_fallback_and_strict_required_are_recorded(monkeypatch):
    capabilities = AIProviderCapabilities(supports_json_object=True)
    monkeypatch.setenv("AI_STRUCTURED_OUTPUT_MODE", "auto")
    monkeypatch.setenv("AI_ALLOW_JSON_OBJECT_FALLBACK", "true")
    requested, effective, reason = resolve_output_mode(capabilities)
    assert requested is AIOutputMode.AUTO and effective is AIOutputMode.JSON_OBJECT
    assert reason == "STRICT_SCHEMA_UNSUPPORTED"

    monkeypatch.setenv("AI_STRUCTURED_OUTPUT_MODE", "json_schema")
    monkeypatch.setenv("AI_STRICT_SCHEMA_REQUIRED", "true")
    _, effective, reason = resolve_output_mode(capabilities)
    assert effective is AIOutputMode.DISABLED and reason == "STRICT_SCHEMA_UNSUPPORTED"


def test_json_object_fallback_supplies_schema_in_prompt(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "compatible")
    monkeypatch.setenv("AI_MODEL", "legacy-model")
    provider = ChatCompletionsAIProvider()
    body = provider.build_request(_request(AIOutputMode.JSON_OBJECT))
    user_payload = json.loads(body["messages"][1]["content"])
    assert body["response_format"] == {"type": "json_object"}
    assert user_payload["response_schema"] == RESPONSE_SCHEMA
    assert user_payload["response_schema_version"] == SCHEMA_VERSION


@pytest.mark.asyncio
async def test_chat_response_usage_request_id_and_pricing(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MODEL", "model")
    monkeypatch.setenv("AI_PRICE_VERSION", "prices-v1")
    monkeypatch.setenv("AI_INPUT_COST_PER_MILLION_USD", "2")
    monkeypatch.setenv("AI_CACHED_INPUT_COST_PER_MILLION_USD", "1")
    monkeypatch.setenv("AI_OUTPUT_COST_PER_MILLION_USD", "4")

    class Provider(ChatCompletionsAIProvider):
        async def _post(self, body):
            return ({"id": "body-id", "choices": [{"message": {"content": "{}"}}], "usage": {
                "prompt_tokens": 100, "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 40},
                "completion_tokens_details": {"reasoning_tokens": 5},
            }}, "header-id")

    result = await Provider().analyze(_request())
    assert result.provider_request_id == "header-id"
    assert result.cached_tokens == 40 and result.reasoning_tokens == 5
    assert result.cost_status == "PRICED" and result.pricing_version == "prices-v1"
    assert result.estimated_cost_usd == Decimal("0.00024")


@pytest.mark.asyncio
async def test_responses_output_and_usage_normalization(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MODEL", "model")
    monkeypatch.delenv("AI_PRICE_VERSION", raising=False)

    class Provider(OpenAIResponsesAIProvider):
        async def _post(self, body):
            return ({"id": "resp-id", "output_text": "{}", "usage": {
                "input_tokens": 10, "output_tokens": 5,
                "input_tokens_details": {"cached_tokens": 2},
                "output_tokens_details": {"reasoning_tokens": 3},
            }}, None)

    result = await Provider().analyze(_request())
    assert result.payload == {} and result.structured_text == "{}" and result.provider_request_id == "resp-id"
    assert result.input_tokens == 10 and result.output_tokens == 5
    assert result.cost_status == "UNPRICED"


@pytest.mark.asyncio
async def test_malformed_outer_response_is_normalized(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MODEL", "model")

    class Provider(ChatCompletionsAIProvider):
        async def _post(self, body):
            return ({"choices": []}, None)

    result = await Provider().analyze(_request())
    assert not result.extraction_valid and result.extraction_code == "CHAT_CHOICES_MISSING"
