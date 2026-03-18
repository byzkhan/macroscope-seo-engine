"""Tests for topic cooldown and shortlist history behavior."""

from pathlib import Path

from app.history import (
    assess_topic_reuse,
    load_topic_cooldowns,
    load_topic_shortlist_history,
    record_topic_shortlist,
    upsert_topic_cooldown,
)


def test_upsert_topic_cooldown_creates_record(tmp_path: Path):
    upsert_topic_cooldown(
        tmp_path,
        slug="ai-code-review-benchmark",
        title="AI Code Review Benchmark",
        cluster="ai-code-review",
        keywords=["ai code review", "benchmark"],
        cooldown_days=30,
    )

    records = load_topic_cooldowns(tmp_path)
    assert len(records) == 1
    assert records[0].slug == "ai-code-review-benchmark"


def test_assess_topic_reuse_blocks_active_cooldown(tmp_path: Path):
    upsert_topic_cooldown(
        tmp_path,
        slug="ai-code-review-benchmark",
        title="AI Code Review Benchmark",
        cluster="ai-code-review",
        keywords=["ai code review", "benchmark"],
        cooldown_days=30,
    )

    records = load_topic_cooldowns(tmp_path)
    assessment = assess_topic_reuse(
        slug="ai-code-review-benchmark",
        title="AI Code Review Benchmark",
        cluster="ai-code-review",
        keywords=["ai code review", "benchmark"],
        cooldowns=records,
        shortlist_records=[],
    )

    assert assessment.eligible is False
    assert assessment.penalty == 10.0


def test_shortlisted_topics_remain_eligible(tmp_path: Path):
    record_topic_shortlist(
        tmp_path,
        run_id="run-1",
        shortlisted_topics=[
            {
                "slug": "semantic-review-monorepos",
                "title": "Semantic Review in Monorepos",
                "cluster": "ai-code-review",
                "keywords": ["semantic review", "monorepos"],
            }
        ],
    )
    shortlist_history = load_topic_shortlist_history(tmp_path)

    assessment = assess_topic_reuse(
        slug="semantic-review-monorepos",
        title="Semantic Review in Monorepos",
        cluster="ai-code-review",
        keywords=["semantic review", "monorepos"],
        cooldowns=[],
        shortlist_records=shortlist_history,
    )

    assert assessment.eligible is True
    assert assessment.penalty >= 1.0
