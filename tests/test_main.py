"""Tests for CLI JSON error behavior."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from app import main
from app.config import load_config as real_load_config


def test_score_topics_emits_json_error_when_config_load_fails(monkeypatch):
    runner = CliRunner()

    def fake_load_config(*args, **kwargs):
        raise FileNotFoundError("missing config")

    monkeypatch.setattr(main, "load_config", fake_load_config)

    result = runner.invoke(main.cli, ["--json", "score-topics"])

    assert result.exit_code == main.EXIT_CONFIG_ERROR
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert "missing config" in payload["error"]


def test_score_topics_emits_json_error_when_scoring_fails(monkeypatch, tmp_path: Path):
    runner = CliRunner()

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()

    monkeypatch.setattr(main, "load_config", lambda *args, **kwargs: real_load_config(root=tmp_path))
    monkeypatch.setattr(main, "build_provider_registry", lambda config: object())

    class FakeOrchestrator:
        def __init__(self, config, providers):
            self.store = None
            self.summary = None

        def _bootstrap_run(self, ctx):
            raise RuntimeError("boom")

    monkeypatch.setattr(main, "PipelineOrchestrator", FakeOrchestrator)

    result = runner.invoke(main.cli, ["--json", "score-topics", "--root", str(tmp_path)])

    assert result.exit_code == main.EXIT_FATAL
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert "Scoring failed: boom" == payload["error"]
