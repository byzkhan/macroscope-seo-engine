"""Configuration loading for the Macroscope SEO engine.

Loads YAML configs, markdown guides, and environment settings.
All paths are resolved relative to the project root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _project_root() -> Path:
    """Resolve the project root (directory containing pyproject.toml)."""
    current = Path(__file__).resolve().parent.parent
    if (current / "pyproject.toml").exists():
        return current
    raise FileNotFoundError(f"Cannot find project root from {current}")


@dataclass(frozen=True)
class EngineConfig:
    """Immutable configuration for a pipeline run."""

    project_root: Path
    config_dir: Path
    data_dir: Path

    # Loaded content
    brand_context: str = ""
    style_guide: str = ""
    seo_rules: str = ""
    aeo_rules: str = ""
    competitors: dict[str, Any] = field(default_factory=dict)
    topic_clusters: dict[str, Any] = field(default_factory=dict)
    forbidden_claims: list[str] = field(default_factory=list)
    requires_evidence: list[dict[str, str]] = field(default_factory=list)

    # Runtime settings
    min_topic_score: float = 45.0
    target_word_count: int = 2000
    min_word_count: int = 800
    max_word_count: int = 4000
    min_internal_links: int = 3
    min_faq_count: int = 4
    dry_run: bool = False
    json_output: bool = False


def _load_yaml(path: Path) -> Any:
    """Load a YAML file, returning empty dict if missing or malformed."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return {}


def _load_text(path: Path) -> str:
    """Load a text/markdown file, returning empty string if missing."""
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_config(
    root: Path | None = None,
    dry_run: bool = False,
    json_output: bool = False,
) -> EngineConfig:
    """Load all configuration files and return an immutable EngineConfig."""
    root = root or _project_root()
    config_dir = root / "config"
    data_dir = root / "data"

    competitors_data = _load_yaml(config_dir / "competitors.yaml")
    clusters_data = _load_yaml(config_dir / "topic_clusters.yaml")
    forbidden_data = _load_yaml(config_dir / "forbidden_claims.yaml")

    return EngineConfig(
        project_root=root,
        config_dir=config_dir,
        data_dir=data_dir,
        brand_context=_load_text(config_dir / "brand_context.md"),
        style_guide=_load_text(config_dir / "style_guide.md"),
        seo_rules=_load_text(config_dir / "seo_rules.md"),
        aeo_rules=_load_text(config_dir / "aeo_rules.md"),
        competitors=competitors_data,
        topic_clusters=clusters_data,
        forbidden_claims=forbidden_data.get("forbidden_claims", []),
        requires_evidence=forbidden_data.get("requires_evidence", []),
        dry_run=dry_run,
        json_output=json_output,
    )
