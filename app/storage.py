"""Run persistence, checkpoints, and artifact management."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .schemas import ExecutionState, ResumePlan, RunSummary, StageCheckpoint


def _now_str() -> str:
    """Generate a timestamp string for run directory naming."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")


EVENTS_FILENAME = "events.jsonl"
EXECUTION_STATE_FILENAME = "execution_state.json"
CHECKPOINTS_DIRNAME = "checkpoints"


def generate_run_id() -> str:
    """Generate a fresh run id for dashboard and CLI workers."""
    return _now_str()


class RunStore:
    """Manages a single pipeline run's directory and artifacts."""

    def __init__(self, data_dir: Path, run_id: str | None = None):
        self.data_dir = data_dir
        self.run_id = run_id or _now_str()
        self.run_dir = data_dir / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def save_json(self, filename: str, data: Any) -> Path:
        """Save data as JSON. Accepts Pydantic models, lists of models, or dicts."""
        path = self.run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, BaseModel):
            content = data.model_dump_json(indent=2)
        elif isinstance(data, list) and data and isinstance(data[0], BaseModel):
            serialized = [item.model_dump() for item in data]
            content = json.dumps(serialized, indent=2, default=str)
        else:
            content = json.dumps(data, indent=2, default=str)
        path.write_text(content, encoding="utf-8")
        return path

    def save_markdown(self, filename: str, content: str) -> Path:
        """Save markdown content to the run directory."""
        path = self.run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def load_json(self, filename: str) -> Any:
        """Load JSON data from the run directory."""
        path = self.run_dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def artifact_path(self, filename: str) -> str:
        """Return the string path to an artifact in this run."""
        return str(self.run_dir / filename)


class ExecutionJournal:
    """Durable run journal for events, checkpoints, and execution state."""

    def __init__(self, store: RunStore):
        self.store = store
        self.events_path = self.store.run_dir / EVENTS_FILENAME
        self.execution_state_path = self.store.run_dir / EXECUTION_STATE_FILENAME
        self.checkpoints_dir = self.store.run_dir / CHECKPOINTS_DIRNAME
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def append_event(self, event: dict[str, Any]) -> Path:
        """Append an event to the durable journal."""
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str) + "\n")
        return self.events_path

    def load_execution_state(self) -> ExecutionState | None:
        """Load the current execution state if present and valid."""
        payload = self.store.load_json(EXECUTION_STATE_FILENAME)
        if not isinstance(payload, dict):
            return None
        try:
            return ExecutionState.model_validate(payload)
        except Exception:
            return None

    def save_execution_state(self, state: ExecutionState) -> Path:
        """Persist the current execution state."""
        return self.store.save_json(EXECUTION_STATE_FILENAME, state)

    def update_execution_state(self, **updates: Any) -> ExecutionState:
        """Merge updates into the durable execution state."""
        state = self.load_execution_state()
        if state is None:
            run_id = str(updates.get("run_id") or self.store.run_id)
            started_at = updates.get("started_at") or datetime.now(timezone.utc)
            state = ExecutionState(run_id=run_id, started_at=started_at)
        payload = state.model_dump()
        payload.update(updates)
        payload["last_heartbeat_at"] = datetime.now(timezone.utc)
        normalized = ExecutionState.model_validate(payload)
        self.save_execution_state(normalized)
        return normalized

    def checkpoint_path(self, stage: str, stage_order: list[str]) -> Path:
        """Return the deterministic checkpoint path for a stage."""
        index = stage_order.index(stage) + 1
        return self.checkpoints_dir / f"{index:02d}_{stage}.json"

    def save_checkpoint(
        self,
        *,
        stage: str,
        stage_order: list[str],
        attempt: int,
        ctx_fragment: dict[str, Any],
        stage_result: dict[str, Any],
    ) -> Path:
        """Persist a successful stage checkpoint."""
        checkpoint = StageCheckpoint(
            stage=stage,
            completed_at=datetime.now(timezone.utc),
            attempt=attempt,
            ctx_fragment=ctx_fragment,
            stage_result=stage_result,
        )
        path = self.checkpoint_path(stage, stage_order)
        return self.store.save_json(str(path.relative_to(self.store.run_dir)), checkpoint)

    def load_checkpoints(self, stage_order: list[str]) -> tuple[list[StageCheckpoint], list[str]]:
        """Load valid checkpoints in stage order, collecting corruption notes."""
        checkpoints: list[StageCheckpoint] = []
        notes: list[str] = []
        for stage in stage_order:
            path = self.checkpoint_path(stage, stage_order)
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                checkpoints.append(StageCheckpoint.model_validate(payload))
            except Exception:
                notes.append(f"Checkpoint for stage '{stage}' was missing or corrupt.")
                break
        return checkpoints, notes

    def build_resume_plan(self, stage_order: list[str]) -> ResumePlan:
        """Compute a safe stage-boundary resume plan for an interrupted run."""
        state = self.load_execution_state()
        if state is None:
            return ResumePlan(
                run_id=self.store.run_id,
                resumable=False,
                resume_reason="Legacy run has no execution_state.json, so it cannot resume.",
            )

        checkpoints, notes = self.load_checkpoints(stage_order)
        valid_by_stage = {checkpoint.stage: checkpoint for checkpoint in checkpoints}
        completed_prefix: list[str] = []
        merged_ctx: dict[str, Any] = {}
        for stage in stage_order:
            checkpoint = valid_by_stage.get(stage)
            if checkpoint is None:
                break
            completed_prefix.append(stage)
            merged_ctx.update(checkpoint.ctx_fragment)

        resume_stage = None
        resume_reason = state.resume_reason
        earliest_recoverable = stage_order[len(completed_prefix)] if len(completed_prefix) < len(stage_order) else None

        if state.current_stage and state.current_stage in stage_order:
            current_index = stage_order.index(state.current_stage)
            if current_index > len(completed_prefix):
                resume_stage = earliest_recoverable
                resume_reason = notes[0] if notes else (
                    f"Checkpoint gap detected before '{state.current_stage}'. Falling back to '{resume_stage}'."
                    if resume_stage
                    else resume_reason
                )
            elif state.current_stage not in completed_prefix:
                resume_stage = state.current_stage
                resume_reason = resume_reason or f"Resuming interrupted stage '{state.current_stage}'."
        elif state.next_stage and state.next_stage in stage_order:
            next_index = stage_order.index(state.next_stage)
            if next_index > len(completed_prefix):
                resume_stage = earliest_recoverable
                resume_reason = notes[0] if notes else (
                    f"Checkpoint gap detected before '{state.next_stage}'. Falling back to '{resume_stage}'."
                    if resume_stage
                    else resume_reason
                )
            elif state.next_stage not in completed_prefix:
                resume_stage = state.next_stage
                resume_reason = resume_reason or f"Resuming next stage '{state.next_stage}'."
        elif earliest_recoverable:
            resume_stage = earliest_recoverable
            if notes:
                resume_reason = notes[0]
            else:
                resume_reason = resume_reason or f"Resuming from '{resume_stage}' after durable prefix recovery."

        resumable = resume_stage is not None and state.status not in {"completed", "failed", "canceled"}
        return ResumePlan(
            run_id=self.store.run_id,
            resumable=resumable,
            resume_stage=resume_stage,
            completed_stages=completed_prefix,
            resume_count=state.resume_count,
            resume_reason=resume_reason,
            ctx=merged_ctx,
            resumed_from_stage=resume_stage,
        )


def load_topic_history(data_dir: Path) -> list[dict]:
    """Load the global topic history from data/topic_history.json."""
    path = data_dir / "topic_history.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def append_to_topic_history(
    data_dir: Path,
    slug: str,
    title: str,
    keywords: list[str],
    cluster: str,
) -> None:
    """Append a published article to the topic history.

    Uses atomic write pattern: write to temp file, then rename.
    """
    path = data_dir / "topic_history.json"
    history = load_topic_history(data_dir)

    # Deduplicate: don't add if slug already present
    if any(entry.get("slug") == slug for entry in history):
        return

    history.append(
        {
            "slug": slug,
            "title": title,
            "keywords": keywords,
            "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "cluster": cluster,
        }
    )

    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    tmp_path.rename(path)


def save_run_summary(store: RunStore, summary: RunSummary) -> Path:
    """Save the final run summary."""
    return store.save_json("run_summary.json", summary)


def list_runs(data_dir: Path) -> list[str]:
    """List all run IDs sorted by most recent first."""
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        [d.name for d in runs_dir.iterdir() if d.is_dir()],
        reverse=True,
    )


def get_latest_run(data_dir: Path) -> str | None:
    """Get the most recent run ID, or None if no runs exist."""
    runs = list_runs(data_dir)
    return runs[0] if runs else None
