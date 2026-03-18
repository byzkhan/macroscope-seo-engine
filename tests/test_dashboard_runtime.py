"""Tests for the dashboard runtime helpers."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.dashboard_runtime import DashboardRunManager, ScheduleSettings, _read_events, build_stage_views
from app.schemas import ExecutionState, StageResult
from app.storage import ExecutionJournal, RunStore


def test_build_stage_views_marks_running_and_success_states():
    events = [
        {"event": "stage_completed", "stage": "bootstrap_run", "duration_seconds": 0.2},
        {"event": "stage_completed", "stage": "collect_signals", "duration_seconds": 2.5},
        {
            "event": "agent_trace",
            "stage": "collect_signals",
            "message": "Prepared market research brief.",
            "prompt_summary": "Collect market signals for AI review.",
            "preview": "ai-code-review, pr-workflows",
        },
        {"event": "stage_started", "stage": "generate_topics"},
    ]

    views = build_stage_views(events)

    assert views[0]["stage"] == "bootstrap_run"
    assert views[0]["status"] == "success"
    assert views[1]["stage"] == "collect_signals"
    assert views[1]["status"] == "success"
    assert views[1]["duration_seconds"] == 2.5
    assert views[1]["latest_prompt"] == "Collect market signals for AI review."
    assert views[1]["latest_preview"] == "ai-code-review, pr-workflows"
    assert views[1]["traces"][0]["message"] == "Prepared market research brief."
    assert views[2]["stage"] == "generate_topics"
    assert views[2]["status"] == "running"
    assert views[3]["status"] == "pending"


def test_build_stage_views_uses_summary_for_final_status():
    summary = {
        "stages": [
            {
                "stage": "write_draft",
                "success": False,
                "duration_seconds": 18.4,
                "artifact_path": None,
                "error": "draft generation failed",
            }
        ]
    }

    views = build_stage_views([], summary)
    write_draft = next(view for view in views if view["stage"] == "write_draft")

    assert write_draft["status"] == "failed"
    assert write_draft["duration_seconds"] == 18.4
    assert write_draft["error"] == "draft generation failed"


def test_build_stage_views_marks_canceled_stage():
    summary = {
        "stages": [
            {
                "stage": "qa_optimize",
                "success": False,
                "duration_seconds": 55.2,
                "artifact_path": None,
                "error": "Run stopped by user during qa_optimize",
                "failure_category": "canceled",
            }
        ]
    }

    views = build_stage_views([], summary)
    qa_optimize = next(view for view in views if view["stage"] == "qa_optimize")

    assert qa_optimize["status"] == "canceled"
    assert qa_optimize["error"] == "Run stopped by user during qa_optimize"


def test_dashboard_manager_persists_schedule(tmp_path: Path):
    project_root = tmp_path
    (project_root / "data").mkdir()
    manager = DashboardRunManager(project_root)

    schedule = manager.update_schedule(enabled=True, daily_time="07:30")

    assert schedule["enabled"] is True
    assert schedule["daily_time"] == "07:30"
    assert (project_root / "data" / "ui" / "dashboard_schedule.json").exists()


def test_dashboard_manager_stop_run_sets_stop_requested(tmp_path: Path):
    class DummyWorker:
        def is_alive(self) -> bool:
            return True

    project_root = tmp_path
    (project_root / "data").mkdir()
    manager = DashboardRunManager(project_root)
    manager._worker = DummyWorker()
    manager._active_run_id = "run-123"

    status = manager.stop_run()

    assert status["running"] is True
    assert status["stop_requested"] is True
    assert status["active_run_id"] == "run-123"


def test_dashboard_manager_stop_run_clears_stale_state(tmp_path: Path):
    project_root = tmp_path
    (project_root / "data").mkdir()
    state_path = project_root / "data" / "ui" / "dashboard_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"running": true, "active_run_id": "run-stale", "last_run_id": "run-stale"}',
        encoding="utf-8",
    )

    manager = DashboardRunManager(project_root)
    manager._active_run_id = "run-stale"

    status = manager.stop_run()

    assert status["running"] is False
    assert status["active_run_id"] is None


def test_dashboard_manager_marks_incomplete_runs_as_interrupted(tmp_path: Path):
    project_root = tmp_path
    run_dir = project_root / "data" / "runs" / "run-123"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                '{"event":"run_started","run_id":"run-123","timestamp":"2026-03-17T10:00:00+00:00"}',
                '{"event":"stage_completed","stage":"bootstrap_run","duration_seconds":0.2}',
                '{"event":"stage_started","stage":"collect_signals"}',
            ]
        ),
        encoding="utf-8",
    )

    manager = DashboardRunManager(project_root)

    overview = manager.get_run_overview("run-123")
    detail = manager.get_run_detail("run-123")

    assert overview["status"] == "interrupted"
    assert detail["summary"]["success"] is False
    assert detail["summary"]["errors"]


def test_read_events_keeps_valid_lines_when_one_line_is_bad(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                '{"event":"run_started","run_id":"run-1"}',
                '{"event": invalid json}',
                '{"event":"stage_completed","stage":"bootstrap_run"}',
            ]
        ),
        encoding="utf-8",
    )

    events = _read_events(run_dir)

    assert len(events) == 2
    assert events[0]["event"] == "run_started"
    assert events[1]["event"] == "stage_completed"


def test_schedule_settings_reject_invalid_time_values():
    with pytest.raises(ValidationError):
        ScheduleSettings(enabled=True, daily_time="25:99")


def test_dashboard_manager_flags_legacy_incomplete_runs_as_not_resumable(tmp_path: Path):
    project_root = tmp_path
    run_dir = project_root / "data" / "runs" / "run-legacy"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        '{"event":"stage_started","stage":"generate_topics"}\n',
        encoding="utf-8",
    )

    manager = DashboardRunManager(project_root)
    overview = manager.get_run_overview("run-legacy")
    detail = manager.get_run_detail("run-legacy")

    assert overview["status"] == "interrupted"
    assert overview["resumable"] is False
    assert detail["resumable"] is False


def test_dashboard_manager_resume_run_uses_checkpointed_stage(tmp_path: Path, monkeypatch):
    class FakeHandle:
        def __init__(self):
            self.pid = 4321
            self.create_time = 1234.5

        def is_alive(self) -> bool:
            return True

    project_root = tmp_path
    store = RunStore(project_root / "data", run_id="run-resume")
    journal = ExecutionJournal(store)
    for index, stage in enumerate(["bootstrap_run", "collect_signals", "generate_topics"]):
        journal.save_checkpoint(
            stage=stage,
            stage_order=[
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
            ],
            attempt=1,
            ctx_fragment={stage: index},
            stage_result=StageResult(stage=stage, success=True, duration_seconds=1.0).model_dump(mode="json"),
        )
    journal.save_execution_state(
        ExecutionState(
            run_id="run-resume",
            status="interrupted",
            current_stage="score_topics",
            next_stage="score_topics",
            completed_stages=["bootstrap_run", "collect_signals", "generate_topics"],
            resume_stage="score_topics",
            resume_count=0,
            started_at=datetime.now(timezone.utc),
            resume_reason="Worker exited during topic scoring.",
        )
    )

    manager = DashboardRunManager(project_root)
    monkeypatch.setattr(manager, "_launch_worker", lambda command, run_id: FakeHandle())

    status = manager.resume_run("run-resume")
    detail = manager.get_run_detail("run-resume")

    assert status["active_run_id"] == "run-resume"
    assert detail["status"] == "resuming"
    assert detail["resume_stage"] == "score_topics"


def test_dashboard_manager_rejects_path_traversal_for_artifacts(tmp_path: Path):
    project_root = tmp_path
    run_dir = project_root / "data" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    outside = project_root / "secret.txt"
    outside.write_text("nope", encoding="utf-8")

    manager = DashboardRunManager(project_root)

    with pytest.raises(FileNotFoundError):
        manager.resolve_artifact_path("run-1", "../../secret.txt")
