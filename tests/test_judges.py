"""Tests for deterministic judge panels."""

from app.judges import draft_evaluation_from_scores, evaluate_draft_variant, final_quality_jury, topic_jury_scores
from app.qa import QAResult, QACheck
from app.schemas import (
    ArticleManifest,
    DraftVariant,
    FAQ,
    InternalLink,
    JudgeScore,
    OutlineSection,
    ResearchBrief,
    SearchIntent,
    ScoredTopic,
    SEOAEOScore,
    TopicCandidate,
    TopicReuseAssessment,
    TopicScore,
)


def _make_topic() -> ScoredTopic:
    return ScoredTopic(
        candidate=TopicCandidate(
            title="AI Code Review Benchmark for Engineering Teams",
            slug="ai-code-review-benchmark",
            cluster="ai-code-review",
            description="Compares AI code review systems using engineering-facing criteria.",
            target_keywords=["ai code review", "code review benchmark"],
            search_intent=SearchIntent.COMMERCIAL,
            source="test",
            rationale="Strong benchmark angle",
            freshness_signal="New benchmark debate",
        ),
        score=TopicScore(
            business_relevance=22.0,
            search_opportunity=16.0,
            aeo_fit=12.0,
            freshness=8.0,
            authority_to_win=14.0,
            uniqueness_vs_archive=9.0,
            production_ease=4.0,
        ),
    )


def _make_brief() -> ResearchBrief:
    topic = _make_topic().candidate
    return ResearchBrief(
        topic=topic,
        outline=[OutlineSection(heading="One", description="Depth section", target_word_count=200, key_points=["a"]) for _ in range(6)],
        target_word_count=1800,
        primary_keyword="ai code review",
        secondary_keywords=["code review benchmark", "engineering teams"],
        entities=["Macroscope", "GitHub", "GitLab", "benchmarks"],
        faqs=[
            FAQ(question="How do AI code review benchmarks work?", suggested_answer="They compare tools against a defined evaluation set.")
            for _ in range(5)
        ],
        claims_needing_evidence=["Benchmark methodology", "Detection precision"],
        internal_link_suggestions=[
            InternalLink(anchor_text="AI review guide", target_path="/blog/guide", context="intro"),
            InternalLink(anchor_text="PR workflow", target_path="/blog/pr-workflow", context="body"),
            InternalLink(anchor_text="Benchmarking", target_path="/blog/benchmarking", context="faq"),
        ],
        cta="Try Macroscope",
        do_not_say=["guaranteed"],
        meta_description="AI code review benchmark for engineering teams evaluating review quality and workflow fit.",
        title_options=["AI Code Review Benchmark", "Benchmarking AI Code Review"],
    )


def test_topic_jury_scores_return_consensus():
    scores, consensus, variance = topic_jury_scores(
        topic=_make_topic(),
        reuse=TopicReuseAssessment(slug="ai-code-review-benchmark", eligible=True, penalty=1.0),
        keyword_metrics={"ai code review": {"volume": 2400}},
    )

    assert len(scores) == 5
    assert consensus > 7.0
    assert variance >= 0.0


def test_final_quality_jury_builds_panel():
    qa_result = QAResult(checks=[QACheck(name="faq_section", passed=True, message="ok")])
    seo_score = SEOAEOScore(
        title_score=9.0,
        meta_description_score=9.0,
        keyword_density_score=8.0,
        heading_structure_score=9.0,
        internal_links_score=9.0,
        faq_presence_score=9.0,
        direct_answer_score=9.0,
        readability_score=8.0,
        content_depth_score=8.0,
        freshness_signals_score=7.0,
    )
    manifest = ArticleManifest(
        title="AI Code Review",
        slug="ai-code-review",
        primary_keyword="ai code review",
        opening_direct_answer="Direct answer. Another answer.",
        heading_map=["AI Code Review", "Benchmark", "FAQ"],
        faq_questions=["How do AI code review benchmarks work?"],
        internal_links=["/blog/guide", "/blog/faq"],
        claim_candidates=["Benchmark methodology"],
        section_excerpts=[],
        qa_snapshot=qa_result.to_dict(),
        seo_snapshot=seo_score.model_dump(),
        word_count=1200,
        meta_description="AI code review benchmark for engineering teams evaluating review quality and workflow fit.",
    )

    gate = final_quality_jury(
        article_manifest=manifest,
        round_number=1,
    )

    assert len(gate.scores) == 3
    assert gate.average_score > 8.0


def test_draft_evaluation_from_scores_applies_thresholds():
    evaluation = draft_evaluation_from_scores(
        writer_id="technical",
        scores=[
            JudgeScore(judge="technical_accuracy_judge", score=8.8, rationale="Strong"),
            JudgeScore(judge="seo_judge", score=8.4, rationale="Strong"),
            JudgeScore(judge="aeo_judge", score=8.6, rationale="Strong"),
        ],
        min_average_score=8.2,
        min_single_score=7.4,
    )

    assert evaluation.passed is True
    assert evaluation.average_score >= 8.2
    assert evaluation.score_variance >= 0.0


def test_evaluate_draft_variant_uses_configurable_thresholds():
    brief = _make_brief()
    variant = DraftVariant(
        writer_id="technical",
        writer_label="Technical Writer",
        focus_summary="Technical",
        title=brief.title_options[0],
        slug=brief.topic.slug,
        content_md=(
            "# Heading\n\n"
            "Direct answer: teams should benchmark pull request review policies against real failure modes, "
            "then turn those findings into explicit test and approval rules.\n\n"
            "## Why it matters\n\n"
            "This draft explains how engineering leaders can compare review latency, defect escape rate, and "
            "policy exceptions without relying on vague process claims.\n\n"
            "## FAQ\n\n"
            "Q: Should teams change policy after one incident?\n\n"
            "A: No, they should validate patterns across a representative set of pull requests first."
        ),
        word_count=82,
        brief_hash=brief.brief_hash(),
    )
    qa_result = QAResult(checks=[QACheck(name="faq_section", passed=True, message="ok")])
    seo_score = SEOAEOScore(
        title_score=8.0,
        meta_description_score=8.0,
        keyword_density_score=8.0,
        heading_structure_score=8.0,
        internal_links_score=8.0,
        faq_presence_score=8.0,
        direct_answer_score=8.0,
        readability_score=8.0,
        content_depth_score=8.0,
        freshness_signals_score=8.0,
    )

    evaluation = evaluate_draft_variant(
        variant=variant,
        brief=brief,
        qa_result=qa_result,
        seo_score=seo_score,
        min_average_score=9.5,
        min_single_score=9.0,
    )

    assert evaluation.passed is False
    assert "Draft needs stronger optimization before publication" in evaluation.notes
