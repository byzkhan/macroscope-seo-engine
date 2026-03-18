"""Tests for the token-reduction pipeline architecture."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.config import EngineConfig
from app.guardrails import build_run_context
from app.openai_providers import (
    OpenAIContentGenerationProvider,
    OpenAIKeywordDataProvider,
    OpenAIMarketSignalProvider,
)
from app.orchestrator import (
    PipelineOrchestrator,
    _apply_optimization_patch,
    _collect_article_jury_scores,
    _normalize_internal_markdown_links,
    _should_unlock_second_draft,
)
from app.providers import MockContentGenerationProvider, ProviderRegistry
from app.schemas import (
    ArticleManifest,
    BlueprintSection,
    FactCheckReport,
    InternalLink,
    JudgeScore,
    MarketSignal,
    MarketSignalReport,
    OptimizationPatch,
    ProviderCallUsage,
    ResearchBrief,
    ResearchPacket,
    ScoredTopic,
    SearchIntent,
    SEOAEOScore,
    TopicCandidate,
    TopicScore,
    WriterBlueprint,
    FAQ,
    OutlineSection,
    RunSummary,
    RunUsageLedger,
)
from app.storage import RunStore


def _make_config(tmp_path: Path, **overrides) -> EngineConfig:
    base = EngineConfig(
        project_root=tmp_path,
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        provider_mode="mock",
    )
    return EngineConfig(**{**base.__dict__, **overrides})


def _make_candidate(index: int) -> TopicCandidate:
    return TopicCandidate(
        title=f"AI Code Review Topic {index}",
        slug=f"ai-code-review-topic-{index}",
        cluster="ai-code-review",
        description=f"Topic {index} explores technical evaluation patterns for AI code review in engineering teams.",
        target_keywords=[
            f"ai code review topic {index}",
            f"engineering review topic {index}",
            f"benchmark workflow topic {index}",
        ],
        search_intent=SearchIntent.COMMERCIAL,
        freshness_signal=f"fresh signal {index}",
        source="test",
        rationale="Engineering-facing topic with clear evaluation angle.",
    )


def _make_scored_topic(index: int = 1) -> ScoredTopic:
    return ScoredTopic(
        candidate=_make_candidate(index),
        score=TopicScore(
            business_relevance=22.0,
            search_opportunity=16.0,
            aeo_fit=13.0,
            freshness=8.0,
            authority_to_win=14.0,
            uniqueness_vs_archive=9.0,
            production_ease=4.0,
        ),
    )


def _make_brief(index: int = 1) -> ResearchBrief:
    topic = _make_scored_topic(index).candidate
    return ResearchBrief(
        topic=topic,
        outline=[
            OutlineSection(
                heading=f"Section {i}",
                description=f"Engineering section {i}",
                target_word_count=220,
                key_points=[f"point {i}a", f"point {i}b"],
            )
            for i in range(1, 7)
        ],
        target_word_count=1800,
        primary_keyword=topic.target_keywords[0],
        secondary_keywords=topic.target_keywords[1:],
        entities=["Macroscope", "GitHub", "CI", "benchmarks"],
        faqs=[
            FAQ(
                question=f"How does topic {index} work in practice?",
                suggested_answer="Teams benchmark it on real pull requests and rollout gates.",
            )
            for _ in range(5)
        ],
        claims_needing_evidence=["Benchmark methodology", "False-positive rate", "Rollout guidance"],
        internal_link_suggestions=[
            InternalLink(anchor_text="AI review guide", target_path="/blog/guide", context="intro"),
            InternalLink(anchor_text="PR workflow", target_path="/blog/pr-workflow", context="body"),
            InternalLink(anchor_text="Benchmarks", target_path="/blog/benchmarks", context="faq"),
        ],
        cta="Try Macroscope",
        do_not_say=["guaranteed"],
        meta_description="Engineering guide to evaluating AI code review quality with benchmarks and rollout criteria.",
        title_options=[topic.title, f"{topic.title} Guide"],
    )


def _make_research_packet() -> ResearchPacket:
    return ResearchPacket(
        themes=["ai code review", "engineering workflows", "benchmarks"],
        fresh_market_notes=["Theme signal: ai code review"],
        keyword_serp_notes=["Shortlist the SERP around 'ai code review' using the research packet only."],
    )


def _make_manifest() -> ArticleManifest:
    return ArticleManifest(
        title="AI Code Review Topic",
        slug="ai-code-review-topic",
        primary_keyword="ai code review",
        opening_direct_answer="Direct answer: benchmark AI review on real pull requests and keep humans on risky changes.",
        heading_map=["AI Code Review Topic", "Benchmarks", "Rollout", "FAQ"],
        faq_questions=["How should teams benchmark AI code review?"],
        internal_links=["/blog/guide", "/blog/pr-workflow", "/blog/benchmarks"],
        claim_candidates=["Benchmark methodology", "False-positive rate"],
        section_excerpts=[],
        qa_snapshot={"passed": True, "summary": "9/9 checks passed", "failed_checks": []},
        seo_snapshot={
            "title_score": 9.0,
            "meta_description_score": 9.0,
            "heading_structure_score": 9.0,
            "internal_links_score": 9.0,
            "faq_presence_score": 9.0,
            "direct_answer_score": 9.0,
            "content_depth_score": 9.0,
            "total": 88.0,
            "grade": "A",
        },
        word_count=1800,
        meta_description="Engineering guide to evaluating AI code review quality with benchmarks and rollout criteria.",
    )


def test_generate_topics_caps_merged_candidates_to_12(tmp_path):
    class TopicBatchProvider(MockContentGenerationProvider):
        def __init__(self):
            self.calls = 0

        def generate_topics(self, prompt: str, market_signals: MarketSignalReport) -> list[TopicCandidate]:
            batch_index = self.calls
            self.calls += 1
            return [_make_candidate(batch_index * 5 + offset) for offset in range(5)]

    config = _make_config(tmp_path)
    provider = TopicBatchProvider()
    orchestrator = PipelineOrchestrator(config, providers=ProviderRegistry(content_generation=provider))
    orchestrator.store = RunStore(config.data_dir, run_id="topic-cap-run")
    orchestrator.summary = RunSummary(run_id="topic-cap-run", started_at=datetime.now(timezone.utc))

    market_signals = MarketSignalReport(
        signals=[],
        trending_themes=["ai code review", "engineering workflows"],
        recommended_angles=["benchmark angle"],
    )
    result = orchestrator._generate_topics(
        {
            "market_signals": market_signals,
            "research_packet": _make_research_packet(),
        }
    )

    assert len(result["candidates"]) == 12


def test_score_topics_uses_strict_model_funnel(tmp_path):
    class TopicJudgeProvider(MockContentGenerationProvider):
        def __init__(self):
            self.calls: dict[str, list[str]] = {}

        def judge_topic(self, prompt: str, topic: ScoredTopic, judge_name: str) -> JudgeScore:
            self.calls.setdefault(topic.candidate.slug, []).append(judge_name)
            return JudgeScore(judge=judge_name, score=9.1, rationale="Strong topic")

    config = _make_config(
        tmp_path,
        provider_mode="openai",
        openai_api_key="test-key",
        min_topic_consensus_score=0.0,
        min_topic_authority_score=0.0,
        model_judged_topics=6,
        full_panel_topics=2,
    )
    provider = TopicJudgeProvider()
    orchestrator = PipelineOrchestrator(config, providers=ProviderRegistry(content_generation=provider))
    orchestrator.store = RunStore(config.data_dir, run_id="topic-funnel-run")
    orchestrator.summary = RunSummary(run_id="topic-funnel-run", started_at=datetime.now(timezone.utc))

    result = orchestrator._score_topics(
        {
            "candidates": [_make_candidate(index) for index in range(12)],
            "run_context": build_run_context(config, "topic-funnel-run"),
            "research_packet": _make_research_packet(),
            "market_signals": MarketSignalReport(signals=[], trending_themes=[], recommended_angles=[]),
        }
    )

    ranked = result["scored"]
    assert len(ranked[0].judge_scores) == 5
    assert len(ranked[1].judge_scores) == 5
    assert len(ranked[2].judge_scores) == 3
    assert len(ranked[5].judge_scores) == 3
    assert len(ranked[6].judge_scores) == 0
    assert "Outside model-judged topic funnel" in ranked[6].rejection_reasons


def test_write_draft_generates_blueprints_but_one_full_draft_by_default(tmp_path):
    config = _make_config(tmp_path, writer_blueprints=3, full_draft_candidates=1)
    orchestrator = PipelineOrchestrator(config, providers=ProviderRegistry(content_generation=MockContentGenerationProvider()))
    orchestrator.store = RunStore(config.data_dir, run_id="draft-funnel-run")
    orchestrator.summary = RunSummary(run_id="draft-funnel-run", started_at=datetime.now(timezone.utc))

    result = orchestrator._write_draft(
        {
            "brief": _make_brief(),
            "research_packet": _make_research_packet(),
            "run_context": build_run_context(config, "draft-funnel-run"),
        }
    )

    assert len(result["writer_blueprints"]) == 3
    assert len(result["draft_variants"]) == 1
    assert result["runner_up_blueprint"] is not None


def test_second_draft_unlock_rule_is_strict():
    weak_gate = type("Gate", (), {"average_score": 8.6, "technical_accuracy_score": 9.0})()
    strong_gate = type("Gate", (), {"average_score": 9.1, "technical_accuracy_score": 9.2})()

    assert _should_unlock_second_draft(
        gate=weak_gate,
        round_number=4,
        second_draft_unlock_round=4,
        already_unlocked=False,
        runner_up_available=True,
    )
    assert not _should_unlock_second_draft(
        gate=weak_gate,
        round_number=3,
        second_draft_unlock_round=4,
        already_unlocked=False,
        runner_up_available=True,
    )
    assert not _should_unlock_second_draft(
        gate=strong_gate,
        round_number=5,
        second_draft_unlock_round=4,
        already_unlocked=False,
        runner_up_available=True,
    )


def test_optimization_patch_preserves_direct_answer_and_internal_links():
    brief = _make_brief()
    content = (
        "# AI Code Review Topic\n\n"
        "Old intro.\n\n"
        "## Benchmarks\n\n"
        "See https://macroscope.com/blog/guide for the benchmark workflow.\n"
    )
    patch = OptimizationPatch(
        opening_direct_answer="Direct answer: benchmark AI review on real pull requests and keep humans on risky changes.",
        internal_link_suggestions=[brief.internal_link_suggestions[0]],
        notes=["Refresh the opening and restore internal links."],
    )

    updated, notes = _apply_optimization_patch(content, patch=patch, brief=brief)

    assert "Direct answer: benchmark AI review on real pull requests" in updated
    assert "[AI review guide](/blog/guide)" in updated
    assert notes


def test_collect_article_jury_scores_uses_manifest_json(tmp_path):
    class ArticleJudgeProvider(MockContentGenerationProvider):
        def __init__(self):
            self.contents: list[str] = []

        def judge_article(self, prompt: str, content: str, judge_name: str) -> JudgeScore:
            self.contents.append(content)
            return JudgeScore(judge=judge_name, score=9.4, rationale="Strong")

    config = _make_config(tmp_path, provider_mode="openai", openai_api_key="test-key")
    provider = ArticleJudgeProvider()
    orchestrator = PipelineOrchestrator(config, providers=ProviderRegistry(content_generation=provider))

    manifest = _make_manifest()
    scores, notes = _collect_article_jury_scores(
        orchestrator=orchestrator,
        article_manifest=manifest,
        brief=_make_brief(),
        quality_policy=build_run_context(config, "article-jury-run").quality_policy,
    )

    assert len(scores) == 3
    assert notes == []
    assert provider.contents
    assert all(content == manifest.model_dump_json() for content in provider.contents)


def test_capture_stage_usage_records_run_usage_ledger(tmp_path):
    config = _make_config(tmp_path)
    orchestrator = PipelineOrchestrator(config)
    orchestrator.store = RunStore(config.data_dir, run_id="usage-run")
    orchestrator.summary = RunSummary(run_id="usage-run", started_at=datetime.now(timezone.utc))
    orchestrator.usage_ledger = RunUsageLedger()
    usage_record = ProviderCallUsage(
        provider="OpenAIContentGenerationProvider",
        operation="generate_topics",
        model="gpt-5-mini",
        input_tokens=120,
        output_tokens=80,
        total_tokens=200,
        web_search_used=False,
    )
    orchestrator.providers.drain_usage_records = lambda: [usage_record]

    orchestrator._capture_stage_usage("generate_topics")

    assert orchestrator.usage_ledger.total_tokens == 200
    assert orchestrator.usage_ledger.stage_summaries[0].stage == "generate_topics"
    assert (orchestrator.store.run_dir / "usage" / "run_usage_ledger.json").exists()


def test_openai_web_search_is_reserved_for_research_and_fact_check(tmp_path, monkeypatch):
    config = _make_config(tmp_path, provider_mode="openai", openai_api_key="test-key")
    market_provider = OpenAIMarketSignalProvider(config)
    keyword_provider = OpenAIKeywordDataProvider(config)
    content_provider = OpenAIContentGenerationProvider(config)
    calls: list[tuple[str, bool]] = []

    def fake_parse(self, *, text_format, operation, prompt, use_web_search, max_output_tokens, **kwargs):
        calls.append((operation, use_web_search))
        if text_format is MarketSignalReport:
            return MarketSignalReport(
                signals=[
                    MarketSignal(
                        source="hacker_news",
                        title="HN thread",
                        url="https://news.ycombinator.com/item?id=1",
                        summary="Engineers discuss AI review rollout criteria.",
                        relevance_score=0.9,
                        detected_at=datetime.now(timezone.utc),
                        themes=["ai code review"],
                    )
                ],
                trending_themes=["ai code review"],
                recommended_angles=["benchmark angle"],
            )
        if text_format.__name__ == "KeywordMetricBatch":
            return text_format(metrics=[])
        if text_format.__name__ == "SERPAnalysis":
            return text_format(keyword="ai code review", top_results=[], featured_snippet=False, people_also_ask=[])
        if text_format is WriterBlueprint:
            return WriterBlueprint(
                writer_id="technical",
                writer_label="Technical Writer",
                focus_summary="Technical depth",
                title="AI Code Review Topic",
                opening_hook="Engineering teams need a repeatable way to benchmark AI code review before rollout.",
                direct_answer="Direct answer: benchmark AI review on real pull requests.",
                sections=[
                    BlueprintSection(heading="Benchmarks", bullets=["Use real pull requests."]),
                    BlueprintSection(heading="Rollout", bullets=["Gate risky changes."]),
                    BlueprintSection(heading="FAQ", bullets=["Answer implementation questions."]),
                ],
                faq_plan=["How do benchmarks work?"],
                internal_link_targets=["/blog/guide"],
                claims_plan=["Benchmark methodology"],
                estimated_word_count=1600,
            )
        if text_format is OptimizationPatch:
            return OptimizationPatch(notes=["No-op patch"])
        if text_format is JudgeScore:
            return JudgeScore(judge="search_readiness_judge", score=9.2, rationale="Strong")
        if text_format is FactCheckReport:
            return FactCheckReport(checked_claims=["Benchmark methodology"], verified_claims=["Benchmark methodology"])
        if text_format is ResearchBrief:
            return _make_brief()
        if text_format.__name__ == "TopicCandidateBatch":
            return text_format(topics=[_make_candidate(1)])
        raise AssertionError(f"Unexpected parse type: {text_format}")

    def fake_text(self, *, operation, prompt, use_web_search, max_output_tokens, **kwargs):
        calls.append((operation, use_web_search))
        return "# AI Code Review Topic\n\nDirect answer: benchmark AI review on real pull requests."

    monkeypatch.setattr(OpenAIMarketSignalProvider, "_parse", fake_parse)
    monkeypatch.setattr(OpenAIKeywordDataProvider, "_parse", fake_parse)
    monkeypatch.setattr(OpenAIContentGenerationProvider, "_parse", fake_parse)
    monkeypatch.setattr(OpenAIContentGenerationProvider, "_text", fake_text)

    market_provider.collect(["ai code review"], prompt="Collect market signals.")
    keyword_provider.get_keyword_metrics(["ai code review"])
    keyword_provider.get_serp_analysis("ai code review")
    content_provider.generate_topics("Generate topics.", MarketSignalReport(signals=[], trending_themes=[], recommended_angles=[]))
    content_provider.generate_brief_bundle("Build brief.", _make_scored_topic(), _make_research_packet())
    content_provider.generate_writer_blueprint(
        "Build blueprint.",
        _make_brief(),
        _make_research_packet(),
        "technical",
        "Technical Writer",
    )
    content_provider.generate_draft_from_blueprint(
        "Write draft.",
        _make_brief(),
        WriterBlueprint(
            writer_id="technical",
            writer_label="Technical Writer",
            focus_summary="Technical depth",
            title="AI Code Review Topic",
            opening_hook="Engineering teams need a repeatable way to benchmark AI code review before rollout.",
            direct_answer="Direct answer: benchmark AI review on real pull requests.",
            sections=[
                BlueprintSection(heading="Benchmarks", bullets=["Use real pull requests."]),
                BlueprintSection(heading="Rollout", bullets=["Gate risky changes."]),
                BlueprintSection(heading="FAQ", bullets=["Answer implementation questions."]),
            ],
            faq_plan=["How do benchmarks work?"],
            internal_link_targets=["/blog/guide"],
            claims_plan=["Benchmark methodology"],
            estimated_word_count=1600,
        ),
        _make_research_packet(),
    )
    content_provider.optimize_sections(
        "Optimize sections.",
        "# Draft",
        _make_manifest(),
    )
    content_provider.judge_article("Judge article.", _make_manifest().model_dump_json(), "search_readiness_judge")
    content_provider.fact_check_claims("Fact check.", _make_manifest())

    assert ("collect_market_signals", True) in calls
    assert ("fact_check_claims", True) in calls
    assert all(
        use_web_search is False
        for operation, use_web_search in calls
        if operation not in {"collect_market_signals", "fact_check_claims"}
    )


def test_normalize_internal_links_does_not_duplicate_absolute_macroscope_links():
    repaired, notes = _normalize_internal_markdown_links(
        "See [Guide](https://macroscope.com/blog/guide) for more context.",
        suggestions=[
            InternalLink(anchor_text="Guide", target_path="/blog/guide", context="intro"),
            InternalLink(anchor_text="Benchmarks", target_path="/blog/benchmarks", context="body"),
            InternalLink(anchor_text="FAQ", target_path="/blog/faq", context="faq"),
        ],
        min_links=3,
    )

    assert repaired.count("[Guide](https://macroscope.com/blog/guide)") == 1
    assert "[Guide]([Guide](/blog/guide))" not in repaired
    assert repaired.count("(/blog/guide)") == 0
    assert any("internal-links section" in note.lower() for note in notes)
