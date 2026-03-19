"""Runtime services for the dashboard UI.

This module owns three things:
- background pipeline execution
- persisted daily scheduling state
- read models for live and historical runs
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, Field

from .config import ensure_live_run_provider, load_config
from .schemas import ExecutionState
from .storage import ExecutionJournal, RunStore, generate_run_id, list_runs

try:
    import psutil
except ImportError:  # pragma: no cover - exercised only if dependency is missing
    psutil = None  # type: ignore[assignment]

PIPELINE_STAGE_META = [
    {
        "stage": "bootstrap_run",
        "agent": "Run Bootstrapper",
        "label": "Bootstrap Run",
        "description": "Creates fresh run policies, guardrails, and agent context.",
    },
    {
        "stage": "collect_signals",
        "agent": "Market Watcher",
        "label": "Collect Signals",
        "description": "Researches the market and gathers recent signals.",
    },
    {
        "stage": "generate_topics",
        "agent": "Topic Researcher",
        "label": "Generate Topics",
        "description": "Turns live signals into candidate blog angles.",
    },
    {
        "stage": "score_topics",
        "agent": "Topic Scorer",
        "label": "Score Topics",
        "description": "Ranks candidates and selects the strongest topic.",
    },
    {
        "stage": "build_brief",
        "agent": "Research Brief Writer",
        "label": "Build Brief",
        "description": "Creates the outline, keywords, FAQ, and CTA.",
    },
    {
        "stage": "write_draft",
        "agent": "Blog Writer",
        "label": "Write Draft",
        "description": "Writes the article draft from the brief.",
    },
    {
        "stage": "qa_optimize",
        "agent": "SEO / AEO Editor",
        "label": "QA + Optimize",
        "description": "Checks quality and improves the final article.",
    },
    {
        "stage": "fact_check",
        "agent": "Fact Checker",
        "label": "Fact Check",
        "description": "Verifies final claims against fresh web evidence.",
    },
    {
        "stage": "export",
        "agent": "Publisher",
        "label": "Export",
        "description": "Exports markdown and sidecar artifacts.",
    },
    {
        "stage": "persist_history",
        "agent": "Archive Manager",
        "label": "Persist History",
        "description": "Adds the final topic to the publishing archive.",
    },
]
EVENTS_FILENAME = "events.jsonl"
UI_STATE_FILENAME = "dashboard_state.json"
SCHEDULE_FILENAME = "dashboard_schedule.json"
STOP_GRACE_SECONDS = 5.0


class ScheduleSettings(BaseModel):
    """Persisted daily schedule for the dashboard."""

    enabled: bool = False
    daily_time: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _read_events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / EVENTS_FILENAME
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return events


class ProcessHandle:
    """Durable wrapper around a worker subprocess identity."""

    def __init__(self, pid: int, create_time: float):
        self.pid = pid
        self.create_time = create_time

    @classmethod
    def from_pid(cls, pid: int, create_time: float) -> "ProcessHandle | None":
        handle = cls(pid=pid, create_time=create_time)
        return handle if handle.is_alive() else None

    def is_alive(self) -> bool:
        if psutil is None:
            return False
        try:
            process = psutil.Process(self.pid)
            return process.is_running() and abs(process.create_time() - self.create_time) < 0.01
        except (psutil.Error, OSError):
            return False

    def terminate(self) -> None:
        if psutil is None:
            return
        try:
            process = psutil.Process(self.pid)
            if abs(process.create_time() - self.create_time) >= 0.01:
                return
            process.terminate()
        except (psutil.Error, OSError):
            return

    def kill(self) -> None:
        if psutil is None:
            return
        try:
            process = psutil.Process(self.pid)
            if abs(process.create_time() - self.create_time) >= 0.01:
                return
            process.kill()
        except (psutil.Error, OSError):
            return


def _find_exported_markdown(run_dir: Path) -> Path | None:
    preferred = [
        path
        for path in sorted(run_dir.glob("*.md"))
        if path.name not in {"draft.md", "optimized_draft.md", "final.md"}
    ]
    if preferred:
        return preferred[0]

    fallback = run_dir / "final.md"
    return fallback if fallback.exists() else None


def build_stage_views(
    events: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build UI-ready stage cards from live events and the final summary."""
    event_by_stage: dict[str, dict[str, Any]] = {}
    trace_by_stage: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        stage = event.get("stage")
        if stage:
            if event.get("event") == "agent_trace":
                trace_by_stage.setdefault(stage, []).append(
                    {
                        "message": event.get("message"),
                        "status": event.get("status", "info"),
                        "preview": event.get("preview"),
                        "prompt_summary": event.get("prompt_summary"),
                        "artifact_path": event.get("artifact_path"),
                        "timestamp": event.get("timestamp"),
                    }
                )
                continue
            event_by_stage[stage] = event

    summary_stages = {
        stage["stage"]: stage
        for stage in (summary or {}).get("stages", [])
        if isinstance(stage, dict) and stage.get("stage")
    }

    stage_views: list[dict[str, Any]] = []
    for meta in PIPELINE_STAGE_META:
        stage_name = meta["stage"]
        status = "pending"
        duration_seconds = None
        artifact_path = None
        error = None
        traces = trace_by_stage.get(stage_name, [])[-4:]
        latest_prompt = next(
            (trace["prompt_summary"] for trace in reversed(traces) if trace.get("prompt_summary")),
            None,
        )
        latest_preview = next(
            (trace["preview"] for trace in reversed(traces) if trace.get("preview")),
            None,
        )

        event = event_by_stage.get(stage_name)
        if event:
            if event["event"] == "stage_started":
                status = "running"
            elif event["event"] == "stage_completed":
                status = "success"
                duration_seconds = event.get("duration_seconds")
                artifact_path = event.get("artifact_path")
            elif event["event"] == "stage_failed":
                status = "canceled" if event.get("failure_category") == "canceled" else "failed"
                duration_seconds = event.get("duration_seconds")
                error = event.get("error")

        stage_summary = summary_stages.get(stage_name)
        if stage_summary:
            failure_category = stage_summary.get("failure_category")
            if stage_summary.get("success"):
                status = "success"
            else:
                status = "canceled" if failure_category == "canceled" else "failed"
            duration_seconds = stage_summary.get("duration_seconds")
            artifact_path = stage_summary.get("artifact_path")
            error = stage_summary.get("error")

        stage_views.append(
            {
                **meta,
                "status": status,
                "duration_seconds": duration_seconds,
                "artifact_path": artifact_path,
                "error": error,
                "latest_prompt": latest_prompt,
                "latest_preview": latest_preview,
                "traces": traces,
            }
        )

    return stage_views


class DashboardRunManager:
    """Owns pipeline execution and scheduling for the dashboard."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.data_dir = project_root / "data"
        self.ui_dir = self.data_dir / "ui"
        self.ui_dir.mkdir(parents=True, exist_ok=True)
        self.schedule_path = self.ui_dir / SCHEDULE_FILENAME
        self.state_path = self.ui_dir / UI_STATE_FILENAME
        previous_state = _read_json(self.state_path, {})
        self._lock = threading.RLock()
        self._worker: ProcessHandle | None = None
        self._active_run_id: str | None = None
        self._active_run_dir: Path | None = None
        self._scheduler_started = False
        self._last_run_id: str | None = previous_state.get("last_run_id") or previous_state.get("active_run_id")
        self._last_error: str | None = previous_state.get("last_error")
        self._last_trigger: str | None = previous_state.get("last_trigger")
        self.scheduler = BackgroundScheduler(timezone=datetime.now().astimezone().tzinfo)
        self._recover_worker_from_disk()
        self._write_state()

    def start(self) -> None:
        """Start the background scheduler once."""
        with self._lock:
            if self._scheduler_started:
                return
            self.scheduler.start()
            self._scheduler_started = True
            self._sync_schedule_job()

    def shutdown(self) -> None:
        """Stop the background scheduler without waiting on a run thread."""
        with self._lock:
            if not self._scheduler_started:
                return
            self.scheduler.shutdown(wait=False)
            self._scheduler_started = False

    def get_schedule(self) -> dict[str, Any]:
        settings = ScheduleSettings.model_validate(
            _read_json(self.schedule_path, ScheduleSettings().model_dump())
        )
        return {
            **settings.model_dump(),
            "timezone": str(self.scheduler.timezone),
        }

    def update_schedule(self, enabled: bool, daily_time: str) -> dict[str, Any]:
        settings = ScheduleSettings(enabled=enabled, daily_time=daily_time)
        _write_json(self.schedule_path, settings.model_dump())
        self._sync_schedule_job(settings)
        return self.get_schedule()

    def start_run(self, trigger: str = "manual") -> dict[str, Any]:
        with self._lock:
            self._reconcile_worker_locked()
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("A pipeline run is already in progress.")
            try:
                config = load_config(root=self.project_root, json_output=True)
                ensure_live_run_provider(config, context="Dashboard pipeline run")
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc

            run_id = generate_run_id()
            store = RunStore(self.data_dir, run_id=run_id)
            journal = ExecutionJournal(store)
            started_at = datetime.now(timezone.utc)
            journal.save_execution_state(
                ExecutionState(
                    run_id=run_id,
                    status="running",
                    current_stage=None,
                    next_stage="bootstrap_run",
                    completed_stages=[],
                    resume_stage="bootstrap_run",
                    resume_count=0,
                    stop_requested=False,
                    started_at=started_at,
                    resume_reason=None,
                )
            )
            try:
                handle = self._launch_worker("run", run_id)
            except Exception as exc:
                journal.update_execution_state(
                    status="failed",
                    current_stage=None,
                    next_stage=None,
                    resume_stage=None,
                    last_error=str(exc),
                    resume_reason=None,
                )
                raise
            journal.update_execution_state(
                worker_pid=handle.pid,
                worker_create_time=handle.create_time,
                status="running",
                next_stage="bootstrap_run",
                resume_stage="bootstrap_run",
            )
            self._worker = handle
            self._active_run_id = run_id
            self._active_run_dir = store.run_dir
            self._last_run_id = run_id
            self._last_error = None
            self._last_trigger = trigger
            self._write_state(trigger=trigger)

        return self.get_status()

    def stop_run(self) -> dict[str, Any]:
        with self._lock:
            previous_state = _read_json(self.state_path, {})
            had_stale_state = bool(
                self._active_run_id
                or previous_state.get("running")
                or previous_state.get("active_run_id")
            )
            self._reconcile_worker_locked()
            if self._worker is None or not self._worker.is_alive():
                if self._active_run_id:
                    self._mark_run_interrupted(self._active_run_id, "Stopped tracking stale run state.")
                self._active_run_id = None
                self._active_run_dir = None
                self._worker = None
                self._write_state()
                if had_stale_state:
                    return self.get_status()
                raise RuntimeError("No pipeline run is currently in progress.")
            assert self._active_run_id is not None
            journal = ExecutionJournal(RunStore(self.data_dir, run_id=self._active_run_id))
            journal.update_execution_state(
                status="stopping",
                stop_requested=True,
                resume_reason="Stop requested from dashboard.",
            )
            worker = self._worker
            self._write_state()
            threading.Thread(
                target=self._escalate_stop_after_grace,
                args=(self._active_run_id, worker),
                daemon=True,
            ).start()

        return self.get_status()

    def resume_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            self._reconcile_worker_locked()
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("A pipeline run is already in progress.")

            run_dir = self.data_dir / "runs" / run_id
            if not run_dir.exists():
                raise RuntimeError(f"Run '{run_id}' does not exist.")
            store = RunStore(self.data_dir, run_id=run_id)
            journal = ExecutionJournal(store)
            stage_order = [meta["stage"] for meta in PIPELINE_STAGE_META]
            plan = journal.build_resume_plan(stage_order)
            if not plan.resumable or not plan.resume_stage:
                raise RuntimeError(plan.resume_reason or f"Run '{run_id}' is not resumable.")

            try:
                handle = self._launch_worker("resume", run_id)
            except Exception as exc:
                journal.update_execution_state(
                    status="interrupted",
                    last_error=str(exc),
                    resume_reason=plan.resume_reason,
                )
                raise
            journal.update_execution_state(
                status="resuming",
                current_stage=None,
                next_stage=plan.resume_stage,
                resume_stage=plan.resume_stage,
                worker_pid=handle.pid,
                worker_create_time=handle.create_time,
                stop_requested=False,
                last_error=None,
                resume_reason=plan.resume_reason,
            )
            self._worker = handle
            self._active_run_id = run_id
            self._active_run_dir = store.run_dir
            self._last_run_id = run_id
            self._last_error = None
            self._last_trigger = "resume"
            self._write_state(trigger="resume")

        return self.get_status()

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            self._reconcile_worker_locked()
            running = self._worker is not None and self._worker.is_alive()
            active_run_id = self._active_run_id
            stop_requested = False
            if active_run_id:
                state = ExecutionJournal(RunStore(self.data_dir, run_id=active_run_id)).load_execution_state()
                stop_requested = bool(state is not None and state.stop_requested)

        recent = [self.get_run_overview(run_id) for run_id in list_runs(self.data_dir)[:8]]
        return {
            "running": running,
            "active_run_id": active_run_id,
            "stop_requested": stop_requested,
            "schedule": self.get_schedule(),
            "recent_runs": recent,
            "ui_state": _read_json(self.state_path, {}),
        }

    def get_run_overview(self, run_id: str) -> dict[str, Any]:
        run_dir = self.data_dir / "runs" / run_id
        summary = _read_json(run_dir / "run_summary.json", None)
        selected = _read_json(run_dir / "selected_topic.json", {})
        state = ExecutionJournal(RunStore(self.data_dir, run_id=run_id)).load_execution_state()
        with self._lock:
            self._reconcile_worker_locked()
            running = self._worker is not None and self._worker.is_alive()
            is_active = running and run_id == self._active_run_id
        concise = summary.get("topic_selected") if summary else None
        if not concise:
            concise = selected.get("candidate", {}).get("title")

        resumable = bool(
            state is not None
            and state.status in {"interrupted", "resumable"}
            and state.resume_stage
        )

        if is_active and state is not None:
            status = state.status
        elif summary:
            if summary.get("success") is True:
                status = "completed"
            else:
                has_canceled_stage = any(
                    stage.get("failure_category") == "canceled"
                    for stage in summary.get("stages", [])
                    if isinstance(stage, dict)
                )
                status = "canceled" if has_canceled_stage else "failed"
        elif state is not None:
            if state.status in {"running", "stopping", "resuming"} and not is_active:
                status = "interrupted"
            elif state.status in {"interrupted", "resumable"}:
                status = "interrupted"
            else:
                status = state.status
        else:
            status = "running" if is_active else "interrupted"

        return {
            "run_id": run_id,
            "success": summary.get("success") if summary else None,
            "status": status,
            "resumable": resumable,
            "resume_stage": state.resume_stage if state is not None else None,
            "resume_reason": state.resume_reason if state is not None else None,
            "topic": concise,
            "grade": summary.get("final_grade") if summary else None,
            "score": summary.get("final_score") if summary else None,
            "word_count": summary.get("word_count") if summary else None,
            "started_at": summary.get("started_at") if summary else (state.started_at.isoformat() if state is not None else None),
            "completed_at": summary.get("completed_at") if summary else None,
        }

    def get_run_detail(self, run_id: str) -> dict[str, Any]:
        run_dir = self.data_dir / "runs" / run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"Run '{run_id}' does not exist")

        summary = _read_json(run_dir / "run_summary.json", None)
        events = _read_events(run_dir)
        journal = ExecutionJournal(RunStore(self.data_dir, run_id=run_id))
        state = journal.load_execution_state()
        with self._lock:
            self._reconcile_worker_locked()
            running = self._worker is not None and self._worker.is_alive()
            is_active = running and run_id == self._active_run_id
        if summary is None and not is_active:
            summary = self._build_incomplete_summary(run_id, events, state)
        selected = _read_json(run_dir / "selected_topic.json", {})
        qa_result = _read_json(run_dir / "qa_result.json", None)
        final_meta = _read_json(run_dir / "meta.json", None)
        article_path = _find_exported_markdown(run_dir)
        article_preview = ""
        if article_path:
            try:
                article_preview = article_path.read_text(encoding="utf-8")[:8000]
            except OSError:
                article_preview = ""

        artifacts = [
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(run_dir.iterdir())
            if path.is_file()
        ]

        return {
            "run_id": run_id,
            "summary": summary,
            "status": state.status if state is not None else ("running" if is_active else "interrupted"),
            "resumable": bool(
                state is not None
                and state.status in {"interrupted", "resumable"}
                and state.resume_stage
            ),
            "resume_stage": state.resume_stage if state is not None else None,
            "resume_reason": state.resume_reason if state is not None else None,
            "execution_state": state.model_dump(mode="json") if state is not None else None,
            "selected_topic": selected,
            "qa_result": qa_result,
            "final_meta": final_meta,
            "stage_views": build_stage_views(events, summary),
            "events": events[-25:],
            "artifacts": artifacts,
            "article_path": str(article_path) if article_path else None,
            "article_preview": article_preview,
        }

    def _build_incomplete_summary(
        self,
        run_id: str,
        events: list[dict[str, Any]],
        state: ExecutionState | None = None,
    ) -> dict[str, Any]:
        """Synthesize a failed summary for abandoned runs without run_summary.json."""
        stages: list[dict[str, Any]] = []
        completed: dict[str, dict[str, Any]] = {}
        started: dict[str, dict[str, Any]] = {}
        last_started_stage: str | None = None

        for event in events:
            stage = event.get("stage")
            if not stage:
                continue
            if event.get("event") == "stage_started":
                started[stage] = event
                last_started_stage = stage
            elif event.get("event") == "stage_completed":
                completed[stage] = event
            elif event.get("event") == "stage_failed":
                stages.append(
                    {
                        "stage": stage,
                        "success": False,
                        "duration_seconds": event.get("duration_seconds", 0.0),
                        "artifact_path": event.get("artifact_path"),
                        "error": event.get("error") or "Run stopped before completion.",
                        "failure_category": event.get("failure_category") or "unknown",
                    }
                )

        for stage, event in completed.items():
            stages.append(
                {
                    "stage": stage,
                    "success": True,
                    "duration_seconds": event.get("duration_seconds", 0.0),
                    "artifact_path": event.get("artifact_path"),
                    "error": None,
                    "failure_category": None,
                }
            )

        failed_stages = {stage.get("stage") for stage in stages}
        if last_started_stage and last_started_stage not in completed and last_started_stage not in failed_stages:
            stages.append(
                {
                    "stage": last_started_stage,
                    "success": False,
                    "duration_seconds": 0.0,
                    "artifact_path": None,
                    "error": "Run did not complete. The dashboard or pipeline process stopped before a summary was written.",
                    "failure_category": "unknown",
                }
            )

        started_at = None
        for event in events:
            if event.get("event") == "run_started":
                started_at = event.get("timestamp")
                break

        return {
            "run_id": run_id,
            "success": False,
            "started_at": started_at or (state.started_at.isoformat() if state is not None else None),
            "completed_at": None,
            "resume_count": state.resume_count if state is not None else 0,
            "resumed_from_stage": state.resume_stage if state is not None else None,
            "stages": stages,
            "topic_selected": None,
            "final_score": None,
            "final_grade": None,
            "word_count": None,
            "artifacts": {},
            "errors": [
                state.last_error
                if state is not None and state.last_error
                else "Run did not complete. The dashboard or pipeline process stopped before the run summary was written."
            ],
            "total_duration_seconds": sum(
                float(stage.get("duration_seconds", 0.0) or 0.0) for stage in stages
            ),
        }

    def get_artifact_content(self, run_id: str, artifact_name: str) -> dict[str, Any]:
        path = self.resolve_artifact_path(run_id, artifact_name)

        suffix = path.suffix.lower()
        mime = "text/plain"
        try:
            if suffix == ".json":
                payload = _read_json(path, {})
                mime = "application/json"
                content = json.dumps(payload, indent=2)
            else:
                content = path.read_text(encoding="utf-8")
                if suffix == ".md":
                    mime = "text/markdown"
        except OSError as exc:
            raise FileNotFoundError(str(exc)) from exc

        return {
            "name": artifact_name,
            "path": str(path),
            "content_type": mime,
            "content": content,
        }

    def resolve_artifact_path(self, run_id: str, artifact_name: str) -> Path:
        """Resolve a run artifact path and ensure it exists."""
        run_dir = self.data_dir / "runs" / run_id
        path = run_dir / artifact_name
        try:
            resolved_run_dir = run_dir.resolve()
            resolved = path.resolve()
            resolved.relative_to(resolved_run_dir)
        except (OSError, RuntimeError, ValueError):
            raise FileNotFoundError(f"Artifact '{artifact_name}' not found for run '{run_id}'")
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Artifact '{artifact_name}' not found for run '{run_id}'")
        return resolved

    def _sync_schedule_job(self, settings: ScheduleSettings | None = None) -> None:
        settings = settings or ScheduleSettings.model_validate(
            _read_json(self.schedule_path, ScheduleSettings().model_dump())
        )

        existing = self.scheduler.get_job("daily-pipeline-run")
        if existing is not None:
            self.scheduler.remove_job("daily-pipeline-run")

        if not settings.enabled:
            return

        hour, minute = map(int, settings.daily_time.split(":"))
        trigger = CronTrigger(hour=hour, minute=minute, timezone=self.scheduler.timezone)
        self.scheduler.add_job(
            self._run_scheduled_pipeline,
            trigger=trigger,
            id="daily-pipeline-run",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )

    def _run_scheduled_pipeline(self) -> None:
        try:
            self.start_run(trigger="schedule")
        except RuntimeError:
            return

    def _recover_worker_from_disk(self) -> None:
        """Recover a live worker handle after dashboard restart."""
        with self._lock:
            self._reconcile_worker_locked()

    def _reconcile_worker_locked(self) -> None:
        """Refresh live worker state and mark dead workers interrupted."""
        if self._worker is not None and self._worker.is_alive():
            return

        live_run_id: str | None = None
        live_handle: ProcessHandle | None = None

        for run_id in list_runs(self.data_dir):
            state = ExecutionJournal(RunStore(self.data_dir, run_id=run_id)).load_execution_state()
            if state is None:
                continue
            if state.status not in {"running", "stopping", "resuming"}:
                continue
            handle = (
                ProcessHandle.from_pid(state.worker_pid, state.worker_create_time)
                if state.worker_pid and state.worker_create_time
                else None
            )
            if handle is None:
                self._mark_run_interrupted(
                    run_id,
                    state.last_error or "Worker process exited before the run completed.",
                )
                continue
            live_run_id = run_id
            live_handle = handle
            break

        self._worker = live_handle
        self._active_run_id = live_run_id
        self._active_run_dir = self.data_dir / "runs" / live_run_id if live_run_id else None
        if live_run_id is None:
            self._write_state()

    def _mark_run_interrupted(self, run_id: str, reason: str) -> None:
        """Convert a stale running state into a resumable interrupted state."""
        journal = ExecutionJournal(RunStore(self.data_dir, run_id=run_id))
        state = journal.load_execution_state()
        if state is None:
            return
        if state.status in {"completed", "failed", "canceled", "interrupted", "resumable"}:
            return
        stage_order = [meta["stage"] for meta in PIPELINE_STAGE_META]
        plan = journal.build_resume_plan(stage_order)
        journal.update_execution_state(
            status="interrupted",
            current_stage=None,
            next_stage=plan.resume_stage,
            resume_stage=plan.resume_stage,
            worker_pid=None,
            worker_create_time=None,
            stop_requested=False,
            last_error=reason,
            resume_reason=plan.resume_reason or reason,
        )

    def _launch_worker(self, command: str, run_id: str) -> ProcessHandle:
        """Launch a durable subprocess for a fresh or resumed run."""
        if psutil is None:
            raise RuntimeError("psutil is required for durable dashboard workers.")
        args = [
            sys.executable,
            "-m",
            "app.main",
            "--json",
            command,
            "--run-id",
            run_id,
            "--root",
            str(self.project_root),
        ]
        process = subprocess.Popen(
            args,
            cwd=self.project_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            worker = psutil.Process(process.pid)
            return ProcessHandle(pid=process.pid, create_time=worker.create_time())
        except psutil.NoSuchProcess as exc:
            process.wait(timeout=0.1)
            raise RuntimeError(
                f"Worker process failed to start (pid={process.pid}, exit_code={process.returncode}). "
                "Check that app.main is importable."
            ) from exc

    def _escalate_stop_after_grace(self, run_id: str, worker: ProcessHandle) -> None:
        """Escalate a stop request to process termination if needed."""
        time.sleep(STOP_GRACE_SECONDS)
        with self._lock:
            journal = ExecutionJournal(RunStore(self.data_dir, run_id=run_id))
            state = journal.load_execution_state()
            if state is None or not state.stop_requested:
                return
            if not worker.is_alive():
                return
            if hasattr(worker, "terminate"):
                worker.terminate()
        time.sleep(2.0)
        if worker.is_alive() and hasattr(worker, "kill"):
            worker.kill()
        with self._lock:
            journal = ExecutionJournal(RunStore(self.data_dir, run_id=run_id))
            latest = journal.load_execution_state()
            if latest is not None and latest.stop_requested:
                journal.update_execution_state(
                    status="canceled",
                    current_stage=None,
                    next_stage=None,
                    resume_stage=None,
                    worker_pid=None,
                    worker_create_time=None,
                    stop_requested=False,
                    last_error="Run stopped by user from the dashboard.",
                    resume_reason=None,
                )
            if self._active_run_id == run_id and (self._worker is None or not self._worker.is_alive()):
                self._worker = None
                self._active_run_id = None
                self._active_run_dir = None
                self._write_state()

    def _write_state(
        self,
        *,
        trigger: str | None = None,
        last_run_id: str | None = None,
        last_error: str | None = None,
    ) -> None:
        with self._lock:
            running = self._worker is not None and self._worker.is_alive()
            stop_requested = False
            if self._active_run_id:
                state = ExecutionJournal(RunStore(self.data_dir, run_id=self._active_run_id)).load_execution_state()
                stop_requested = bool(state is not None and state.stop_requested)
            if last_run_id is not None:
                self._last_run_id = last_run_id
            if last_error is not None:
                self._last_error = last_error
            if trigger is not None:
                self._last_trigger = trigger
            payload = {
                "running": running,
                "stop_requested": stop_requested,
                "active_run_id": self._active_run_id,
                "last_run_id": self._last_run_id,
                "last_error": self._last_error,
                "last_trigger": self._last_trigger,
                "updated_at": _utc_now_iso(),
            }
            _write_json(self.state_path, payload)
