"""Run persistence and artifact management.

Handles creating run directories, saving intermediate artifacts,
and managing topic history with safe file I/O.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .schemas import RunSummary


def _now_str() -> str:
    """Generate a timestamp string for run directory naming."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")


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
