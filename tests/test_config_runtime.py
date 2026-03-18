"""Tests for runtime config loading and live-provider enforcement."""

from pathlib import Path

import pytest

from app.config import ensure_live_run_provider, load_config
from app.dashboard_runtime import DashboardRunManager


def test_load_config_reads_openai_settings_from_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_root = tmp_path
    (project_root / "config").mkdir()
    (project_root / "data").mkdir()
    (project_root / ".env").write_text(
        "OPENAI_API_KEY=test-key\nSEO_ENGINE_PROVIDER=openai\nOPENAI_MODEL=gpt-5.1-mini\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SEO_ENGINE_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    config = load_config(root=project_root)

    assert config.provider_mode == "openai"
    assert config.openai_api_key == "test-key"
    assert config.openai_model == "gpt-5.1-mini"


def test_load_config_ignores_invalid_utf8_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_root = tmp_path
    (project_root / "config").mkdir()
    (project_root / "data").mkdir()
    (project_root / ".env").write_bytes(b"OPENAI_API_KEY=\xff\xfe")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SEO_ENGINE_PROVIDER", raising=False)

    config = load_config(root=project_root)

    assert config.openai_api_key is None
    assert config.provider_mode == "mock"


def test_ensure_live_run_provider_rejects_mock_fallback(tmp_path: Path):
    project_root = tmp_path
    (project_root / "config").mkdir()
    (project_root / "data").mkdir()

    config = load_config(root=project_root)

    with pytest.raises(ValueError, match="Real runs must use OpenAI"):
        ensure_live_run_provider(config, context="Dashboard pipeline run")


def test_dashboard_start_run_rejects_silent_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_root = tmp_path
    (project_root / "config").mkdir()
    (project_root / "data").mkdir()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SEO_ENGINE_PROVIDER", raising=False)

    manager = DashboardRunManager(project_root)

    with pytest.raises(RuntimeError, match="Real runs must use OpenAI"):
        manager.start_run(trigger="manual")
