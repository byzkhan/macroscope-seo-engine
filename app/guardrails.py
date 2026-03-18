"""Run-scoped guardrails and quality policy helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import EngineConfig
from .schemas import QualityPolicy, RunContext, SourcePolicy

RESEARCH_SOURCE_CLASSES = [
    "official_docs",
    "engineering_blog",
    "community_discussion",
    "market_announcement",
    "serp_signal",
    "benchmark_or_paper",
]

DEFAULT_AGENT_MANIFEST = [
    "run_bootstrapper",
    "community_scout",
    "primary_source_scout",
    "practitioner_scout",
    "market_gap_researcher",
    "engineering_pain_researcher",
    "technical_depth_researcher",
    "seo_opportunity_judge",
    "technical_authority_judge",
    "freshness_relevance_judge",
    "commercial_value_judge",
    "originality_judge",
    "topic_tiebreaker_judge",
    "brief_composer",
    "brief_critic",
    "technical_writer",
    "pragmatic_writer",
    "analytical_writer",
    "optimization_coordinator",
    "search_readiness_judge",
    "structure_clarity_judge",
    "technical_rigor_judge",
    "draft_tiebreaker_judge",
    "final_tiebreaker_judge",
    "final_fact_checker",
]


def build_quality_policy(config: EngineConfig) -> QualityPolicy:
    """Construct the run quality policy from configuration."""
    return QualityPolicy(
        min_topic_consensus_score=config.min_topic_consensus_score,
        min_topic_authority_score=config.min_topic_authority_score,
        min_brief_quality_score=config.min_brief_quality_score,
        min_draft_quality_score=config.min_draft_quality_score,
        final_average_score=config.final_jury_average_threshold,
        final_min_judge_score=config.final_jury_min_threshold,
        final_technical_accuracy_score=config.technical_accuracy_threshold,
        topic_judge_spread_threshold=config.topic_judge_spread_threshold,
        topic_judge_variance_threshold=config.topic_judge_variance_threshold,
        draft_judge_spread_threshold=config.draft_judge_spread_threshold,
        draft_judge_variance_threshold=config.draft_judge_variance_threshold,
        final_judge_spread_threshold=config.final_judge_spread_threshold,
        final_judge_variance_threshold=config.final_judge_variance_threshold,
        max_optimization_rounds=config.optimizer_max_rounds,
    )


def build_source_policy(config: EngineConfig) -> SourcePolicy:
    """Construct the run source policy from configuration."""
    return SourcePolicy(
        required_source_classes=RESEARCH_SOURCE_CLASSES,
        minimum_unique_classes=4,
        require_primary_technical_source=True,
        lookback_days=config.research_lookback_days,
    )


def build_run_context(config: EngineConfig, run_id: str) -> RunContext:
    """Create the immutable context object for one pipeline run."""
    config_snapshot: dict[str, Any] = {
        "provider_mode": config.provider_mode,
        "topic_cooldown_days": config.topic_cooldown_days,
        "research_lookback_days": config.research_lookback_days,
        "writer_personas": list(config.writer_personas),
        "optimizer_personas": list(config.optimizer_personas),
        "min_word_count": config.min_word_count,
        "max_word_count": config.max_word_count,
        "min_internal_links": config.min_internal_links,
        "min_faq_count": config.min_faq_count,
        "max_topic_candidates": config.max_topic_candidates,
        "model_judged_topics": config.model_judged_topics,
        "full_panel_topics": config.full_panel_topics,
        "writer_blueprints": config.writer_blueprints,
        "full_draft_candidates": config.full_draft_candidates,
        "second_draft_unlock_round": config.second_draft_unlock_round,
        "enable_final_fact_check": config.enable_final_fact_check,
        "web_search_stages": list(config.web_search_stages),
        "topic_judge_spread_threshold": config.topic_judge_spread_threshold,
        "topic_judge_variance_threshold": config.topic_judge_variance_threshold,
        "draft_judge_spread_threshold": config.draft_judge_spread_threshold,
        "draft_judge_variance_threshold": config.draft_judge_variance_threshold,
        "final_judge_spread_threshold": config.final_judge_spread_threshold,
        "final_judge_variance_threshold": config.final_judge_variance_threshold,
        "openai_model": config.openai_model,
        "openai_market_model": config.openai_market_model,
        "openai_content_model": config.openai_content_model,
    }
    return RunContext(
        run_id=run_id,
        run_started_at=datetime.now(timezone.utc),
        provider_mode=config.provider_mode,
        config_snapshot=config_snapshot,
        quality_policy=build_quality_policy(config),
        source_policy=build_source_policy(config),
        agent_manifest=list(DEFAULT_AGENT_MANIFEST),
    )


def is_stateless_run_context(run_context: RunContext) -> bool:
    """Simple marker that the run is configured for stateless subagent calls."""
    return bool(run_context.agent_manifest and run_context.provider_mode)


def quality_gate_passed(
    *,
    average_score: float,
    min_score: float,
    technical_accuracy_score: float,
    policy: QualityPolicy,
) -> tuple[bool, list[str]]:
    """Evaluate whether the final jury scores satisfy the publish gate."""
    notes: list[str] = []
    if average_score < policy.final_average_score:
        notes.append(
            f"Average jury score {average_score:.2f} below {policy.final_average_score:.2f}"
        )
    if min_score < policy.final_min_judge_score:
        notes.append(
            f"One or more judges scored {min_score:.2f}, below the floor of {policy.final_min_judge_score:.2f}"
        )
    if technical_accuracy_score < policy.final_technical_accuracy_score:
        notes.append(
            "Technical accuracy score below the required publication threshold"
        )
    return (len(notes) == 0, notes)
