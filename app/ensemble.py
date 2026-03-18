"""Helpers for fan-out/fan-in ensemble stages."""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from .schemas import (
    DraftEvaluation,
    DraftVariant,
    JudgeScore,
    MarketSignal,
    MarketSignalReport,
    TopicCandidate,
)


def merge_market_signal_reports(reports: list[MarketSignalReport]) -> MarketSignalReport:
    """Merge multiple scout reports into one deduplicated report."""
    deduped: dict[str, MarketSignal] = {}
    trending_themes: list[str] = []
    recommended_angles: list[str] = []
    collected_at = None

    for report in reports:
        if collected_at is None or report.collected_at > collected_at:
            collected_at = report.collected_at
        for signal in report.signals:
            key = (signal.url or signal.title).lower().strip()
            existing = deduped.get(key)
            if existing is None or signal.relevance_score > existing.relevance_score:
                deduped[key] = signal
        for theme in report.trending_themes:
            if theme not in trending_themes:
                trending_themes.append(theme)
        for angle in report.recommended_angles:
            if angle not in recommended_angles:
                recommended_angles.append(angle)

    return MarketSignalReport(
        signals=sorted(
            deduped.values(),
            key=lambda signal: signal.relevance_score,
            reverse=True,
        )[:18],
        trending_themes=trending_themes[:8],
        recommended_angles=recommended_angles[:8],
        collected_at=collected_at or datetime.now(timezone.utc),
    )


def merge_topic_candidates(candidate_batches: list[list[TopicCandidate]]) -> list[TopicCandidate]:
    """Merge topic pools from multiple ideators while deduplicating by slug."""
    deduped: dict[str, TopicCandidate] = {}
    for batch in candidate_batches:
        for candidate in batch:
            existing = deduped.get(candidate.slug)
            if existing is None:
                deduped[candidate.slug] = candidate
                continue
            if len(candidate.description) > len(existing.description):
                deduped[candidate.slug] = candidate
    return list(deduped.values())


def summarize_judge_panel(scores: list[JudgeScore]) -> tuple[float, float]:
    """Return the average and minimum judge scores."""
    if not scores:
        return 0.0, 0.0
    values = [score.score for score in scores]
    return round(mean(values), 2), round(min(values), 2)


def select_best_draft(
    variants: list[DraftVariant],
    evaluations: list[DraftEvaluation],
) -> tuple[DraftVariant, DraftEvaluation]:
    """Select the strongest draft using evaluation score, then minimum score."""
    evaluation_by_writer = {evaluation.writer_id: evaluation for evaluation in evaluations}
    ranked_variants = sorted(
        variants,
        key=lambda variant: (
            evaluation_by_writer[variant.writer_id].average_score,
            evaluation_by_writer[variant.writer_id].min_score,
            variant.word_count,
        ),
        reverse=True,
    )
    best_variant = ranked_variants[0]
    return best_variant, evaluation_by_writer[best_variant.writer_id]
