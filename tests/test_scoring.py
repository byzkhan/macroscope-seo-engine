"""Tests for the topic scoring engine."""

import json
from pathlib import Path

import pytest

from app.schemas import SearchIntent, ScoredTopic, TopicCandidate, TopicScore
from app.scoring import (
    MIN_TOTAL_SCORE,
    SCORING_WEIGHTS,
    check_archive_similarity,
    check_generic,
    rank_topics,
    score_topic,
    select_best,
)


@pytest.fixture
def sample_candidate() -> TopicCandidate:
    return TopicCandidate(
        title="How AI Code Review Catches Logic Bugs in Pull Requests",
        slug="ai-code-review-logic-bugs",
        cluster="ai-code-review",
        description="Explores how AI-powered code review identifies logic errors that traditional linters miss.",
        target_keywords=["ai code review", "logic bugs", "pull request review"],
        search_intent=SearchIntent.INFORMATIONAL,
        source="topic-researcher",
        rationale="High search volume for AI code review, unique angle on logic bugs",
    )


@pytest.fixture
def archive_file(tmp_path: Path) -> Path:
    archive = [
        {
            "slug": "ai-code-review-best-practices",
            "title": "AI Code Review Best Practices for Engineering Teams",
            "keywords": ["ai code review", "automated code review", "code quality"],
            "published_at": "2026-02-15",
            "cluster": "ai-code-review",
        },
        {
            "slug": "reducing-pr-cycle-time",
            "title": "How to Reduce PR Cycle Time Without Sacrificing Quality",
            "keywords": ["pr cycle time", "pull request workflow"],
            "published_at": "2026-02-22",
            "cluster": "pr-workflows",
        },
    ]
    path = tmp_path / "topic_history.json"
    path.write_text(json.dumps(archive))
    return path


@pytest.fixture
def good_raw_scores() -> dict[str, float]:
    return {
        "business_relevance": 22.0,
        "search_opportunity": 16.0,
        "aeo_fit": 12.0,
        "freshness": 7.0,
        "authority_to_win": 13.0,
        "production_ease": 4.0,
    }


class TestScoringWeights:
    def test_weights_sum_to_100(self):
        assert sum(SCORING_WEIGHTS.values()) == 100.0

    def test_all_weights_positive(self):
        for name, weight in SCORING_WEIGHTS.items():
            assert weight > 0, f"{name} weight must be positive"

    def test_min_score_is_reasonable(self):
        assert 30 <= MIN_TOTAL_SCORE <= 70


class TestCheckGeneric:
    def test_generic_title_detected(self):
        candidate = TopicCandidate(
            title="Introduction to Code Review for Developers",
            slug="intro-code-review",
            cluster="ai-code-review",
            description="A beginner's guide to understanding code review processes and tools.",
            target_keywords=["code review", "beginner guide"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="test",
            rationale="Testing generic detection logic here",
        )
        reasons = check_generic(candidate)
        assert len(reasons) > 0
        assert any("introduction to" in r.lower() for r in reasons)

    def test_specific_title_passes(self, sample_candidate):
        reasons = check_generic(sample_candidate)
        assert len(reasons) == 0

    def test_too_few_keywords_rejected_at_schema_level(self):
        """Pydantic enforces min_length=2 on target_keywords before check_generic runs."""
        with pytest.raises(Exception):
            TopicCandidate(
                title="Advanced Race Condition Detection in Go Code Reviews",
                slug="race-conditions-go-review",
                cluster="ai-code-review",
                description="Deep dive into race condition detection during automated code review.",
                target_keywords=["race conditions"],
                search_intent=SearchIntent.INFORMATIONAL,
                source="test",
                rationale="Testing keyword count requirement",
            )

    def test_multiple_generic_indicators(self):
        candidate = TopicCandidate(
            title="Getting Started with Understanding Code Review Basics",
            slug="getting-started-code-review",
            cluster="code-quality",
            description="Overview covering the basics of getting started with code review.",
            target_keywords=["code review", "getting started"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="test",
            rationale="Testing multiple generic indicators",
        )
        reasons = check_generic(candidate)
        assert len(reasons) >= 2


class TestArchiveSimilarity:
    def test_exact_slug_match_rejected(self):
        candidate = TopicCandidate(
            title="AI Code Review Best Practices Updated for Teams",
            slug="ai-code-review-best-practices",
            cluster="ai-code-review",
            description="Updated guide to AI code review best practices for modern teams.",
            target_keywords=["ai code review", "best practices"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="test",
            rationale="Testing slug match detection logic",
        )
        archive = [{"slug": "ai-code-review-best-practices", "title": "Old Title", "keywords": []}]
        score, reasons = check_archive_similarity(candidate, archive)
        assert score == 0.0
        assert len(reasons) > 0
        assert "already exists" in reasons[0]

    def test_unique_topic_scores_high(self, sample_candidate):
        archive = [{"slug": "completely-different", "title": "Unrelated Topic", "keywords": ["unrelated"]}]
        score, reasons = check_archive_similarity(sample_candidate, archive)
        assert score == 10.0
        assert len(reasons) == 0

    def test_empty_archive_scores_max(self, sample_candidate):
        score, reasons = check_archive_similarity(sample_candidate, [])
        assert score == 10.0

    def test_high_title_overlap_penalized(self):
        candidate = TopicCandidate(
            title="AI Code Review Best Practices for Teams",
            slug="ai-review-practices-teams",
            cluster="ai-code-review",
            description="Very similar to existing article about AI code review practices.",
            target_keywords=["ai code review", "best practices"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="test",
            rationale="Testing title overlap detection logic",
        )
        archive = [
            {"slug": "other-slug", "title": "AI Code Review Best Practices for Engineering Teams", "keywords": []}
        ]
        score, reasons = check_archive_similarity(candidate, archive)
        assert score <= 5.0

    def test_high_keyword_overlap_penalized(self):
        candidate = TopicCandidate(
            title="Completely Different Title About Something",
            slug="different-title-something",
            cluster="ai-code-review",
            description="Different title but same keywords as archived content.",
            target_keywords=["ai code review", "automated code review", "code quality"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="test",
            rationale="Testing keyword overlap detection logic",
        )
        archive = [
            {
                "slug": "old-article",
                "title": "Old Article",
                "keywords": ["ai code review", "automated code review", "code quality"],
            }
        ]
        score, reasons = check_archive_similarity(candidate, archive)
        assert score <= 3.0


class TestScoreTopic:
    def test_good_topic_selected(self, sample_candidate, good_raw_scores, archive_file):
        result = score_topic(sample_candidate, good_raw_scores, archive_file)
        assert result.selected is True
        assert result.total_score > MIN_TOTAL_SCORE
        assert len(result.rejection_reasons) == 0

    def test_low_scores_rejected(self, sample_candidate, archive_file):
        low_scores = {k: 1.0 for k in SCORING_WEIGHTS if k != "uniqueness_vs_archive"}
        result = score_topic(sample_candidate, low_scores, archive_file)
        assert result.selected is False
        assert any("below minimum" in r for r in result.rejection_reasons)

    def test_scores_clamped_to_max(self, sample_candidate, archive_file):
        over_scores = {k: 999.0 for k in SCORING_WEIGHTS if k != "uniqueness_vs_archive"}
        result = score_topic(sample_candidate, over_scores, archive_file)
        assert result.score.business_relevance <= 25.0
        assert result.score.search_opportunity <= 20.0
        assert result.score.production_ease <= 5.0

    def test_missing_scores_default_zero(self, sample_candidate, archive_file):
        result = score_topic(sample_candidate, {}, archive_file)
        assert result.score.business_relevance == 0.0
        assert result.score.search_opportunity == 0.0

    def test_nonexistent_archive_file(self, sample_candidate, good_raw_scores, tmp_path):
        path = tmp_path / "nonexistent.json"
        result = score_topic(sample_candidate, good_raw_scores, path)
        assert result.score.uniqueness_vs_archive == 10.0


class TestRankAndSelect:
    def _make_scored(self, slug: str, br: float, so: float, selected: bool = True) -> ScoredTopic:
        return ScoredTopic(
            candidate=TopicCandidate(
                title=f"Topic About {slug.replace('-', ' ').title()}",
                slug=slug,
                cluster="ai-code-review",
                description=f"Description for topic {slug} with enough content.",
                target_keywords=["kw1", "kw2"],
                search_intent=SearchIntent.INFORMATIONAL,
                source="test",
                rationale="Testing ranking behavior works correctly",
            ),
            score=TopicScore(
                business_relevance=br,
                search_opportunity=so,
                aeo_fit=10.0,
                freshness=5.0,
                authority_to_win=10.0,
                uniqueness_vs_archive=8.0,
                production_ease=4.0,
            ),
            selected=selected,
        )

    def test_rank_by_score_descending(self):
        topics = [
            self._make_scored("low-topic", 5.0, 5.0),
            self._make_scored("high-topic", 25.0, 20.0),
            self._make_scored("mid-topic", 15.0, 12.0),
        ]
        ranked = rank_topics(topics)
        assert ranked[0].candidate.slug == "high-topic"
        assert ranked[-1].candidate.slug == "low-topic"

    def test_select_best_skips_rejected(self):
        high_rejected = self._make_scored("rejected", 25.0, 20.0, selected=False)
        high_rejected.rejection_reasons = ["duplicate slug"]
        lower_valid = self._make_scored("valid", 15.0, 12.0, selected=True)

        best = select_best([high_rejected, lower_valid])
        assert best is not None
        assert best.candidate.slug == "valid"

    def test_select_best_returns_none_if_all_rejected(self):
        rejected = self._make_scored("only", 5.0, 5.0, selected=False)
        rejected.rejection_reasons = ["too low"]
        assert select_best([rejected]) is None

    def test_empty_list(self):
        assert select_best([]) is None
