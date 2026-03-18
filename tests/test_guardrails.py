"""Tests for run guardrails and quality policy helpers."""

from pathlib import Path

from app.config import EngineConfig
from app.guardrails import build_run_context, quality_gate_passed


def _make_config(**overrides) -> EngineConfig:
    base = EngineConfig(
        project_root=Path("/tmp/project"),
        config_dir=Path("/tmp/project/config"),
        data_dir=Path("/tmp/project/data"),
    )
    return EngineConfig(**{**base.__dict__, **overrides})


def test_build_run_context_uses_fresh_policy():
    config = _make_config(provider_mode="openai")
    run_context = build_run_context(config, "run-123")

    assert run_context.run_id == "run-123"
    assert run_context.provider_mode == "openai"
    assert run_context.quality_policy.final_average_score == 9.0
    assert run_context.quality_policy.topic_judge_spread_threshold == 1.5
    assert "community_scout" in run_context.agent_manifest
    assert "final_fact_checker" in run_context.agent_manifest


def test_quality_gate_passed_rejects_low_scores():
    passed, notes = quality_gate_passed(
        average_score=8.7,
        min_score=7.8,
        technical_accuracy_score=8.9,
        policy=build_run_context(_make_config(), "run-1").quality_policy,
    )

    assert passed is False
    assert len(notes) == 3
