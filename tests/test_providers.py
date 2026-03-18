"""Tests for provider selection and runtime wiring."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from app.config import EngineConfig
from app.openai_providers import (
    OpenAIContentGenerationProvider,
    OpenAIMarketSignalProvider,
    _structured_output_prompt,
    _structured_token_budgets,
)
from app.providers import MockContentGenerationProvider, MockKeywordDataProvider, MockMarketSignalProvider, build_provider_registry
from app.schemas import ArticleManifest, FactCheckReport, MarketSignalReport, TopicCandidate, SearchIntent


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


def test_openai_text_handles_none_output_text():
    class FakeResponse:
        output_text = None
        usage = None

    class FakeResponsesAPI:
        def create(self, **kwargs):
            return FakeResponse()

    provider = OpenAIMarketSignalProvider(
        _make_config(provider_mode="openai", openai_api_key="test-key")
    )
    provider.client = type("FakeClient", (), {"responses": FakeResponsesAPI()})()

    with pytest.raises(ValueError, match="did not contain text output"):
        provider._text(
            operation="tiny_text",
            prompt="Return text.",
            use_web_search=False,
            max_output_tokens=100,
        )


def test_generate_topics_includes_market_signals_in_prompt(monkeypatch):
    provider = OpenAIContentGenerationProvider(
        _make_config(provider_mode="openai", openai_api_key="test-key")
    )
    captured: dict[str, str] = {}

    class TopicBatch(BaseModel):
        topics: list[TopicCandidate]

    def fake_parse(self, **kwargs):
        captured["prompt"] = kwargs["prompt"]
        return TopicBatch(
            topics=[
                TopicCandidate(
                    title="AI Review Benchmarks for Senior Teams",
                    slug="ai-review-benchmarks-senior-teams",
                    cluster="ai-code-review",
                    description="Benchmark AI review with engineering-grade methodology.",
                    target_keywords=["ai code review benchmark", "engineering review benchmark"],
                    search_intent=SearchIntent.INFORMATIONAL,
                    source="test",
                    rationale="Grounded in research signals.",
                )
            ]
        )

    monkeypatch.setattr(OpenAIContentGenerationProvider, "_parse", fake_parse)

    provider.generate_topics(
        "Generate topics.",
        MarketSignalReport(
            signals=[],
            trending_themes=["signal alpha"],
            recommended_angles=["angle beta"],
        ),
    )

    assert "signal alpha" in captured["prompt"]
    assert "angle beta" in captured["prompt"]


def test_fact_check_cache_key_includes_prompt(monkeypatch, tmp_path):
    provider = OpenAIContentGenerationProvider(
        _make_config(
            provider_mode="openai",
            openai_api_key="test-key",
            project_root=tmp_path,
            config_dir=tmp_path / "config",
            data_dir=tmp_path / "data",
        )
    )
    calls: list[str] = []

    def fake_parse(self, **kwargs):
        calls.append(kwargs["prompt"])
        return FactCheckReport(
            checked_claims=["A"],
            verified_claims=["A"],
            flagged_claims=[],
            required_revisions=[],
            notes=[],
            passed=True,
        )

    monkeypatch.setattr(OpenAIContentGenerationProvider, "_parse", fake_parse)
    manifest = ArticleManifest(
        title="AI Review",
        slug="ai-review",
        primary_keyword="ai review",
        opening_direct_answer="Direct answer.",
        heading_map=["Intro"],
        faq_questions=[],
        internal_links=[],
        claim_candidates=["A"],
        section_excerpts=[],
        qa_snapshot={},
        seo_snapshot={},
        word_count=1000,
        meta_description="A sufficiently long meta description for testing cache behavior in fact checks.",
    )

    provider.fact_check_claims("Prompt one", manifest)
    provider.fact_check_claims("Prompt two", manifest)

    assert calls == ["Prompt one", "Prompt two"]
