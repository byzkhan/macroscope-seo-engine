"""Central pipeline orchestrator for the Macroscope SEO engine.

Controls all execution — agents do not coordinate directly.
Each stage takes structured input and returns structured output.
The orchestrator uses a ProviderRegistry to swap mock/real providers
without changing business logic.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import EngineConfig
from .docs_export import export_article
from .prompts import (
    blog_writer_prompt,
    market_watcher_prompt,
    research_brief_prompt,
    seo_aeo_editor_prompt,
    topic_researcher_prompt,
    topic_scorer_prompt,
)
from .providers import ProviderRegistry
from .qa import run_qa, score_seo_aeo
from .scoring import rank_topics, score_topic, select_best
from .schemas import (
    DraftArticle,
    FailureCategory,
    FinalArticle,
    PipelineStage,
    RunSummary,
    ScoredTopic,
    StageResult,
)
from .storage import (
    RunStore,
    append_to_topic_history,
    load_topic_history,
    save_run_summary,
)

logger = logging.getLogger(__name__)
console = Console()

# Stages that abort the pipeline on failure
FATAL_STAGES = {
    PipelineStage.SCORE_TOPICS,
    PipelineStage.BUILD_BRIEF,
    PipelineStage.WRITE_DRAFT,
}


class PipelineOrchestrator:
    """Orchestrates the full content production pipeline."""

    def __init__(
        self,
        config: EngineConfig,
        providers: ProviderRegistry | None = None,
    ):
        self.config = config
        self.providers = providers or ProviderRegistry()
        self.store: RunStore | None = None
        self.summary: RunSummary | None = None

    def run(self) -> RunSummary:
        """Execute the complete pipeline and return a RunSummary."""
        start = datetime.now(timezone.utc)
        self.store = RunStore(self.config.data_dir)
        self.summary = RunSummary(run_id=self.store.run_id, started_at=start)

        if not self.config.json_output:
            console.print(Panel(f"[bold green]Pipeline Run: {self.store.run_id}[/bold green]"))

        stages: list[tuple[PipelineStage, Any]] = [
            (PipelineStage.COLLECT_SIGNALS, self._collect_signals),
            (PipelineStage.GENERATE_TOPICS, self._generate_topics),
            (PipelineStage.SCORE_TOPICS, self._score_topics),
            (PipelineStage.BUILD_BRIEF, self._build_brief),
            (PipelineStage.WRITE_DRAFT, self._write_draft),
            (PipelineStage.QA_OPTIMIZE, self._run_qa_optimize),
            (PipelineStage.EXPORT, self._export),
            (PipelineStage.PERSIST_HISTORY, self._persist_history),
        ]

        ctx: dict[str, Any] = {}

        for stage_enum, stage_fn in stages:
            stage_name = stage_enum.value
            t0 = time.monotonic()
            try:
                if not self.config.json_output:
                    console.print(f"\n[bold cyan]▶ {stage_name}[/bold cyan]")

                result = stage_fn(ctx)
                elapsed = round(time.monotonic() - t0, 2)

                artifact = result.get("artifact") if isinstance(result, dict) else None
                self.summary.stages.append(
                    StageResult(stage=stage_name, success=True, duration_seconds=elapsed, artifact_path=artifact)
                )
                if isinstance(result, dict):
                    ctx.update(result)

                if not self.config.json_output:
                    console.print(f"  [green]✓[/green] {stage_name} ({elapsed:.1f}s)")

            except Exception as exc:
                elapsed = round(time.monotonic() - t0, 2)
                error_msg = f"{stage_name}: {exc}"
                logger.exception("Stage failed: %s", stage_name)

                category = _classify_error(exc)
                self.summary.stages.append(
                    StageResult(
                        stage=stage_name,
                        success=False,
                        duration_seconds=elapsed,
                        error=str(exc),
                        failure_category=category,
                    )
                )
                self.summary.errors.append(error_msg)

                if not self.config.json_output:
                    console.print(f"  [red]✗[/red] {stage_name}: {exc}")

                if stage_enum in FATAL_STAGES:
                    if not self.config.json_output:
                        console.print("[bold red]Fatal stage failure — aborting pipeline[/bold red]")
                    break

        self.summary.completed_at = datetime.now(timezone.utc)
        if self.store:
            save_run_summary(self.store, self.summary)

        if not self.config.json_output:
            self._print_summary()

        return self.summary

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def _collect_signals(self, ctx: dict) -> dict:
        """Stage 1: Collect market signals from the configured provider."""
        topic_clusters = self.config.topic_clusters
        themes = list((topic_clusters.get("clusters", {})).keys())
        report = self.providers.market_signals.collect(themes)

        assert self.store is not None
        path = self.store.save_json("market_signals.json", report)
        self.summary.artifacts["market_signals"] = str(path)  # type: ignore[union-attr]
        return {"market_signals": report, "artifact": str(path)}

    def _generate_topics(self, ctx: dict) -> dict:
        """Stage 2: Generate topic candidates via content provider."""
        market_signals = ctx.get("market_signals")
        topic_history = load_topic_history(self.config.data_dir)

        prompt = topic_researcher_prompt(
            brand_context=self.config.brand_context,
            topic_clusters=self.config.topic_clusters,
            topic_history=topic_history,
            market_signals=market_signals,
        )
        candidates = self.providers.content_generation.generate_topics(prompt, market_signals)

        assert self.store is not None
        path = self.store.save_json("topic_candidates.json", candidates)
        self.summary.artifacts["topic_candidates"] = str(path)  # type: ignore[union-attr]
        return {"candidates": candidates, "artifact": str(path)}

    def _score_topics(self, ctx: dict) -> dict:
        """Stage 3: Score candidates and select the best topic."""
        candidates = ctx["candidates"]
        archive_path = self.config.data_dir / "topic_history.json"

        # Score each candidate with mock raw scores derived from candidate metadata
        scored: list[ScoredTopic] = []
        for candidate in candidates:
            raw = _derive_raw_scores(candidate)
            scored.append(score_topic(candidate, raw, archive_path))

        ranked = rank_topics(scored)
        best = select_best(ranked)

        if best is None:
            raise ValueError("All topic candidates were rejected — no viable topic found")

        assert self.store is not None
        path = self.store.save_json("scored_topics.json", ranked)
        self.store.save_json("selected_topic.json", best)
        self.summary.artifacts["scored_topics"] = str(path)  # type: ignore[union-attr]
        self.summary.topic_selected = best.candidate.title  # type: ignore[union-attr]

        if not self.config.json_output:
            self._print_scoring_table(ranked)

        return {"scored": ranked, "selected": best, "artifact": str(path)}

    def _build_brief(self, ctx: dict) -> dict:
        """Stage 4: Build a research brief for the selected topic."""
        selected: ScoredTopic = ctx["selected"]

        prompt = research_brief_prompt(
            topic=selected,
            brand_context=self.config.brand_context,
            style_guide=self.config.style_guide,
            forbidden_claims=self.config.forbidden_claims,
            topic_history=load_topic_history(self.config.data_dir),
        )
        brief = self.providers.content_generation.generate_brief(prompt, selected)

        assert self.store is not None
        path = self.store.save_json("research_brief.json", brief)
        self.summary.artifacts["research_brief"] = str(path)  # type: ignore[union-attr]
        return {"brief": brief, "artifact": str(path)}

    def _write_draft(self, ctx: dict) -> dict:
        """Stage 5: Write a draft article from the brief."""
        brief = ctx["brief"]

        prompt = blog_writer_prompt(
            brief=brief,
            brand_context=self.config.brand_context,
            style_guide=self.config.style_guide,
        )
        content_md = self.providers.content_generation.generate_draft(prompt, brief)

        draft = DraftArticle(
            title=brief.title_options[0],
            slug=brief.topic.slug,
            content_md=content_md,
            word_count=len(content_md.split()),
            brief_hash=brief.brief_hash(),
        )

        assert self.store is not None
        self.store.save_markdown("draft.md", content_md)
        path = self.store.save_json("draft_meta.json", draft)
        self.summary.artifacts["draft"] = str(path)  # type: ignore[union-attr]
        return {"draft": draft, "brief": brief, "artifact": str(path)}

    def _run_qa_optimize(self, ctx: dict) -> dict:
        """Stage 6: Run SEO/AEO QA and produce the final article."""
        draft: DraftArticle = ctx["draft"]
        brief = ctx["brief"]

        # Run the real SEO/AEO scorer
        seo_score = score_seo_aeo(
            content=draft.content_md,
            meta_description=brief.meta_description,
            primary_keyword=brief.primary_keyword,
            slug=draft.slug,
        )

        # Run the real QA suite
        qa_result = run_qa(
            content=draft.content_md,
            slug=draft.slug,
            meta_description=brief.meta_description,
            forbidden_claims=self.config.forbidden_claims,
            do_not_say=brief.do_not_say,
            min_word_count=self.config.min_word_count,
            max_word_count=self.config.max_word_count,
            min_internal_links=self.config.min_internal_links,
        )

        # Extract internal links from content
        internal_links = re.findall(
            r"\[.*?\]\(((?:/|https?://macroscope\.com).*?)\)", draft.content_md
        )

        final = FinalArticle(
            title=draft.title,
            slug=draft.slug,
            content_md=draft.content_md,
            meta_description=brief.meta_description,
            word_count=draft.word_count,
            seo_aeo_score=seo_score,
            faqs_present=any(c.name == "faq_section" and c.passed for c in qa_result.checks),
            internal_links=internal_links,
        )

        assert self.store is not None
        self.store.save_markdown("final.md", final.content_md)
        path = self.store.save_json("meta.json", final)
        self.store.save_json("qa_result.json", qa_result.to_dict())

        self.summary.final_score = seo_score.total  # type: ignore[union-attr]
        self.summary.final_grade = seo_score.grade  # type: ignore[union-attr]
        self.summary.word_count = final.word_count  # type: ignore[union-attr]
        self.summary.artifacts["final"] = str(path)  # type: ignore[union-attr]

        if not self.config.json_output:
            self._print_qa_results(qa_result, seo_score)

        return {"final": final, "qa": qa_result, "artifact": str(path)}

    def _export(self, ctx: dict) -> dict:
        """Stage 7: Export final article to local files and optional external targets."""
        final: FinalArticle = ctx["final"]
        assert self.store is not None

        results = export_article(
            article=final,
            run_summary=self.summary,  # type: ignore[arg-type]
            output_dir=self.store.run_dir,
            google_docs_provider=(
                self.providers.document_export
                if not isinstance(self.providers.document_export, type(self.providers.document_export))
                else None
            ),
        )

        export_meta = [r.to_dict() for r in results]
        path = self.store.save_json("export_results.json", export_meta)
        self.summary.artifacts["export"] = str(path)  # type: ignore[union-attr]
        return {"export_results": results, "artifact": str(path)}

    def _persist_history(self, ctx: dict) -> dict:
        """Stage 8: Append to topic archive."""
        final: FinalArticle = ctx.get("final")  # type: ignore[assignment]
        if final is None:
            return {}

        selected: ScoredTopic | None = ctx.get("selected")  # type: ignore[assignment]
        cluster = selected.candidate.cluster if selected else "unknown"

        append_to_topic_history(
            data_dir=self.config.data_dir,
            slug=final.slug,
            title=final.title,
            keywords=[],  # keywords from brief would be injected here
            cluster=cluster,
        )
        return {}

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def _print_scoring_table(self, scored: list[ScoredTopic]) -> None:
        """Print a rich table of scored topics."""
        table = Table(title="Topic Scores", show_lines=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("Title", max_width=50)
        table.add_column("Score", justify="right")
        table.add_column("Status")
        table.add_column("Reasons", max_width=40)

        for i, st in enumerate(scored[:10], 1):
            status = "[green]SELECTED[/green]" if st.selected else (
                "[red]REJECTED[/red]" if st.rejection_reasons else "[yellow]PASSED[/yellow]"
            )
            reasons = "; ".join(st.rejection_reasons[:2]) if st.rejection_reasons else "-"
            table.add_row(str(i), st.candidate.title, f"{st.total_score:.1f}", status, reasons)

        console.print(table)

    def _print_qa_results(self, qa_result, seo_score) -> None:
        """Print QA results and SEO/AEO scores."""
        table = Table(title="SEO/AEO Scores")
        table.add_column("Dimension")
        table.add_column("Score", justify="right")

        for field_name in [
            "title_score", "meta_description_score", "keyword_density_score",
            "heading_structure_score", "internal_links_score", "faq_presence_score",
            "direct_answer_score", "readability_score", "content_depth_score",
            "freshness_signals_score",
        ]:
            val = getattr(seo_score, field_name)
            label = field_name.replace("_score", "").replace("_", " ").title()
            color = "green" if val >= 7 else ("yellow" if val >= 5 else "red")
            table.add_row(label, f"[{color}]{val:.1f}[/{color}]")

        table.add_row("[bold]Total[/bold]", f"[bold]{seo_score.total:.1f}/100 ({seo_score.grade})[/bold]")
        console.print(table)

        console.print(f"\nQA: {qa_result.summary}")
        for check in qa_result.failures:
            console.print(f"  [red]✗[/red] {check.name}: {check.message}")

    def _print_summary(self) -> None:
        """Print final run summary."""
        assert self.summary is not None
        panel_style = "green" if self.summary.success else "red"
        lines = [
            f"Run ID: {self.summary.run_id}",
            f"Duration: {self.summary.total_duration_seconds:.1f}s",
            f"Stages: {len(self.summary.stages_completed)}/{len(self.summary.stages)} completed",
        ]
        if self.summary.topic_selected:
            lines.append(f"Topic: {self.summary.topic_selected}")
        if self.summary.final_score is not None:
            lines.append(f"SEO/AEO: {self.summary.final_score:.1f}/100 ({self.summary.final_grade})")
        if self.summary.word_count:
            lines.append(f"Words: {self.summary.word_count}")
        if self.summary.errors:
            lines.append(f"Errors: {len(self.summary.errors)}")

        console.print(Panel("\n".join(lines), title="Run Summary", border_style=panel_style))

        if self.store:
            console.print(f"\nArtifacts: {self.store.run_dir}")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _derive_raw_scores(candidate) -> dict[str, float]:
    """Derive plausible raw scores from candidate metadata for mock scoring.

    In production, these scores would come from the topic-scorer agent
    backed by real keyword data, SERP analysis, and competitive intelligence.
    """
    import hashlib
    seed = int(hashlib.md5(candidate.slug.encode()).hexdigest()[:8], 16)

    # Deterministic but varied scores
    base = (seed % 20) + 50  # 50-69 range

    scores = {
        "business_relevance": min(25.0, 14.0 + (seed % 12)),
        "search_opportunity": min(20.0, 10.0 + (seed % 11)),
        "aeo_fit": min(15.0, 7.0 + (seed % 9)),
        "freshness": min(10.0, 3.0 + (seed % 8)) if candidate.freshness_signal else 3.0,
        "authority_to_win": min(15.0, 8.0 + (seed % 8)),
        "production_ease": min(5.0, 2.0 + (seed % 4)),
    }
    return scores


def _classify_error(exc: Exception) -> FailureCategory:
    """Classify an exception into a failure category."""
    from pydantic import ValidationError
    if isinstance(exc, ValidationError):
        return FailureCategory.VALIDATION_ERROR
    if isinstance(exc, (OSError, IOError)):
        return FailureCategory.IO_ERROR
    if isinstance(exc, TimeoutError):
        return FailureCategory.TIMEOUT
    if isinstance(exc, ValueError) and "rejected" in str(exc).lower():
        return FailureCategory.SCORING_REJECTION
    return FailureCategory.UNKNOWN
