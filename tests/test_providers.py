"""Tests for provider selection and runtime wiring."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from app.config import EngineConfig
from app.openai_providers import (
    OpenAIMarketSignalProvider,
    _structured_output_prompt,
    _structured_token_budgets,
)
from app.providers import (
    MockContentGenerationProvider,
    MockKeywordDataProvider,
    MockMarketSignalProvider,
    build_provider_registry,
)


def _make_config(**overrides) -> EngineConfig:
    base = EngineConfig(
        project_root=Path("/tmp/project"),
        config_dir=Path("/tmp/project/config"),
        data_dir=Path("/tmp/project/data"),
    )
    return EngineConfig(**{**base.__dict__, **overrides})


def test_build_provider_registry_uses_mocks_by_default():
    registry = build_provider_registry(_make_config(provider_mode="mock"))
    assert isinstance(registry.market_signals, MockMarketSignalProvider)
    assert isinstance(registry.keyword_data, MockKeywordDataProvider)
    assert isinstance(registry.content_generation, MockContentGenerationProvider)


def test_build_provider_registry_requires_api_key_for_openai():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_provider_registry(_make_config(provider_mode="openai", openai_api_key=None))


def test_build_provider_registry_builds_openai_providers():
    registry = build_provider_registry(
        _make_config(
            provider_mode="openai",
            openai_api_key="test-key",
        )
    )
    assert registry.market_signals.__class__.__name__ == "OpenAIMarketSignalProvider"
    assert registry.keyword_data.__class__.__name__ == "OpenAIKeywordDataProvider"
    assert registry.content_generation.__class__.__name__ == "OpenAIContentGenerationProvider"


def test_structured_output_prompt_enforces_compact_json():
    prompt = _structured_output_prompt("Return a schema.")
    assert "Return only compact JSON" in prompt
    assert "Do not wrap the JSON in markdown fences." in prompt


def test_structured_token_budgets_scale_without_duplicates():
    assert _structured_token_budgets(2000) == [2000, 3000, 4000]
    assert _structured_token_budgets(1000) == [1000, 1500, 2000]


def test_openai_parse_retries_truncated_json(monkeypatch):
    class TinyModel(BaseModel):
        value: str

    class FakeResponse:
        def __init__(self, output_text: str, *, status: str, incomplete_details=None):
            self.output_text = output_text
            self.status = status
            self.incomplete_details = incomplete_details

    class FakeResponsesAPI:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeResponse(
                    '{"value": "truncated"',
                    status="incomplete",
                    incomplete_details={"reason": "max_output_tokens"},
                )
            return FakeResponse('{"value": "ok"}', status="completed")

    provider = OpenAIMarketSignalProvider(
        _make_config(provider_mode="openai", openai_api_key="test-key")
    )
    fake_responses = FakeResponsesAPI()
    provider.client = type("FakeClient", (), {"responses": fake_responses})()

    parsed = provider._parse(
        text_format=TinyModel,
        operation="tiny_test",
        prompt="Return JSON.",
        use_web_search=False,
        max_output_tokens=1000,
    )

    assert parsed.value == "ok"
    assert fake_responses.calls == 2
