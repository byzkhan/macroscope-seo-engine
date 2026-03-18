"""Deterministic judge panels for topics, drafts, and final quality gates."""

from __future__ import annotations

import math
import re
from statistics import mean, pvariance

from .qa import QAResult
from .schemas import (
    ArticleManifest,
    DraftEvaluation,
    DraftVariant,
    FinalQualityGate,
    JudgeScore,
    ResearchBrief,
    ScoredTopic,
    SEOAEOScore,
    TopicReuseAssessment,
)


def topic_jury_scores(
    *,
    topic: ScoredTopic,
    reuse: TopicReuseAssessment,
    keyword_metrics: dict[str, dict[str, float | int | str]],
) -> tuple[list[JudgeScore], float, float]:
    """Score a topic through multiple specialized judges."""
    average_volume = 0.0
    if topic.candidate.target_keywords:
        volumes = [
            float(keyword_metrics.get(keyword, {}).get("volume", 300))
            for keyword in topic.candidate.target_keywords
        ]
        average_volume = sum(volumes) / len(volumes)

    scores = [
        JudgeScore(
            judge="seo_opportunity_judge",
            score=_clamp(5.5 + min(3.0, math.log10(max(average_volume, 10.0)) - 1.5)),
            rationale="Evaluates keyword demand and ranking opportunity.",
            notes=[f"Average estimated volume {average_volume:.0f}"],
        ),
        JudgeScore(
            judge="technical_authority_judge",
            score=_authority_score(topic.candidate.cluster, topic.candidate.title),
            rationale="Checks whether Macroscope can credibly own the topic.",
            notes=[f"Cluster: {topic.candidate.cluster}"],
        ),
        JudgeScore(
            judge="freshness_relevance_judge",
            score=_clamp(6.0 + (1.8 if topic.candidate.freshness_signal else 0.3)),
            rationale="Rewards timely, discussion-backed topics.",
            notes=[topic.candidate.freshness_signal or "No explicit freshness signal"],
        ),
        JudgeScore(
            judge="commercial_value_judge",
            score=_clamp(
                6.4
                + (1.6 if topic.candidate.search_intent.value in {"commercial", "transactional"} else 0.4)
            ),
            rationale="Estimates conversion and buyer-research value.",
            notes=[f"Intent: {topic.candidate.search_intent.value}"],
        ),
        JudgeScore(
            judge="originality_judge",
            score=_clamp(9.5 - reuse.penalty),
            rationale="Penalizes archive overlap and over-familiar framing.",
            notes=reuse.reasons or ["No significant overlap detected"],
        ),
    ]
    score_values = [score.score for score in scores]
    return scores, round(mean(score_values), 2), round(pvariance(score_values), 3)


def evaluate_brief_quality(brief: ResearchBrief) -> tuple[float, list[str]]:
    """Heuristically score a research brief before writing."""
    score = 6.5
    notes: list[str] = []
    if len(brief.outline) >= 6:
        score += 1.0
    else:
        notes.append("Outline is shorter than preferred")
    if len(brief.faqs) >= 5:
        score += 0.8
    else:
        notes.append("FAQ coverage is light")
    if len(brief.claims_needing_evidence) >= 3:
        score += 0.7
    else:
        notes.append("Brief should call out more evidence-dependent claims")
    if len(brief.internal_link_suggestions) >= 3:
        score += 0.6
    else:
        notes.append("Brief needs a stronger internal linking plan")
    if len(brief.entities) >= 4:
        score += 0.5
    else:
        notes.append("Entity coverage is thin")
    return _clamp(score), notes


def evaluate_draft_variant(
    *,
    variant: DraftVariant,
    brief: ResearchBrief,
    qa_result: QAResult,
    seo_score: SEOAEOScore,
    min_average_score: float = 8.2,
    min_single_score: float = 7.4,
) -> DraftEvaluation:
    """Score one writer draft before optimization."""
    content_lower = variant.content_md.lower()
    keyword_hits = sum(1 for keyword in [brief.primary_keyword, *brief.secondary_keywords[:3]] if keyword.lower() in content_lower)
    technical_markers = sum(
        1
        for marker in ("pull request", "ci", "lint", "test", "diff", "benchmark", "policy", "error")
        if marker in content_lower
    )
    scores = [
        JudgeScore(
            judge="technical_accuracy_judge",
            score=_clamp(6.8 + technical_markers * 0.35),
            rationale="Rewards engineering-specific detail and concrete terminology.",
        ),
        JudgeScore(
            judge="seo_judge",
            score=_clamp(5.8 + seo_score.total / 22.0),
            rationale="Assesses on-page SEO fundamentals from the current draft.",
        ),
        JudgeScore(
            judge="aeo_judge",
            score=_clamp(6.0 + seo_score.direct_answer_score * 0.25 + seo_score.faq_presence_score * 0.2),
            rationale="Assesses answer-engine readiness and snippet potential.",
        ),
        JudgeScore(
            judge="clarity_judge",
            score=_clamp(7.2 if qa_result.passed else 6.3),
            rationale="Uses QA pass state and structure as a clarity proxy.",
        ),
        JudgeScore(
            judge="evidence_completeness_judge",
            score=_clamp(6.5 + min(2.0, keyword_hits * 0.35) + (0.5 if len(brief.claims_needing_evidence) >= 3 else 0.0)),
            rationale="Checks whether the draft appears to cover the brief and proof points.",
        ),
    ]
    values = [score.score for score in scores]
    average_score = round(mean(values), 2)
    min_score = round(min(values), 2)
    score_variance = round(pvariance(values), 3) if len(values) > 1 else 0.0
    notes: list[str] = []
    if average_score < min_average_score:
        notes.append("Draft needs stronger optimization before publication")
    if min_score < min_single_score:
        notes.append("At least one quality dimension is too weak")
    return DraftEvaluation(
        writer_id=variant.writer_id,
        scores=scores,
        average_score=average_score,
        min_score=min_score,
        score_variance=score_variance,
        passed=average_score >= min_average_score and min_score >= min_single_score,
        notes=notes,
    )


def draft_evaluation_from_scores(
    *,
    writer_id: str,
    scores: list[JudgeScore],
    min_average_score: float,
    min_single_score: float,
) -> DraftEvaluation:
    """Build a DraftEvaluation from precomputed judge scores."""
    values = [score.score for score in scores]
    average_score = round(mean(values), 2) if values else 0.0
    min_score = round(min(values), 2) if values else 0.0
    score_variance = round(pvariance(values), 3) if len(values) > 1 else 0.0
    notes: list[str] = []
    if average_score < min_average_score:
        notes.append("Draft needs stronger optimization before publication")
    if min_score < min_single_score:
        notes.append("At least one quality dimension is too weak")
    return DraftEvaluation(
        writer_id=writer_id,
        scores=scores,
        average_score=average_score,
        min_score=min_score,
        score_variance=score_variance,
        passed=average_score >= min_average_score and min_score >= min_single_score,
        notes=notes,
    )


def final_quality_jury(
    *,
    article_manifest: ArticleManifest,
    round_number: int,
) -> FinalQualityGate:
    """Final publish gate using multiple specialized quality judges."""
    seo_snapshot = article_manifest.seo_snapshot
    qa_snapshot = article_manifest.qa_snapshot
    link_count = len(article_manifest.internal_links)
    heading_count = len([heading for heading in article_manifest.heading_map if heading])
    claim_count = len(article_manifest.claim_candidates)

    scores = [
        JudgeScore(
            judge="search_readiness_judge",
            score=_clamp(
                6.9
                + float(seo_snapshot.get("direct_answer_score", 0.0)) * 0.17
                + float(seo_snapshot.get("meta_description_score", 0.0)) * 0.1
                + float(seo_snapshot.get("internal_links_score", 0.0)) * 0.08
            ),
            rationale="Checks whether the article is ready for search and answer engines.",
        ),
        JudgeScore(
            judge="structure_clarity_judge",
            score=_clamp(
                6.8
                + min(1.5, heading_count * 0.18)
                + float(seo_snapshot.get("faq_presence_score", 0.0)) * 0.08
                + (0.5 if qa_snapshot.get("passed") else 0.0)
            ),
            rationale="Checks structure, clarity, FAQ usefulness, and navigability.",
        ),
        JudgeScore(
            judge="technical_rigor_judge",
            score=_clamp(
                8.0
                + min(1.2, claim_count * 0.12)
                + (0.5 if float(seo_snapshot.get("content_depth_score", 0.0)) >= 8.0 else 0.0)
                + (0.3 if float(seo_snapshot.get("direct_answer_score", 0.0)) >= 8.0 else 0.0)
            ),
            rationale="Rewards technically grounded engineering content.",
        ),
    ]
    values = [score.score for score in scores]
    technical_accuracy_score = next(
        score.score for score in scores if score.judge == "technical_rigor_judge"
    )
    score_variance = round(pvariance(values), 3) if len(values) > 1 else 0.0
    notes: list[str] = []
    if not qa_snapshot.get("passed", False):
        notes.append("QA still reports blocking failures")
    if link_count < 2:
        notes.append("Internal linking is still too sparse")

    return FinalQualityGate(
        round_number=round_number,
        scores=scores,
        average_score=round(mean(values), 2),
        min_score=round(min(values), 2),
        score_variance=score_variance,
        technical_accuracy_score=round(technical_accuracy_score, 2),
        passed=bool(qa_snapshot.get("passed", False)),
        notes=notes,
    )


def _clamp(value: float, minimum: float = 0.0, maximum: float = 10.0) -> float:
    return round(max(minimum, min(value, maximum)), 2)


def _authority_score(cluster: str, title: str) -> float:
    cluster_bonus = {
        "ai-code-review": 8.9,
        "security-in-review": 8.4,
        "pr-workflows": 8.3,
        "engineering-productivity": 8.0,
        "devops-ci-cd": 7.7,
        "code-quality": 7.8,
    }.get(cluster, 7.4)
    if "ai code review" in title.lower():
        cluster_bonus += 0.4
    return _clamp(cluster_bonus)
