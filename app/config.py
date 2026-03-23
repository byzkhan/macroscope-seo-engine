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
    research_lookback_days: int = 14
    topic_cooldown_days: int = 45
    min_topic_consensus_score: float = 7.2
    min_topic_authority_score: float = 7.5
    min_brief_quality_score: float = 8.0
    min_draft_quality_score: float = 8.2
    final_jury_average_threshold: float = 9.0
    final_jury_min_threshold: float = 8.0
    technical_accuracy_threshold: float = 9.0
    topic_judge_spread_threshold: float = 1.5
    topic_judge_variance_threshold: float = 0.7
    draft_judge_spread_threshold: float = 1.4
    draft_judge_variance_threshold: float = 0.6
    final_judge_spread_threshold: float = 1.2
    final_judge_variance_threshold: float = 0.45
    optimizer_max_rounds: int = 10
    max_topic_candidates: int = 12
    model_judged_topics: int = 6
    full_panel_topics: int = 2
    writer_blueprints: int = 3
    full_draft_candidates: int = 1
    second_draft_unlock_round: int = 4
    enable_final_fact_check: bool = True
    web_search_stages: list[str] = field(default_factory=lambda: ["research", "fact_check"])
    writer_personas: list[str] = field(default_factory=lambda: ["technical", "pragmatic", "analytical"])
    optimizer_personas: list[str] = field(default_factory=lambda: ["seo", "aeo", "clarity", "technical_accuracy"])
    dry_run: bool = False
    json_output: bool = False
    provider_mode: str = "mock"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-5-mini"
    openai_market_model: str = "gpt-5-mini"
    openai_content_model: str = "gpt-5-mini"
    openai_reasoning_effort: str = "medium"
    openai_enable_web_search: bool = True
    openai_search_context_size: str = "medium"
    openai_timeout_seconds: float = 120.0


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_dotenv(path: Path) -> dict[str, str]:
    """Load a simple .env file without overriding existing process env."""
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key] = value
    except (OSError, ValueError):
        return {}
    return values


def _env_value(name: str, dotenv_values: dict[str, str], default: str | None = None) -> str | None:
    """Read a setting from process env first, then .env, then default."""
    return os.getenv(name, dotenv_values.get(name, default))


def _env_bool_with_dotenv(name: str, dotenv_values: dict[str, str], default: bool) -> bool:
    value = _env_value(name, dotenv_values)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list_with_dotenv(name: str, dotenv_values: dict[str, str], default: str) -> list[str]:
    value = _env_value(name, dotenv_values, default) or default
    return [item.strip() for item in value.split(",") if item.strip()]


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
    dotenv_values = _load_dotenv(root / ".env")

    competitors_data = _load_yaml(config_dir / "competitors.yaml")
    clusters_data = _load_yaml(config_dir / "topic_clusters.yaml")
    forbidden_data = _load_yaml(config_dir / "forbidden_claims.yaml")
    openai_api_key = _env_value("OPENAI_API_KEY", dotenv_values)
    provider_mode = _env_value("SEO_ENGINE_PROVIDER", dotenv_values) or ("openai" if openai_api_key else "mock")
    openai_model = _env_value("OPENAI_MODEL", dotenv_values, "gpt-5-mini") or "gpt-5-mini"

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
        research_lookback_days=int(_env_value("RESEARCH_LOOKBACK_DAYS", dotenv_values, "14") or "14"),
        topic_cooldown_days=int(_env_value("TOPIC_COOLDOWN_DAYS", dotenv_values, "45") or "45"),
        min_topic_consensus_score=float(_env_value("MIN_TOPIC_CONSENSUS_SCORE", dotenv_values, "7.2") or "7.2"),
        min_topic_authority_score=float(_env_value("MIN_TOPIC_AUTHORITY_SCORE", dotenv_values, "7.5") or "7.5"),
        min_brief_quality_score=float(_env_value("MIN_BRIEF_QUALITY_SCORE", dotenv_values, "8.0") or "8.0"),
        min_draft_quality_score=float(_env_value("MIN_DRAFT_QUALITY_SCORE", dotenv_values, "8.2") or "8.2"),
        final_jury_average_threshold=float(_env_value("FINAL_JURY_AVERAGE_THRESHOLD", dotenv_values, "9.0") or "9.0"),
        final_jury_min_threshold=float(_env_value("FINAL_JURY_MIN_THRESHOLD", dotenv_values, "8.0") or "8.0"),
        technical_accuracy_threshold=float(_env_value("TECHNICAL_ACCURACY_THRESHOLD", dotenv_values, "9.0") or "9.0"),
        topic_judge_spread_threshold=float(_env_value("TOPIC_JUDGE_SPREAD_THRESHOLD", dotenv_values, "1.5") or "1.5"),
        topic_judge_variance_threshold=float(_env_value("TOPIC_JUDGE_VARIANCE_THRESHOLD", dotenv_values, "0.7") or "0.7"),
        draft_judge_spread_threshold=float(_env_value("DRAFT_JUDGE_SPREAD_THRESHOLD", dotenv_values, "1.4") or "1.4"),
        draft_judge_variance_threshold=float(_env_value("DRAFT_JUDGE_VARIANCE_THRESHOLD", dotenv_values, "0.6") or "0.6"),
        final_judge_spread_threshold=float(_env_value("FINAL_JUDGE_SPREAD_THRESHOLD", dotenv_values, "1.2") or "1.2"),
        final_judge_variance_threshold=float(_env_value("FINAL_JUDGE_VARIANCE_THRESHOLD", dotenv_values, "0.45") or "0.45"),
        optimizer_max_rounds=int(_env_value("OPTIMIZER_MAX_ROUNDS", dotenv_values, "10") or "10"),
        max_topic_candidates=int(_env_value("MAX_TOPIC_CANDIDATES", dotenv_values, "12") or "12"),
        model_judged_topics=int(_env_value("MODEL_JUDGED_TOPICS", dotenv_values, "6") or "6"),
        full_panel_topics=int(_env_value("FULL_PANEL_TOPICS", dotenv_values, "2") or "2"),
        writer_blueprints=int(_env_value("WRITER_BLUEPRINTS", dotenv_values, "3") or "3"),
        full_draft_candidates=int(_env_value("FULL_DRAFT_CANDIDATES", dotenv_values, "1") or "1"),
        second_draft_unlock_round=int(_env_value("SECOND_DRAFT_UNLOCK_ROUND", dotenv_values, "4") or "4"),
        enable_final_fact_check=_env_bool_with_dotenv("ENABLE_FINAL_FACT_CHECK", dotenv_values, True),
        web_search_stages=_env_list_with_dotenv("WEB_SEARCH_STAGES", dotenv_values, "research,fact_check"),
        writer_personas=[
            persona.strip()
            for persona in (_env_value("WRITER_PERSONAS", dotenv_values, "technical,pragmatic,analytical") or "technical,pragmatic,analytical").split(",")
            if persona.strip()
        ],
        optimizer_personas=[
            persona.strip()
            for persona in (_env_value("OPTIMIZER_PERSONAS", dotenv_values, "seo,aeo,clarity,technical_accuracy") or "seo,aeo,clarity,technical_accuracy").split(",")
            if persona.strip()
        ],
        dry_run=dry_run,
        json_output=json_output,
        provider_mode=provider_mode.lower().strip(),
        openai_api_key=openai_api_key,
        openai_base_url=_env_value("OPENAI_BASE_URL", dotenv_values),
        openai_model=openai_model,
        openai_market_model=_env_value("OPENAI_MARKET_MODEL", dotenv_values, openai_model) or openai_model,
        openai_content_model=_env_value("OPENAI_CONTENT_MODEL", dotenv_values, openai_model) or openai_model,
        openai_reasoning_effort=_env_value("OPENAI_REASONING_EFFORT", dotenv_values, "medium") or "medium",
        openai_enable_web_search=_env_bool_with_dotenv("OPENAI_ENABLE_WEB_SEARCH", dotenv_values, True),
        openai_search_context_size=_env_value("OPENAI_SEARCH_CONTEXT_SIZE", dotenv_values, "medium") or "medium",
        openai_timeout_seconds=float(_env_value("OPENAI_TIMEOUT_SECONDS", dotenv_values, "120") or "120"),
    )


def ensure_live_run_provider(config: EngineConfig, *, context: str) -> None:
    """Reject silent mock fallbacks for real pipeline runs."""
    if config.dry_run:
        return
    if config.provider_mode == "openai":
        if not config.openai_api_key:
            raise ValueError(
                f"{context} requires OPENAI_API_KEY when provider mode is openai."
            )
        return
    raise ValueError(
        f"{context} resolved to provider_mode='{config.provider_mode}'. "
        "Real runs must use OpenAI. Set OPENAI_API_KEY in the environment or project .env. "
        "If you intentionally want mock mode for testing, set SEO_ENGINE_PROVIDER=mock explicitly."
    )
