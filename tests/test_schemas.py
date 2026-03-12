"""Tests for Pydantic schema validation."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import (
    FAQ,
    DraftArticle,
    FinalArticle,
    InternalLink,
    MarketSignal,
    MarketSignalReport,
    OutlineSection,
    ResearchBrief,
    RunSummary,
    SEOAEOScore,
    ScoredTopic,
    SearchIntent,
    StageResult,
    TopicCandidate,
    TopicScore,
    FailureCategory,
)


class TestTopicCandidate:
    def test_valid_candidate(self):
        tc = TopicCandidate(
            title="How AI Code Review Catches Logic Bugs",
            slug="ai-code-review-logic-bugs",
            cluster="ai-code-review",
            description="Explores AI detection of logic errors in PRs and how it compares to linters.",
            target_keywords=["ai code review", "logic bugs"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="High search intent with clear content angle",
        )
        assert tc.slug == "ai-code-review-logic-bugs"
        assert tc.search_intent == SearchIntent.INFORMATIONAL

    def test_invalid_slug_rejected(self):
        with pytest.raises(ValidationError):
            TopicCandidate(
                title="Valid Title for Testing Purposes",
                slug="Invalid Slug With Spaces",
                cluster="ai-code-review",
                description="Description that is long enough to pass validation.",
                target_keywords=["kw1", "kw2"],
                search_intent=SearchIntent.INFORMATIONAL,
                source="test",
                rationale="Testing slug validation logic",
            )

    def test_title_too_short(self):
        with pytest.raises(ValidationError):
            TopicCandidate(
                title="Short",
                slug="valid-slug",
                cluster="ai-code-review",
                description="Description that is long enough to pass validation.",
                target_keywords=["kw1", "kw2"],
                search_intent=SearchIntent.INFORMATIONAL,
                source="test",
                rationale="Testing title length validation",
            )

    def test_empty_keywords_rejected(self):
        with pytest.raises(ValidationError):
            TopicCandidate(
                title="Valid Title for Testing Here Now",
                slug="valid-slug",
                cluster="ai-code-review",
                description="Description that is long enough to pass validation.",
                target_keywords=[],
                search_intent=SearchIntent.INFORMATIONAL,
                source="test",
                rationale="Testing empty keywords validation",
            )

    def test_single_keyword_rejected(self):
        """min_length=2 on target_keywords."""
        with pytest.raises(ValidationError):
            TopicCandidate(
                title="Valid Title for Testing Purposes",
                slug="valid-slug",
                cluster="ai-code-review",
                description="Description that is long enough to pass validation.",
                target_keywords=["only-one"],
                search_intent=SearchIntent.INFORMATIONAL,
                source="test",
                rationale="Testing single keyword validation",
            )

    def test_keywords_normalized_to_lowercase(self):
        tc = TopicCandidate(
            title="Title That Is Long Enough for Testing",
            slug="test-slug",
            cluster="ai-code-review",
            description="Description that is long enough to pass validation.",
            target_keywords=["AI Code Review", "Logic BUGS"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="test",
            rationale="Testing keyword normalization logic",
        )
        assert tc.target_keywords == ["ai code review", "logic bugs"]


class TestTopicScore:
    def test_total_computed(self):
        score = TopicScore(
            business_relevance=20.0,
            search_opportunity=15.0,
            aeo_fit=10.0,
            freshness=8.0,
            authority_to_win=12.0,
            uniqueness_vs_archive=9.0,
            production_ease=4.0,
        )
        assert score.total == 78.0

    def test_max_score_is_100(self):
        score = TopicScore(
            business_relevance=25.0,
            search_opportunity=20.0,
            aeo_fit=15.0,
            freshness=10.0,
            authority_to_win=15.0,
            uniqueness_vs_archive=10.0,
            production_ease=5.0,
        )
        assert score.total == 100.0

    def test_score_exceeding_max_rejected(self):
        with pytest.raises(ValidationError):
            TopicScore(
                business_relevance=30.0,
                search_opportunity=15.0,
                aeo_fit=10.0,
                freshness=8.0,
                authority_to_win=12.0,
                uniqueness_vs_archive=9.0,
                production_ease=4.0,
            )

    def test_negative_score_rejected(self):
        with pytest.raises(ValidationError):
            TopicScore(
                business_relevance=-1.0,
                search_opportunity=15.0,
                aeo_fit=10.0,
                freshness=8.0,
                authority_to_win=12.0,
                uniqueness_vs_archive=9.0,
                production_ease=4.0,
            )


class TestSEOAEOScore:
    def test_total_and_normalized(self):
        score = SEOAEOScore(
            title_score=8.0,
            meta_description_score=7.0,
            keyword_density_score=6.0,
            heading_structure_score=9.0,
            internal_links_score=5.0,
            faq_presence_score=10.0,
            direct_answer_score=8.0,
            readability_score=7.0,
            content_depth_score=8.0,
            freshness_signals_score=6.0,
        )
        assert score.total == 74.0
        assert score.normalized == pytest.approx(0.74)

    def test_grade_assignment(self):
        high = SEOAEOScore(**{f: 9.0 for f in [
            "title_score", "meta_description_score", "keyword_density_score",
            "heading_structure_score", "internal_links_score", "faq_presence_score",
            "direct_answer_score", "readability_score", "content_depth_score",
            "freshness_signals_score",
        ]})
        assert high.grade == "A"

        low = SEOAEOScore(**{f: 2.0 for f in [
            "title_score", "meta_description_score", "keyword_density_score",
            "heading_structure_score", "internal_links_score", "faq_presence_score",
            "direct_answer_score", "readability_score", "content_depth_score",
            "freshness_signals_score",
        ]})
        assert low.grade == "F"


class TestRunSummary:
    def test_success_when_complete_no_errors(self):
        summary = RunSummary(
            run_id="test-run",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        assert summary.success is True

    def test_failure_when_errors(self):
        summary = RunSummary(
            run_id="test-run",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            errors=["something failed"],
        )
        assert summary.success is False

    def test_failure_when_not_completed(self):
        summary = RunSummary(
            run_id="test-run",
            started_at=datetime.now(timezone.utc),
        )
        assert summary.success is False

    def test_stages_completed_and_failed(self):
        summary = RunSummary(
            run_id="test-run",
            started_at=datetime.now(timezone.utc),
            stages=[
                StageResult(stage="collect_signals", success=True, duration_seconds=1.0),
                StageResult(stage="generate_topics", success=True, duration_seconds=2.0),
                StageResult(stage="score_topics", success=False, duration_seconds=0.5, error="fail"),
            ],
        )
        assert summary.stages_completed == ["collect_signals", "generate_topics"]
        assert summary.stages_failed == ["score_topics"]

    def test_concise_json(self):
        summary = RunSummary(
            run_id="test-run",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            topic_selected="Test Topic",
            final_score=75.0,
            final_grade="B",
            word_count=1800,
        )
        cj = summary.to_concise_json()
        assert cj["success"] is True
        assert cj["topic"] == "Test Topic"
        assert cj["grade"] == "B"


class TestResearchBrief:
    def _make_topic(self) -> TopicCandidate:
        return TopicCandidate(
            title="Test Topic About AI Code Review",
            slug="test-topic-ai-review",
            cluster="ai-code-review",
            description="A test topic with enough description length for validation.",
            target_keywords=["ai code review", "testing"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="test",
            rationale="Testing brief validation works correctly",
        )

    def test_valid_brief(self):
        brief = ResearchBrief(
            topic=self._make_topic(),
            outline=[
                OutlineSection(heading="Intro", description="Introduction section content", key_points=["p1"]),
                OutlineSection(heading="Main Content", description="Main body section content", key_points=["p2"]),
                OutlineSection(heading="Conclusion", description="Closing section content", key_points=["p3"]),
            ],
            primary_keyword="ai code review",
            secondary_keywords=["automated review", "code quality"],
            entities=["Macroscope", "GitHub"],
            faqs=[
                FAQ(question="What is AI code review exactly?", suggested_answer="AI code review uses ML models to analyze code and find bugs."),
                FAQ(question="How accurate is AI review?", suggested_answer="Accuracy depends on the model, context, and codebase complexity."),
                FAQ(question="Does it replace human reviewers?", suggested_answer="No, it augments human reviewers by catching what they miss."),
                FAQ(question="What bugs does AI catch?", suggested_answer="Logic errors, security issues, race conditions, and type confusion."),
            ],
            claims_needing_evidence=["reduces review time"],
            internal_link_suggestions=[
                InternalLink(anchor_text="AI code review", target_path="/ai-code-review", context="intro"),
            ],
            cta="Try Macroscope free today",
            do_not_say=["revolutionary"],
            meta_description="Learn how AI code review catches bugs that linters miss in your PRs.",
            title_options=["AI Code Review Guide", "How AI Catches Bugs in Code"],
        )
        assert brief.target_word_count == 2000
        assert len(brief.faqs) == 4

    def test_brief_requires_min_faqs(self):
        with pytest.raises(ValidationError):
            ResearchBrief(
                topic=self._make_topic(),
                outline=[
                    OutlineSection(heading="H1", description="Description here"),
                    OutlineSection(heading="H2", description="Description here"),
                    OutlineSection(heading="H3", description="Description here"),
                ],
                primary_keyword="kw",
                secondary_keywords=["s1", "s2"],
                entities=["E"],
                faqs=[FAQ(question="Only one question here?", suggested_answer="Only one answer here for testing.")],
                claims_needing_evidence=[],
                internal_link_suggestions=[],
                cta="Try it now please",
                do_not_say=["x"],
                meta_description="Meta description that has enough length for validation to pass.",
                title_options=["T1", "T2"],
            )

    def test_brief_hash_deterministic(self):
        topic = self._make_topic()
        brief = ResearchBrief(
            topic=topic,
            outline=[
                OutlineSection(heading="H1", description="Desc here"),
                OutlineSection(heading="H2", description="Desc here"),
                OutlineSection(heading="H3", description="Desc here"),
            ],
            primary_keyword="kw",
            secondary_keywords=["s1", "s2"],
            entities=["E"],
            faqs=[
                FAQ(question=f"Question number {i} here?", suggested_answer=f"Answer number {i} with enough text.")
                for i in range(4)
            ],
            claims_needing_evidence=[],
            internal_link_suggestions=[],
            cta="Try it today",
            do_not_say=["x"],
            meta_description="Meta description that has enough length for validation to pass.",
            title_options=["T1", "T2"],
        )
        h1 = brief.brief_hash()
        h2 = brief.brief_hash()
        assert h1 == h2
        assert len(h1) == 12
