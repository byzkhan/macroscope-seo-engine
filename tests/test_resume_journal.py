"""Tests for durable execution state and resume planning."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.schemas import ExecutionState, StageResult
from app.storage import ExecutionJournal, RunStore


STAGE_ORDER = [
    "bootstrap_run",
    "collect_signals",
    "generate_topics",
    "score_topics",
    "build_brief",
    "write_draft",
    "qa_optimize",
    "fact_check",
    "export",
    "persist_history",
]


def _save_checkpoint(journal: ExecutionJournal, stage: str, index: int) -> None:
    journal.save_checkpoint(
        stage=stage,
        stage_order=STAGE_ORDER,
        attempt=1,
        ctx_fragment={f"stage_{index}": stage},
        stage_result=StageResult(stage=stage, success=True, duration_seconds=1.0).model_dump(mode="json"),
    )


def test_resume_plan_restarts_same_stage_when_interrupt_happens_mid_stage(tmp_path: Path):
    store = RunStore(tmp_path / "data", run_id="resume-mid-stage")
    journal = ExecutionJournal(store)
    for index, stage in enumerate(STAGE_ORDER[:5]):
        _save_checkpoint(journal, stage, index)

    journal.save_execution_state(
        ExecutionState(
            run_id=store.run_id,
            status="interrupted",
            current_stage="write_draft",
            next_stage="write_draft",
            completed_stages=STAGE_ORDER[:5],
            resume_stage="write_draft",
            resume_count=0,
            started_at=datetime.now(timezone.utc),
            resume_reason="Process died during write_draft.",
        )
    )

    plan = journal.build_resume_plan(STAGE_ORDER)

    assert plan.resumable is True
    assert plan.resume_stage == "write_draft"
    assert plan.completed_stages == STAGE_ORDER[:5]


def test_resume_plan_falls_back_to_earliest_recoverable_stage_on_checkpoint_gap(tmp_path: Path):
    store = RunStore(tmp_path / "data", run_id="resume-gap")
    journal = ExecutionJournal(store)
    for index, stage in enumerate(STAGE_ORDER[:2]):
        _save_checkpoint(journal, stage, index)

    journal.save_execution_state(
        ExecutionState(
            run_id=store.run_id,
            status="interrupted",
            current_stage="write_draft",
            next_stage="write_draft",
            completed_stages=["bootstrap_run", "collect_signals", "score_topics"],
            resume_stage="write_draft",
            resume_count=1,
            started_at=datetime.now(timezone.utc),
            resume_reason="Required checkpoints are missing.",
        )
    )

    plan = journal.build_resume_plan(STAGE_ORDER)

    assert plan.resumable is True
    assert plan.resume_stage == "generate_topics"
    assert "falling back" in (plan.resume_reason or "").lower() or "missing" in (plan.resume_reason or "").lower()


def test_resume_plan_marks_legacy_runs_non_resumable(tmp_path: Path):
    store = RunStore(tmp_path / "data", run_id="legacy-run")
    journal = ExecutionJournal(store)

    plan = journal.build_resume_plan(STAGE_ORDER)

    assert plan.resumable is False
    assert plan.resume_stage is None
