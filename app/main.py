"""CLI entrypoint for the Macroscope SEO engine.

Supports both interactive (rich) and headless (JSON) output modes.
Exit codes:
  0 — success
  1 — pipeline error (partial failure)
  2 — fatal error (pipeline could not complete)
  3 — configuration error
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

from .config import load_config
from .orchestrator import PipelineOrchestrator
from .providers import ProviderRegistry
from .storage import RunStore, get_latest_run, list_runs

console = Console()

EXIT_SUCCESS = 0
EXIT_PIPELINE_ERROR = 1
EXIT_FATAL = 2
EXIT_CONFIG_ERROR = 3


def _setup_logging(verbose: bool = False, json_mode: bool = False) -> None:
    """Configure logging. Suppresses rich output in JSON mode."""
    level = logging.DEBUG if verbose else logging.INFO
    if json_mode:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
    else:
        logging.basicConfig(
            level=level,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(console=console, rich_tracebacks=True)],
        )


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option("--json", "json_output", is_flag=True, help="Output JSON only (for CI/schedulers)")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, json_output: bool) -> None:
    """Macroscope SEO Engine — multi-agent content orchestration."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["json_output"] = json_output
    _setup_logging(verbose, json_output)


@cli.command()
@click.option("--dry-run", is_flag=True, help="Run pipeline without external calls")
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=None)
@click.pass_context
def run(ctx: click.Context, dry_run: bool, root: Path | None) -> None:
    """Execute a full pipeline run."""
    json_output = ctx.obj.get("json_output", False)

    try:
        config = load_config(root=root, dry_run=dry_run, json_output=json_output)
    except FileNotFoundError as e:
        if json_output:
            click.echo(json.dumps({"success": False, "error": str(e)}))
        else:
            console.print(f"[red]Config error: {e}[/red]")
        sys.exit(EXIT_CONFIG_ERROR)

    providers = ProviderRegistry()  # uses mocks by default
    orchestrator = PipelineOrchestrator(config, providers)
    summary = orchestrator.run()

    if json_output:
        click.echo(json.dumps(summary.to_concise_json(), indent=2))

    if not summary.success:
        has_fatal = any(not s.success for s in summary.stages if s.stage in ("score_topics", "build_brief", "write_draft"))
        sys.exit(EXIT_FATAL if has_fatal else EXIT_PIPELINE_ERROR)
    sys.exit(EXIT_SUCCESS)


@cli.command(name="score-topics")
@click.option("--cluster", default=None, help="Filter by topic cluster")
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=None)
@click.pass_context
def score_topics(ctx: click.Context, cluster: str | None, root: Path | None) -> None:
    """Score and rank topic candidates (runs stages 1-3 only)."""
    json_output = ctx.obj.get("json_output", False)

    try:
        config = load_config(root=root, json_output=json_output)
    except FileNotFoundError as e:
        console.print(f"[red]Config error: {e}[/red]")
        sys.exit(EXIT_CONFIG_ERROR)

    providers = ProviderRegistry()
    orchestrator = PipelineOrchestrator(config, providers)

    # Run first 3 stages manually
    from datetime import datetime, timezone
    from .schemas import RunSummary
    from .storage import save_run_summary

    orchestrator.store = RunStore(config.data_dir)
    orchestrator.summary = RunSummary(
        run_id=orchestrator.store.run_id,
        started_at=datetime.now(timezone.utc),
    )

    ctx_data: dict = {}
    try:
        result = orchestrator._collect_signals(ctx_data)
        ctx_data.update(result)
        result = orchestrator._generate_topics(ctx_data)
        ctx_data.update(result)
        result = orchestrator._score_topics(ctx_data)
        ctx_data.update(result)
    except Exception as e:
        console.print(f"[red]Scoring failed: {e}[/red]")
        sys.exit(EXIT_FATAL)

    orchestrator.summary.completed_at = datetime.now(timezone.utc)
    save_run_summary(orchestrator.store, orchestrator.summary)

    if json_output:
        scored = ctx_data.get("scored", [])
        output = [
            {
                "title": s.candidate.title,
                "slug": s.candidate.slug,
                "score": s.total_score,
                "selected": s.selected,
                "reasons": s.rejection_reasons,
            }
            for s in scored
        ]
        click.echo(json.dumps(output, indent=2))
    else:
        console.print(f"\n[green]Scoring complete. Run dir: {orchestrator.store.run_dir}[/green]")


@cli.command()
@click.option("--topic", "topic_slug", required=True, help="Topic slug to research")
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=None)
def research(topic_slug: str, root: Path | None) -> None:
    """Build a research brief for a specific topic (not yet standalone)."""
    console.print(f"[yellow]Research for '{topic_slug}' — run full pipeline with: seo-engine run[/yellow]")


@cli.command()
@click.option("--run-id", default=None, help="Run ID to export (default: latest)")
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=None)
def export(run_id: str | None, root: Path | None) -> None:
    """Export a completed run's final article."""
    config = load_config(root=root)
    target_run = run_id or get_latest_run(config.data_dir)
    if not target_run:
        console.print("[red]No runs found[/red]")
        sys.exit(EXIT_PIPELINE_ERROR)
    console.print(f"[yellow]Export for run '{target_run}' — use full pipeline for now: seo-engine run[/yellow]")


@cli.command(name="list-runs")
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=None)
@click.pass_context
def list_runs_cmd(ctx: click.Context, root: Path | None) -> None:
    """List all pipeline runs."""
    config = load_config(root=root)
    runs = list_runs(config.data_dir)
    json_output = ctx.obj.get("json_output", False)

    if not runs:
        if json_output:
            click.echo("[]")
        else:
            console.print("[yellow]No runs found[/yellow]")
        return

    if json_output:
        click.echo(json.dumps(runs))
    else:
        for r in runs:
            console.print(f"  {r}")


if __name__ == "__main__":
    cli()
