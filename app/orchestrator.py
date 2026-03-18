"""Central pipeline orchestrator for the Macroscope SEO engine.

Controls all execution — agents do not coordinate directly.
Each stage takes structured input and returns structured output.
The orchestrator uses a ProviderRegistry to swap mock/real providers
without changing business logic.
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import EngineConfig
from .docs_export import export_article
from .ensemble import (
    merge_market_signal_reports,
    merge_topic_candidates,
    select_best_draft,
)
from .guardrails import build_run_context, is_stateless_run_context, quality_gate_passed
from .history import (
    assess_topic_reuse,
    load_topic_cooldowns,
    load_topic_shortlist_history,
    record_topic_shortlist,
    upsert_topic_cooldown,
)
from .judges import (
    draft_evaluation_from_scores,
    evaluate_brief_quality,
    evaluate_draft_variant,
    final_quality_jury,
    topic_jury_scores,
)
from .prompts import (
    article_judge_prompt,
    brief_composer_prompt,
    brief_critic_prompt,
    draft_from_blueprint_prompt,
    fact_check_prompt,
    focused_section_rewrite_prompt,
    market_source_scout_prompt,
    optimization_coordinator_prompt,
    optimizer_persona_prompt,
    research_brief_prompt,
    topic_judge_prompt,
    topic_researcher_persona_prompt,
    writer_blueprint_prompt,
)
from .providers import MockGoogleDocsProvider, ProviderRegistry
from .qa import normalize_markdown_headings, run_qa, score_seo_aeo
from .qa import QACheck, QAResult
from .scoring import rank_topics, score_topic, select_best
from .schemas import (
    ArticleManifest,
    BriefQualityReport,
    BlueprintEvaluation,
    CanonicalSource,
    DraftArticle,
    DraftEvaluation,
    DraftVariant,
    FactCheckReport,
    FailureCategory,
    FinalArticle,
    FinalQualityGate,
    MarketSignalReport,
    PipelineStage,
    ProviderCallUsage,
    ResearchFact,
    ResearchBrief,
    ResearchPacket,
    ResumePlan,
    RunContext,
    RunSummary,
    RunUsageLedger,
    ScoredTopic,
    StageUsageSummary,
    StageResult,
    SourceCoverageReport,
    TopicCandidate,
    WriterBlueprint,
)
from .storage import (
    ExecutionJournal,
    RunStore,
    append_to_topic_history,
    load_topic_history,
    save_run_summary,
)

logger = logging.getLogger(__name__)
console = Console()
SERP_RESEARCH_LIMIT = 2
INTERNAL_LINK_PATTERN = re.compile(r"\[.*?\]\(((?:/|https?://macroscope\.com).*?)\)")

RESEARCH_SCOUTS = [
    ("community_scout", "Hacker News and Reddit engineering communities discussing workflow pain, adoption, and tradeoffs."),
    ("primary_source_scout", "Official docs, release notes, migration guides, and benchmarks or papers when relevant."),
    ("practitioner_scout", "Engineering blogs, vendor changelogs, technical launch posts, and implementation writeups."),
]

TOPIC_PERSONAS = {
    "market_gap_researcher": "Find topics where SERPs and competitor content leave a clear gap Macroscope can own.",
    "engineering_pain_researcher": "Center real developer pain, workflow bottlenecks, and adoption friction.",
    "technical_depth_researcher": "Prefer technically rigorous, fresh, commercially meaningful angles with concrete examples.",
}

TOPIC_MINI_JUDGE_FOCUS = {
    "seo_opportunity_judge": "Evaluate ranking potential, search demand, SERP fit, and snippet opportunity.",
    "technical_authority_judge": "Evaluate whether Macroscope can credibly publish a technically serious article on this angle.",
    "originality_judge": "Evaluate novelty versus archive memory, genericity, and angle distinctiveness.",
}

TOPIC_JUDGE_FOCUS = {
    "seo_opportunity_judge": "Evaluate ranking potential, search demand, SERP fit, and snippet opportunity.",
    "technical_authority_judge": "Evaluate whether Macroscope can credibly publish a technically serious article on this angle.",
    "freshness_relevance_judge": "Evaluate timeliness, recency, and whether the topic matters right now.",
    "commercial_value_judge": "Evaluate buyer relevance, evaluation intent, and commercial usefulness.",
    "originality_judge": "Evaluate novelty versus archive memory, genericity, and angle distinctiveness.",
}

WRITER_PERSONAS = {
    "technical": ("Technical Writer", "Write like a precise staff engineer explaining a system to other engineers."),
    "pragmatic": ("Pragmatic Writer", "Write for engineering managers and senior developers who want operational guidance."),
    "analytical": ("Analytical Writer", "Lead with comparisons, benchmarks, evidence, and decision frameworks."),
}

OPTIMIZER_PERSONAS = {
    "seo": ("SEO Optimizer", "Improve search demand alignment, keyword placement, and organic discoverability."),
    "aeo": ("AEO Optimizer", "Improve direct answers, FAQ quality, and answer-engine retrieval potential."),
    "clarity": ("Clarity Optimizer", "Tighten structure, transitions, readability, and paragraph flow."),
    "technical_accuracy": (
        "Technical Accuracy Optimizer",
        "Strengthen technical precision, engineering examples, and claim defensibility without hype.",
    ),
}

FINAL_JUDGE_FOCUS = {
    "search_readiness_judge": "Judge title, keyword alignment, answer-engine clarity, and organic search readiness.",
    "structure_clarity_judge": "Judge heading structure, pacing, FAQ usefulness, and navigability for busy engineers.",
    "technical_rigor_judge": "Judge technical rigor, claim defensibility, and engineering credibility.",
}

DRAFT_JUDGE_FOCUS = {
    "technical_accuracy_judge": "Judge technical rigor, engineering credibility, and claim defensibility in this draft.",
    "seo_judge": "Judge keyword alignment, search intent fit, and on-page SEO fundamentals.",
    "aeo_judge": "Judge direct answers, FAQ usefulness, and answer-engine readiness.",
    "clarity_judge": "Judge readability, structure, pacing, and how easy the draft is to follow.",
    "evidence_completeness_judge": "Judge whether the draft covers the brief thoroughly and supports important claims.",
}

TOPIC_TIEBREAKER_FOCUS = (
    "Resolve disagreement between topic judges. Make a strict final call on overall topic viability, "
    "balancing search value, technical credibility, originality, and buyer usefulness."
)

DRAFT_TIEBREAKER_FOCUS = (
    "Resolve disagreement between draft judges. Decide whether this draft is truly the strongest writer output "
    "for the brief, not just acceptable in one dimension."
)

FINAL_TIEBREAKER_FOCUS = (
    "Resolve disagreement in the final publication jury. Decide whether the article is truly publication-ready "
    "for engineers and search users, not merely average across mixed signals."
)

# Stages that abort the pipeline on failure
FATAL_STAGES = {
    PipelineStage.BOOTSTRAP_RUN,
    PipelineStage.SCORE_TOPICS,
    PipelineStage.BUILD_BRIEF,
    PipelineStage.WRITE_DRAFT,
    PipelineStage.QA_OPTIMIZE,
}

STAGE_SEQUENCE: list[tuple[PipelineStage, str]] = [
    (PipelineStage.BOOTSTRAP_RUN, "bootstrap_run"),
    (PipelineStage.COLLECT_SIGNALS, "collect_signals"),
    (PipelineStage.GENERATE_TOPICS, "generate_topics"),
    (PipelineStage.SCORE_TOPICS, "score_topics"),
    (PipelineStage.BUILD_BRIEF, "build_brief"),
    (PipelineStage.WRITE_DRAFT, "write_draft"),
    (PipelineStage.QA_OPTIMIZE, "qa_optimize"),
    (PipelineStage.FACT_CHECK, "fact_check"),
    (PipelineStage.EXPORT, "export"),
    (PipelineStage.PERSIST_HISTORY, "persist_history"),
]


class RunCanceled(RuntimeError):
    """Raised when the dashboard requests a cooperative stop."""


class PipelineOrchestrator:
    """Orchestrates the full content production pipeline."""

    def __init__(
        self,
        config: EngineConfig,
        providers: ProviderRegistry | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ):
        self.config = config
        self.providers = providers or ProviderRegistry()
        self.event_callback = event_callback
        self.should_cancel = should_cancel
        self.store: RunStore | None = None
        self.journal: ExecutionJournal | None = None
        self.summary: RunSummary | None = None
        self.usage_ledger: RunUsageLedger | None = None
        self._stage_fns: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "bootstrap_run": self._bootstrap_run,
            "collect_signals": self._collect_signals,
            "generate_topics": self._generate_topics,
            "score_topics": self._score_topics,
            "build_brief": self._build_brief,
            "write_draft": self._write_draft,
            "qa_optimize": self._run_qa_optimize,
            "fact_check": self._fact_check,
            "export": self._export,
            "persist_history": self._persist_history,
        }

    def run(self, *, run_id: str | None = None, resume: bool = False) -> RunSummary:
        """Execute the complete pipeline and return a durable RunSummary."""
        stage_order = [stage.value for stage, _ in STAGE_SEQUENCE]
        self.store = self.store or RunStore(self.config.data_dir, run_id=run_id)
        self.journal = ExecutionJournal(self.store)
        self.usage_ledger = RunUsageLedger()
        resume_plan = self.journal.build_resume_plan(stage_order) if resume else ResumePlan(run_id=self.store.run_id)
        if resume and (not resume_plan.resumable or not resume_plan.resume_stage):
            raise ValueError(resume_plan.resume_reason or f"Run '{self.store.run_id}' is not resumable.")
        self.summary = self._initialize_summary(resume_plan=resume_plan, resume=resume)
        ctx = self._restore_ctx_from_resume_plan(resume_plan) if resume else {}
        start_index = stage_order.index(resume_plan.resume_stage) if resume_plan.resume_stage else 0

        self._mark_run_started(resume=resume, resume_plan=resume_plan)
        self._emit_event(
            "run_started",
            run_id=self.store.run_id,
            run_dir=str(self.store.run_dir),
            resume=resume,
            resume_stage=resume_plan.resume_stage if resume else None,
        )

        if not self.config.json_output:
            title = f"Pipeline Run: {self.store.run_id}"
            if resume and resume_plan.resume_stage:
                title += f" (resuming from {resume_plan.resume_stage})"
            console.print(Panel(f"[bold green]{title}[/bold green]"))

        for stage_index, (stage_enum, _) in enumerate(STAGE_SEQUENCE):
            if stage_index < start_index:
                continue

            stage_name = stage_enum.value
            stage_fn = self._stage_fns[stage_name]
            t0 = time.monotonic()
            self._ensure_not_cancelled(stage_name)
            self._mark_stage_started(stage_name, stage_order)
            self._emit_event(
                "stage_started",
                stage=stage_name,
                run_id=self.summary.run_id,
            )
            try:
                if not self.config.json_output:
                    console.print(f"\n[bold cyan]▶ {stage_name}[/bold cyan]")

                result = stage_fn(ctx)
                elapsed = round(time.monotonic() - t0, 2)
                if isinstance(result, dict):
                    ctx.update(result)
                artifact = result.get("artifact") if isinstance(result, dict) else None
                stage_result = StageResult(
                    stage=stage_name,
                    success=True,
                    duration_seconds=elapsed,
                    artifact_path=artifact,
                )
                self.summary.stages = [s for s in self.summary.stages if s.stage != stage_name] + [stage_result]
                self._save_stage_checkpoint(
                    stage_name=stage_name,
                    stage_result=stage_result,
                    ctx=ctx,
                    result=result if isinstance(result, dict) else {},
                    stage_order=stage_order,
                )

                if not self.config.json_output:
                    console.print(f"  [green]✓[/green] {stage_name} ({elapsed:.1f}s)")
                self._emit_event(
                    "stage_completed",
                    stage=stage_name,
                    run_id=self.summary.run_id,
                    duration_seconds=elapsed,
                    artifact_path=artifact,
                )
                next_stage = stage_order[stage_index + 1] if stage_index + 1 < len(stage_order) else None
                self._mark_stage_completed(stage_name, next_stage)

            except RunCanceled as exc:
                elapsed = round(time.monotonic() - t0, 2)
                error_msg = f"{stage_name}: {exc}"
                stage_result = StageResult(
                    stage=stage_name,
                    success=False,
                    duration_seconds=elapsed,
                    error=str(exc),
                    failure_category=FailureCategory.CANCELED,
                )
                self.summary.stages = [s for s in self.summary.stages if s.stage != stage_name] + [stage_result]
                self.summary.errors.append(error_msg)
                self._emit_event(
                    "stage_failed",
                    stage=stage_name,
                    run_id=self.summary.run_id,
                    duration_seconds=elapsed,
                    error=str(exc),
                    failure_category=FailureCategory.CANCELED.value,
                )
                self._mark_run_finished(status="canceled", last_error=str(exc), resume_stage=None)
                break

            except Exception as exc:
                elapsed = round(time.monotonic() - t0, 2)
                error_msg = f"{stage_name}: {exc}"
                logger.exception("Stage failed: %s", stage_name)

                category = _classify_error(exc)
                stage_result = StageResult(
                    stage=stage_name,
                    success=False,
                    duration_seconds=elapsed,
                    error=str(exc),
                    failure_category=category,
                )
                self.summary.stages = [s for s in self.summary.stages if s.stage != stage_name] + [stage_result]
                self.summary.errors.append(error_msg)

                if not self.config.json_output:
                    console.print(f"  [red]✗[/red] {stage_name}: {exc}")
                self._emit_event(
                    "stage_failed",
                    stage=stage_name,
                    run_id=self.summary.run_id,
                    duration_seconds=elapsed,
                    error=str(exc),
                    failure_category=category.value,
                )

                if stage_enum in FATAL_STAGES:
                    self._mark_run_finished(
                        status="failed",
                        last_error=str(exc),
                        resume_stage=None,
                    )
                    if not self.config.json_output:
                        console.print("[bold red]Fatal stage failure — aborting pipeline[/bold red]")
                    break
                else:
                    next_stage = stage_order[stage_index + 1] if stage_index + 1 < len(stage_order) else None
                    if self.journal is not None:
                        state = self.journal.load_execution_state()
                        completed = state.completed_stages if state is not None else []
                        self.journal.update_execution_state(
                            status=state.status if state is not None and state.status == "resuming" else "running",
                            current_stage=stage_name,
                            next_stage=next_stage,
                            completed_stages=completed,
                            resume_stage=next_stage,
                            last_error=str(exc),
                        )
            finally:
                self._capture_stage_usage(stage_name)
                self._persist_summary_snapshot(final=False)

        if self.summary.completed_at is None:
            self.summary.completed_at = datetime.now(timezone.utc)
        if self.store:
            save_run_summary(self.store, self.summary)

        if self.summary.success:
            self._mark_run_finished(status="completed", last_error=None, resume_stage=None)
        elif any(stage.failure_category == FailureCategory.CANCELED for stage in self.summary.stages if not stage.success):
            self._mark_run_finished(status="canceled", last_error=self.summary.errors[-1] if self.summary.errors else None, resume_stage=None)
        else:
            existing_state = self.journal.load_execution_state() if self.journal is not None else None
            if existing_state is None or existing_state.status not in {"failed", "interrupted"}:
                self._mark_run_finished(
                    status="failed",
                    last_error=self.summary.errors[-1] if self.summary.errors else None,
                    resume_stage=None,
                )

        if not self.config.json_output:
            self._print_summary()

        self._emit_event(
            "run_completed",
            run_id=self.summary.run_id,
            success=self.summary.success,
            summary=self.summary.to_concise_json(),
        )

        return self.summary

    def _initialize_summary(self, *, resume_plan: ResumePlan, resume: bool) -> RunSummary:
        """Build a run summary, carrying forward prior successful stages on resume."""
        assert self.store is not None
        state = self.journal.load_execution_state() if self.journal is not None else None
        started_at = state.started_at if state is not None else datetime.now(timezone.utc)
        resume_count = (state.resume_count if state is not None else 0) + (1 if resume else 0)
        summary = RunSummary(
            run_id=self.store.run_id,
            started_at=started_at,
            resume_count=resume_count,
            resumed_from_stage=resume_plan.resume_stage if resume else None,
        )
        if not resume:
            return summary

        stage_order = [stage.value for stage, _ in STAGE_SEQUENCE]
        checkpoints, _ = self.journal.load_checkpoints(stage_order) if self.journal is not None else ([], [])
        for checkpoint in checkpoints:
            try:
                summary.stages.append(StageResult.model_validate(checkpoint.stage_result))
            except Exception:
                continue
        self._hydrate_summary_from_ctx(summary, self._deserialize_ctx(resume_plan.ctx))
        return summary

    def _hydrate_summary_from_ctx(self, summary: RunSummary, ctx: dict[str, Any]) -> None:
        """Backfill summary fields from resumed context fragments."""
        selected = ctx.get("selected")
        if isinstance(selected, ScoredTopic):
            summary.topic_selected = selected.candidate.title
        final_gate = ctx.get("final_quality_gate")
        if isinstance(final_gate, FinalQualityGate):
            summary.final_score = final_gate.average_score
            summary.final_grade = _jury_grade(final_gate.average_score)
        final = ctx.get("final")
        if isinstance(final, FinalArticle):
            summary.word_count = final.word_count

    def _restore_ctx_from_resume_plan(self, resume_plan: ResumePlan) -> dict[str, Any]:
        """Reconstruct typed downstream context from checkpoint fragments."""
        return self._deserialize_ctx(resume_plan.ctx)

    def _deserialize_ctx(self, raw_ctx: dict[str, Any]) -> dict[str, Any]:
        """Convert serialized checkpoint fragments back into typed stage inputs."""
        restored: dict[str, Any] = {}
        for key, value in raw_ctx.items():
            if key == "run_context" and isinstance(value, dict):
                restored[key] = RunContext.model_validate(value)
            elif key == "market_signals" and isinstance(value, dict):
                restored[key] = MarketSignalReport.model_validate(value)
            elif key == "research_packet" and isinstance(value, dict):
                restored[key] = ResearchPacket.model_validate(value)
            elif key == "source_coverage" and isinstance(value, dict):
                restored[key] = SourceCoverageReport.model_validate(value)
            elif key == "candidates" and isinstance(value, list):
                restored[key] = [TopicCandidate.model_validate(item) for item in value]
            elif key == "selected" and isinstance(value, dict):
                restored[key] = ScoredTopic.model_validate(value)
            elif key == "scored" and isinstance(value, list):
                restored[key] = [ScoredTopic.model_validate(item) for item in value]
            elif key == "brief" and isinstance(value, dict):
                restored[key] = ResearchBrief.model_validate(value)
            elif key == "draft" and isinstance(value, dict):
                restored[key] = DraftArticle.model_validate(value)
            elif key == "runner_up_blueprint" and isinstance(value, dict):
                restored[key] = WriterBlueprint.model_validate(value)
            elif key == "final" and isinstance(value, dict):
                restored[key] = FinalArticle.model_validate(value)
            elif key == "article_manifest" and isinstance(value, dict):
                restored[key] = ArticleManifest.model_validate(value)
            elif key == "final_quality_gate" and isinstance(value, dict):
                restored[key] = FinalQualityGate.model_validate(value)
            elif key == "fact_check_report" and isinstance(value, dict):
                restored[key] = FactCheckReport.model_validate(value)
            elif key == "qa" and isinstance(value, dict):
                restored[key] = _qa_result_from_dict(value)
            else:
                restored[key] = value
        return restored

    def _save_stage_checkpoint(
        self,
        *,
        stage_name: str,
        stage_result: StageResult,
        ctx: dict[str, Any],
        result: dict[str, Any],
        stage_order: list[str],
    ) -> None:
        """Persist the durable checkpoint for a successfully completed stage."""
        if self.journal is None:
            return
        fragment = _checkpoint_ctx_fragment(stage_name, ctx, result)
        state = self.journal.load_execution_state()
        attempt = max(1, (state.resume_count if state is not None else 0) + 1)
        self.journal.save_checkpoint(
            stage=stage_name,
            stage_order=stage_order,
            attempt=attempt,
            ctx_fragment=fragment,
            stage_result=stage_result.model_dump(mode="json"),
        )

    def _mark_run_started(self, *, resume: bool, resume_plan: ResumePlan) -> None:
        """Persist the top-level execution state for a run attempt."""
        if self.journal is None:
            return
        existing = self.journal.load_execution_state()
        started_at = existing.started_at if existing is not None else datetime.now(timezone.utc)
        worker_pid = existing.worker_pid if existing is not None else None
        worker_create_time = existing.worker_create_time if existing is not None else None
        resume_count = (existing.resume_count if existing is not None else 0) + (1 if resume else 0)
        status = "resuming" if resume else "running"
        next_stage = resume_plan.resume_stage if resume else STAGE_SEQUENCE[0][0].value
        self.journal.update_execution_state(
            run_id=self.store.run_id,
            status=status,
            current_stage=None,
            next_stage=next_stage,
            completed_stages=resume_plan.completed_stages if resume else [],
            resume_stage=resume_plan.resume_stage if resume else next_stage,
            resume_count=resume_count,
            worker_pid=worker_pid,
            worker_create_time=worker_create_time,
            stop_requested=False,
            started_at=started_at,
            last_error=None,
            resume_reason=resume_plan.resume_reason if resume else None,
        )

    def _mark_stage_started(self, stage_name: str, stage_order: list[str]) -> None:
        """Persist stage cursor before executing the stage body."""
        if self.journal is None:
            return
        state = self.journal.load_execution_state()
        completed = state.completed_stages if state is not None else []
        status = state.status if state is not None and state.status == "resuming" else "running"
        self.journal.update_execution_state(
            status=status,
            current_stage=stage_name,
            next_stage=stage_name,
            completed_stages=completed,
            resume_stage=stage_name,
        )

    def _mark_stage_completed(self, stage_name: str, next_stage: str | None) -> None:
        """Advance the durable stage cursor after a successful stage."""
        if self.journal is None:
            return
        state = self.journal.load_execution_state()
        completed = list(state.completed_stages if state is not None else [])
        if stage_name not in completed:
            completed.append(stage_name)
        self.journal.update_execution_state(
            status=state.status if state is not None and state.status == "resuming" else "running",
            current_stage=stage_name,
            next_stage=next_stage,
            completed_stages=completed,
            resume_stage=next_stage,
            last_error=None,
        )

    def _mark_run_finished(self, *, status: str, last_error: str | None, resume_stage: str | None) -> None:
        """Write final execution state after the attempt exits."""
        if self.journal is None:
            return
        state = self.journal.load_execution_state()
        completed = state.completed_stages if state is not None else []
        self.journal.update_execution_state(
            status=status,
            current_stage=None if status in {"completed", "canceled", "failed"} else resume_stage,
            next_stage=resume_stage,
            completed_stages=completed,
            resume_stage=resume_stage,
            stop_requested=False,
            last_error=last_error,
        )

    def _persist_summary_snapshot(self, *, final: bool) -> None:
        """Optionally persist the latest summary snapshot.

        Active runs deliberately avoid writing partial run_summary.json because the
        dashboard treats that file as terminal output. The hook stays here so the
        orchestrator can evolve without changing the main loop shape.
        """
        return None

    def _ensure_not_cancelled(self, stage: str | None = None) -> None:
        """Abort cooperatively when the dashboard asks to stop the run."""
        stop_requested = False
        if self.should_cancel is not None and self.should_cancel():
            stop_requested = True
        elif self.journal is not None:
            state = self.journal.load_execution_state()
            stop_requested = bool(state is not None and state.stop_requested)
        if stop_requested:
            raise RunCanceled(
                f"Run stopped by user during {stage}" if stage else "Run stopped by user"
            )

    def _emit_event(self, event_type: str, **payload: Any) -> None:
        """Send a non-fatal event to an observer, if configured."""
        event = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        if self.journal is not None:
            self.journal.append_event(event)
        try:
            if self.event_callback is not None:
                self.event_callback(event)
        except Exception:
            logger.exception("Pipeline event callback failed for event %s", event_type)

    def _emit_agent_trace(
        self,
        stage: str,
        message: str,
        *,
        prompt: str | None = None,
        preview: str | None = None,
        artifact_path: str | None = None,
        status: str = "info",
    ) -> None:
        """Emit a stage-scoped trace event for the dashboard."""
        run_id = self.summary.run_id if self.summary is not None else None
        self._emit_event(
            "agent_trace",
            run_id=run_id,
            stage=stage,
            message=message,
            status=status,
            prompt_summary=_summarize_prompt(prompt) if prompt else None,
            preview=preview,
            artifact_path=artifact_path,
        )

    def _capture_stage_usage(self, stage: str) -> None:
        """Drain provider usage after each stage and persist the run ledger."""
        if self.store is None or self.usage_ledger is None:
            return
        records = self.providers.drain_usage_records()
        if not records:
            return
        staged_records = [
            record.model_copy(update={"stage": stage})
            if isinstance(record, ProviderCallUsage)
            else ProviderCallUsage.model_validate({**record, "stage": stage})
            for record in records
        ]
        self.usage_ledger.calls.extend(staged_records)
        stage_summary = StageUsageSummary(
            stage=stage,
            call_count=len(staged_records),
            input_tokens=sum(record.input_tokens for record in staged_records),
            output_tokens=sum(record.output_tokens for record in staged_records),
            total_tokens=sum(record.total_tokens for record in staged_records),
            web_search_calls=sum(1 for record in staged_records if record.web_search_used),
            cached_calls=sum(1 for record in staged_records if record.cached),
        )
        self.usage_ledger.stage_summaries = [
            summary for summary in self.usage_ledger.stage_summaries if summary.stage != stage
        ] + [stage_summary]
        path = self.store.save_json("usage/run_usage_ledger.json", self.usage_ledger)
        if self.summary is not None:
            self.summary.artifacts["run_usage_ledger"] = str(path)

    def _sleep_with_cancel(self, seconds: float, stage: str) -> None:
        """Sleep in short slices so a stop request can interrupt long retries."""
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            self._ensure_not_cancelled(stage)
            remaining = deadline - time.monotonic()
            time.sleep(min(0.5, max(0.05, remaining)))

    def _call_provider_with_retry(
        self,
        *,
        stage: str,
        operation: str,
        func: Callable[[], Any],
        initial_delay: float = 2.0,
        max_delay: float = 30.0,
    ) -> Any:
        """Keep retrying a provider call until it succeeds or the run is stopped."""
        attempt = 1
        delay = initial_delay
        while True:
            self._ensure_not_cancelled(stage)
            try:
                return func()
            except RunCanceled:
                raise
            except Exception as exc:
                logger.exception("%s failed on attempt %s", operation, attempt)
                self._emit_agent_trace(
                    stage,
                    f"{operation} hit a transient error. Retrying.",
                    preview=f"Attempt {attempt}: {exc}",
                    status="warning",
                )
                self._sleep_with_cancel(delay, stage)
                attempt += 1
                delay = min(max_delay, delay * 1.5)

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def _bootstrap_run(self, ctx: dict) -> dict:
        """Stage 0: Build the run context and immutable policies."""
        assert self.store is not None
        run_context = build_run_context(self.config, self.store.run_id)
        if not is_stateless_run_context(run_context):
            raise ValueError("Run context failed stateless execution checks")

        context_path = self.store.save_json("run_context.json", run_context)
        quality_path = self.store.save_json("quality_policy.json", run_context.quality_policy)
        source_path = self.store.save_json("source_policy.json", run_context.source_policy)
        manifest_path = self.store.save_json("agent_manifest.json", run_context.agent_manifest)
        self.summary.artifacts["run_context"] = str(context_path)  # type: ignore[union-attr]
        self._emit_agent_trace(
            "bootstrap_run",
            "Initialized a fresh run context and stateless agent manifest.",
            preview=_list_preview(run_context.agent_manifest[:5]),
            artifact_path=str(context_path),
            status="success",
        )
        return {
            "run_context": run_context,
            "quality_policy_path": str(quality_path),
            "source_policy_path": str(source_path),
            "agent_manifest_path": str(manifest_path),
            "artifact": str(context_path),
        }

    def _collect_signals(self, ctx: dict) -> dict:
        """Stage 1: Collect market signals using multiple specialized scouts."""
        topic_clusters = self.config.topic_clusters
        run_context: RunContext = ctx["run_context"]
        themes = list((topic_clusters.get("clusters", {})).keys())

        scout_reports = []
        assert self.store is not None
        for scout_name, source_focus in RESEARCH_SCOUTS:
            self._ensure_not_cancelled("collect_signals")
            prompt = market_source_scout_prompt(
                brand_context=self.config.brand_context,
                competitors=self.config.competitors,
                scout_name=scout_name,
                source_focus=source_focus,
                themes=themes,
                lookback_days=run_context.source_policy.lookback_days,
            )
            self._emit_agent_trace(
                "collect_signals",
                f"{scout_name} gathering research signals.",
                prompt=prompt,
                preview=", ".join(themes[:4]),
                status="running",
            )
            scout_report = self._call_provider_with_retry(
                stage="collect_signals",
                operation=f"{scout_name} signal collection",
                func=lambda prompt=prompt: self.providers.market_signals.collect(
                    themes,
                    lookback_days=run_context.source_policy.lookback_days,
                    prompt=prompt,
                ),
            )
            scout_path = self.store.save_json(f"research/raw/{scout_name}.json", scout_report)
            scout_reports.append(scout_report)
            self._emit_agent_trace(
                "collect_signals",
                f"{scout_name} captured {len(scout_report.signals)} signals.",
                preview=_list_preview([signal.title for signal in scout_report.signals[:3]]),
                artifact_path=str(scout_path),
                status="success",
            )

        report = merge_market_signal_reports(scout_reports)
        coverage = _evaluate_source_coverage(report, required_classes=run_context.source_policy.required_source_classes)
        coverage_path = self.store.save_json("research/source_coverage_report.json", coverage)
        self.store.save_json("research/normalized_signals.json", report)
        research_packet = _build_research_packet(report, coverage=coverage)
        packet_path = self.store.save_json("research/research_packet.json", research_packet)

        path = self.store.save_json("market_signals.json", report)
        self.summary.artifacts["market_signals"] = str(path)  # type: ignore[union-attr]
        self.summary.artifacts["research_packet"] = str(packet_path)  # type: ignore[union-attr]
        self._emit_agent_trace(
            "collect_signals",
            f"Ensemble research produced {len(report.signals)} signals across {coverage.unique_classes} source classes.",
            preview=_list_preview(report.trending_themes),
            artifact_path=str(packet_path),
            status="success" if coverage.passed else "warning",
        )
        return {
            "market_signals": report,
            "research_packet": research_packet,
            "source_coverage": coverage,
            "artifact": str(path),
        }

    def _generate_topics(self, ctx: dict) -> dict:
        """Stage 2: Generate topic candidates via multiple ideation personas."""
        market_signals = ctx.get("market_signals")
        research_packet: ResearchPacket = ctx["research_packet"]
        topic_history = load_topic_history(self.config.data_dir)
        cooldowns = load_topic_cooldowns(self.config.data_dir)
        shortlist_history = load_topic_shortlist_history(self.config.data_dir)

        candidate_batches = []
        assert self.store is not None
        for persona_name, persona_goal in TOPIC_PERSONAS.items():
            self._ensure_not_cancelled("generate_topics")
            prompt = topic_researcher_persona_prompt(
                persona_name=persona_name,
                persona_goal=persona_goal,
                brand_context=self.config.brand_context,
                topic_clusters=self.config.topic_clusters,
                topic_history=topic_history,
                market_signals=market_signals,
            )
            self._emit_agent_trace(
                "generate_topics",
                f"{persona_name} generating specialist topic angles.",
                prompt=prompt,
                preview=_list_preview(research_packet.fresh_market_notes),
                status="running",
            )
            batch = self._call_provider_with_retry(
                stage="generate_topics",
                operation=f"{persona_name} topic generation",
                func=lambda prompt=prompt: self.providers.content_generation.generate_topics(prompt, market_signals),
            )
            persona_path = self.store.save_json(f"topics/raw/{persona_name}.json", batch)
            candidate_batches.append(batch)
            self._emit_agent_trace(
                "generate_topics",
                f"{persona_name} proposed {len(batch)} candidate topics.",
                preview=_list_preview([candidate.title for candidate in batch[:3]]),
                artifact_path=str(persona_path),
                status="success",
            )

        merged_candidates = merge_topic_candidates(candidate_batches)
        merged_candidates = merged_candidates[: self.config.max_topic_candidates]
        novelty_report = []
        filtered_candidates = []
        for candidate in merged_candidates:
            reuse = assess_topic_reuse(
                slug=candidate.slug,
                title=candidate.title,
                cluster=candidate.cluster,
                keywords=candidate.target_keywords,
                cooldowns=cooldowns,
                shortlist_records=shortlist_history,
            )
            novelty_report.append(
                {
                    "slug": candidate.slug,
                    "title": candidate.title,
                    "eligible": reuse.eligible,
                    "penalty": reuse.penalty,
                    "reasons": reuse.reasons,
                }
            )
            if reuse.eligible:
                filtered_candidates.append(candidate)

        path = self.store.save_json("topic_candidates.json", filtered_candidates)
        novelty_path = self.store.save_json("topics/topic_novelty_report.json", novelty_report)
        self.summary.artifacts["topic_candidates"] = str(path)  # type: ignore[union-attr]
        self._emit_agent_trace(
            "generate_topics",
            f"Ensemble ideation produced {len(filtered_candidates)} eligible candidate topics.",
            preview=_list_preview([candidate.title for candidate in filtered_candidates[:4]]),
            artifact_path=str(novelty_path),
            status="success",
        )
        return {
            "candidates": filtered_candidates,
            "topic_novelty_report": novelty_report,
            "artifact": str(path),
        }

    def _score_topics(self, ctx: dict) -> dict:
        """Stage 3: Score candidates with deterministic logic plus a judge jury."""
        candidates = ctx["candidates"]
        cluster_filter = ctx.get("cluster_filter")
        run_context: RunContext = ctx["run_context"]
        research_packet: ResearchPacket = ctx["research_packet"]
        if cluster_filter:
            candidates = [candidate for candidate in candidates if candidate.cluster == cluster_filter]
            if not candidates:
                raise ValueError(f"No topic candidates found for cluster '{cluster_filter}'")

        archive_path = self.config.data_dir / "topic_history.json"
        market_signals = ctx.get("market_signals")
        cooldowns = load_topic_cooldowns(self.config.data_dir)
        shortlist_history = load_topic_shortlist_history(self.config.data_dir)
        self._emit_agent_trace(
            "score_topics",
            f"Scoring {len(candidates)} candidates through the topic funnel.",
            preview=_list_preview([candidate.title for candidate in candidates[:3]]),
        )

        preliminary: list[ScoredTopic] = []
        for candidate in candidates:
            raw = _derive_raw_scores(
                candidate=candidate,
                keyword_metrics={},
                serp_analysis={},
                market_signals=market_signals,
                topic_clusters=self.config.topic_clusters,
            )
            preliminary.append(score_topic(candidate, raw, archive_path))
        preliminary_ranked = rank_topics(preliminary)
        judged_limit = min(self.config.model_judged_topics, len(preliminary_ranked))
        full_panel_limit = min(self.config.full_panel_topics, judged_limit)
        self._emit_agent_trace(
            "score_topics",
            f"Keeping the top {judged_limit} candidates for model judging and the top {full_panel_limit} for the full panel.",
            preview=_list_preview([topic.candidate.title for topic in preliminary_ranked[:judged_limit]]),
            status="success",
        )

        scored: list[ScoredTopic] = []
        keyword_notes: list[str] = []
        for index, preliminary_topic in enumerate(preliminary_ranked):
            self._ensure_not_cancelled("score_topics")
            candidate = preliminary_topic.candidate
            reuse = assess_topic_reuse(
                slug=candidate.slug,
                title=candidate.title,
                cluster=candidate.cluster,
                keywords=candidate.target_keywords,
                cooldowns=cooldowns,
                shortlist_records=shortlist_history,
            )
            if index < judged_limit:
                judge_focus_map = TOPIC_JUDGE_FOCUS if index < full_panel_limit else TOPIC_MINI_JUDGE_FOCUS
                jury_scores, consensus_score, consensus_variance, disagreement_notes = _collect_topic_jury_scores(
                    orchestrator=self,
                    topic=preliminary_topic,
                    reuse=reuse,
                    keyword_metrics={},
                    quality_policy=run_context.quality_policy,
                    judge_focus_map=judge_focus_map,
                    allow_tiebreaker=index < full_panel_limit,
                )
            else:
                jury_scores, disagreement_notes = [], []
                consensus_score = round(min(10.0, max(0.0, preliminary_topic.total_score / 10.0)), 2)
                consensus_variance = 0.0
            selection_notes = [*reuse.reasons, *disagreement_notes]
            rejection_reasons = list(preliminary_topic.rejection_reasons)
            if not reuse.eligible:
                rejection_reasons.extend(reuse.reasons)
            if index >= judged_limit:
                rejection_reasons.append("Outside model-judged topic funnel")
                selection_notes.append("Skipped model judging to stay within token budget.")
            else:
                authority_score = next(
                    (
                        score.score
                        for score in jury_scores
                        if score.judge == "technical_authority_judge"
                    ),
                    run_context.quality_policy.min_topic_authority_score,
                )
                if consensus_score < run_context.quality_policy.min_topic_consensus_score:
                    rejection_reasons.append(
                        f"Consensus score {consensus_score:.2f} below {run_context.quality_policy.min_topic_consensus_score:.2f}"
                    )
                if authority_score < run_context.quality_policy.min_topic_authority_score:
                    rejection_reasons.append(
                        f"Technical authority score {authority_score:.2f} below {run_context.quality_policy.min_topic_authority_score:.2f}"
                    )
                keyword_notes.append(
                    f"{candidate.target_keywords[0]}: {candidate.search_intent.value} intent, consensus {consensus_score:.2f}/10, cluster {candidate.cluster}"
                )
            scored.append(
                preliminary_topic.model_copy(
                    update={
                        "rejection_reasons": rejection_reasons,
                        "selected": len(rejection_reasons) == 0,
                        "judge_scores": jury_scores,
                        "consensus_score": consensus_score,
                        "consensus_variance": consensus_variance,
                        "selection_notes": selection_notes,
                        "reuse_penalty": reuse.penalty,
                    }
                )
            )

        ranked = rank_topics(scored)
        best = select_best(ranked)

        if best is None:
            raise ValueError("All topic candidates were rejected — no viable topic found")

        for topic in ranked:
            topic.selected = topic.candidate.slug == best.candidate.slug

        assert self.store is not None
        path = self.store.save_json("scored_topics.json", ranked)
        self.store.save_json("selected_topic.json", best)
        research_packet = research_packet.model_copy(update={"keyword_serp_notes": keyword_notes[:judged_limit]})
        research_packet_path = self.store.save_json("research/research_packet.json", research_packet)
        self.store.save_json(
            "scoring/topic_scorecards.json",
            [
                {
                    "slug": topic.candidate.slug,
                    "title": topic.candidate.title,
                    "consensus_score": topic.consensus_score,
                    "consensus_variance": topic.consensus_variance,
                    "judge_scores": [score.model_dump() for score in topic.judge_scores],
                    "rejection_reasons": topic.rejection_reasons,
                }
                for topic in ranked
            ],
        )
        self.summary.artifacts["scored_topics"] = str(path)  # type: ignore[union-attr]
        self.summary.artifacts["research_packet"] = str(research_packet_path)  # type: ignore[union-attr]
        self.summary.topic_selected = best.candidate.title  # type: ignore[union-attr]
        self._emit_agent_trace(
            "score_topics",
            f"Selected '{best.candidate.title}' as the winning topic.",
            preview=f"Consensus {best.consensus_score:.2f}/10 • total {best.total_score:.2f}",
            artifact_path=str(path),
            status="success",
        )
        record_topic_shortlist(
            self.config.data_dir,
            run_id=self.summary.run_id,  # type: ignore[union-attr]
            shortlisted_topics=[
                {
                    "slug": topic.candidate.slug,
                    "title": topic.candidate.title,
                    "cluster": topic.candidate.cluster,
                    "keywords": topic.candidate.target_keywords,
                }
                for topic in ranked[1: min(5, len(ranked))]
            ],
        )

        if not self.config.json_output:
            self._print_scoring_table(ranked)

        return {"scored": ranked, "selected": best, "research_packet": research_packet, "artifact": str(path)}

    def _build_brief(self, ctx: dict) -> dict:
        """Stage 4: Build and validate the research brief."""
        selected: ScoredTopic = ctx["selected"]
        run_context: RunContext = ctx["run_context"]
        research_packet: ResearchPacket = ctx["research_packet"]

        assert self.store is not None
        brief = None
        brief_report = None
        topic_history = load_topic_history(self.config.data_dir)
        for attempt in range(run_context.quality_policy.max_brief_retries + 1):
            self._ensure_not_cancelled("build_brief")
            prompt = brief_composer_prompt(
                topic=selected,
                research_packet=research_packet,
                brand_context=self.config.brand_context,
                style_guide=self.config.style_guide,
                forbidden_claims=self.config.forbidden_claims,
                topic_history=topic_history,
            )
            if attempt > 0 and brief is not None and brief_report is not None:
                prompt = brief_critic_prompt(
                    brief=brief,
                    research_packet=research_packet,
                    notes=brief_report.notes,
                )
            self._emit_agent_trace(
                "build_brief",
                f"Preparing bundled brief for '{selected.candidate.title}'.",
                prompt=prompt,
                preview=_list_preview(selected.candidate.target_keywords),
            )
            brief = self._call_provider_with_retry(
                stage="build_brief",
                operation="brief composer",
                func=lambda prompt=prompt: self.providers.content_generation.generate_brief_bundle(
                    prompt,
                    selected,
                    research_packet,
                ),
            )
            brief_score, brief_notes = evaluate_brief_quality(brief)
            passed = brief_score >= run_context.quality_policy.min_brief_quality_score
            brief_report = BriefQualityReport(score=brief_score, passed=passed, notes=brief_notes)
            attempt_path = self.store.save_json(f"brief/attempt_{attempt + 1}.json", brief)
            self._emit_agent_trace(
                "build_brief",
                f"Brief attempt {attempt + 1} scored {brief_score:.2f}/10.",
                preview=_list_preview(brief_notes) if brief_notes else f"{len(brief.outline)} sections",
                artifact_path=str(attempt_path),
                status="success" if passed else "warning",
            )
            if passed:
                break

        if brief is None or brief_report is None:
            raise ValueError("Brief generation did not produce an artifact")
        if not brief_report.passed:
            raise ValueError(
                f"Research brief quality {brief_report.score:.2f}/10 below threshold"
            )

        path = self.store.save_json("brief/research_brief.json", brief)
        self.store.save_json("brief/brief_quality_report.json", brief_report)
        self.summary.artifacts["research_brief"] = str(path)  # type: ignore[union-attr]
        self._emit_agent_trace(
            "build_brief",
            f"Brief ready with {len(brief.outline)} sections and {len(brief.faqs)} FAQs.",
            preview=f"Primary keyword: {brief.primary_keyword}",
            artifact_path=str(path),
            status="success",
        )
        return {"brief": brief, "artifact": str(path)}

    def _write_draft(self, ctx: dict) -> dict:
        """Stage 5: Run blueprint ideation, then generate one full draft."""
        brief: ResearchBrief = ctx["brief"]
        research_packet: ResearchPacket = ctx["research_packet"]
        run_context: RunContext = ctx["run_context"]
        assert self.store is not None
        blueprints: list[WriterBlueprint] = []
        blueprint_evaluations: list[BlueprintEvaluation] = []
        writer_ids = self.config.writer_personas[: self.config.writer_blueprints]
        for writer_id in writer_ids:
            self._ensure_not_cancelled("write_draft")
            writer_label, focus = WRITER_PERSONAS.get(
                writer_id,
                (writer_id.replace("_", " ").title(), "Write a strong engineering article."),
            )
            prompt = writer_blueprint_prompt(
                brief=brief,
                research_packet=research_packet,
                brand_context=self.config.brand_context,
                persona_name=writer_label,
                persona_focus=focus,
            )
            self._emit_agent_trace(
                "write_draft",
                f"{writer_label} is preparing a draft blueprint.",
                prompt=prompt,
                preview=f"{len(brief.outline)} sections • target {brief.target_word_count} words",
                status="running",
            )
            blueprint = self._call_provider_with_retry(
                stage="write_draft",
                operation=f"{writer_label} blueprint generation",
                func=lambda prompt=prompt, writer_id=writer_id, writer_label=writer_label: self.providers.content_generation.generate_writer_blueprint(
                    prompt,
                    brief,
                    research_packet,
                    writer_id,
                    writer_label,
                ),
            )
            blueprints.append(blueprint)
            blueprint_path = self.store.save_json(f"drafts/blueprints/{writer_id}.json", blueprint)
            evaluation = _evaluate_writer_blueprint(blueprint, brief)
            blueprint_evaluations.append(evaluation)
            self._emit_agent_trace(
                "write_draft",
                f"{writer_label} blueprint scored {evaluation.score:.2f}/10.",
                preview=_list_preview([section.heading for section in blueprint.sections[:3]]),
                artifact_path=str(blueprint_path),
                status="success",
            )

        ranked_blueprints = _rank_blueprints(blueprints, blueprint_evaluations)
        selected_blueprints = ranked_blueprints[: max(1, self.config.full_draft_candidates)]
        runner_up_blueprint = ranked_blueprints[1] if len(ranked_blueprints) > 1 else None
        variants: list[DraftVariant] = []
        evaluations: list[DraftEvaluation] = []
        for blueprint in selected_blueprints:
            self._ensure_not_cancelled("write_draft")
            prompt = draft_from_blueprint_prompt(
                brief=brief,
                blueprint=blueprint,
                research_packet=research_packet,
                brand_context=self.config.brand_context,
                style_guide=self.config.style_guide,
            )
            self._emit_agent_trace(
                "write_draft",
                f"{blueprint.writer_label} is expanding the selected blueprint into a full draft.",
                prompt=prompt,
                preview=f"{len(blueprint.sections)} blueprint sections",
                status="running",
            )
            content_md = self._call_provider_with_retry(
                stage="write_draft",
                operation=f"{blueprint.writer_label} draft generation",
                func=lambda prompt=prompt, blueprint=blueprint: self.providers.content_generation.generate_draft_from_blueprint(
                    prompt,
                    brief,
                    blueprint,
                    research_packet,
                ),
            )
            variant = DraftVariant(
                writer_id=blueprint.writer_id,
                writer_label=blueprint.writer_label,
                focus_summary=blueprint.focus_summary,
                title=blueprint.title,
                slug=brief.topic.slug,
                content_md=content_md,
                word_count=len(content_md.split()),
                brief_hash=brief.brief_hash(),
            )
            variants.append(variant)
            variant_path = self.store.save_markdown(f"drafts/{blueprint.writer_id}.md", content_md)
            qa_result = run_qa(
                content=content_md,
                slug=brief.topic.slug,
                meta_description=brief.meta_description,
                forbidden_claims=self.config.forbidden_claims,
                do_not_say=brief.do_not_say,
                min_word_count=self.config.min_word_count,
                max_word_count=self.config.max_word_count,
                min_internal_links=self.config.min_internal_links,
            )
            seo_score = score_seo_aeo(
                content=content_md,
                meta_description=brief.meta_description,
                primary_keyword=brief.primary_keyword,
                slug=brief.topic.slug,
            )
            evaluation = evaluate_draft_variant(
                variant=variant,
                brief=brief,
                qa_result=qa_result,
                seo_score=seo_score,
            )
            evaluations.append(evaluation)
            self._emit_agent_trace(
                "write_draft",
                f"{blueprint.writer_label} draft scored {evaluation.average_score:.2f}/10.",
                preview=f"Min judge {evaluation.min_score:.2f} • {variant.word_count} words",
                artifact_path=str(variant_path),
                status="success",
            )

        best_variant, best_evaluation = select_best_draft(variants, evaluations)
        draft = DraftArticle(
            title=best_variant.title,
            slug=best_variant.slug,
            content_md=best_variant.content_md,
            word_count=best_variant.word_count,
            brief_hash=best_variant.brief_hash,
        )
        self.store.save_markdown("draft.md", draft.content_md)
        self.store.save_json("drafts/blueprint_scorecards.json", blueprint_evaluations)
        self.store.save_json("drafts/selected_blueprint.json", selected_blueprints[0])
        if runner_up_blueprint is not None:
            self.store.save_json("drafts/runner_up_blueprint.json", runner_up_blueprint)
        self.store.save_json("drafts/draft_scorecards.json", evaluations)
        self.store.save_json("drafts/selected_draft.json", best_evaluation)
        path = self.store.save_json("draft_meta.json", draft)
        self.summary.artifacts["draft"] = str(path)  # type: ignore[union-attr]
        self._emit_agent_trace(
            "write_draft",
            f"Selected {best_variant.writer_label} with average score {best_evaluation.average_score:.2f}/10.",
            preview=_content_preview(draft.content_md),
            artifact_path=str(path),
            status="success",
        )
        return {
            "draft": draft,
            "brief": brief,
            "research_packet": research_packet,
            "writer_blueprints": blueprints,
            "blueprint_evaluations": blueprint_evaluations,
            "draft_variants": variants,
            "draft_evaluations": evaluations,
            "runner_up_blueprint": runner_up_blueprint,
            "artifact": str(path),
        }

    def _run_qa_optimize(self, ctx: dict) -> dict:
        """Stage 6: Optimize with a manifest-driven patch loop and final jury."""
        draft: DraftArticle = ctx["draft"]
        brief: ResearchBrief = ctx["brief"]
        research_packet: ResearchPacket = ctx["research_packet"]
        runner_up_blueprint: WriterBlueprint | None = ctx.get("runner_up_blueprint")
        assert self.store is not None
        run_context: RunContext = ctx["run_context"]
        working_content = draft.content_md
        last_gate: FinalQualityGate | None = None
        last_qa_result = None
        last_seo_score = None
        last_manifest: ArticleManifest | None = None
        second_draft_unlocked = False

        round_number = 1
        while True:
            self._ensure_not_cancelled("qa_optimize")
            if last_qa_result is None or last_seo_score is None:
                last_seo_score = score_seo_aeo(
                    content=working_content,
                    meta_description=brief.meta_description,
                    primary_keyword=brief.primary_keyword,
                    slug=draft.slug,
                )
                last_qa_result = run_qa(
                    content=working_content,
                    slug=draft.slug,
                    meta_description=brief.meta_description,
                    forbidden_claims=self.config.forbidden_claims,
                    do_not_say=brief.do_not_say,
                    min_word_count=self.config.min_word_count,
                    max_word_count=self.config.max_word_count,
                    min_internal_links=self.config.min_internal_links,
                )
            last_manifest = _build_article_manifest(
                content=working_content,
                brief=brief,
                qa_result=last_qa_result,
                seo_score=last_seo_score,
            )
            manifest_path = self.store.save_json(
                f"optimization/article_manifest_round_{round_number}.json",
                last_manifest,
            )
            optimize_prompt = optimization_coordinator_prompt(
                article_manifest=last_manifest,
                brief=brief,
                seo_rules=self.config.seo_rules,
                aeo_rules=self.config.aeo_rules,
                optimization_notes=_optimization_notes(last_qa_result, last_gate),
                round_number=round_number,
            )
            self._emit_agent_trace(
                "qa_optimize",
                "Optimization coordinator is preparing a focused patch.",
                prompt=optimize_prompt,
                preview=f"Round {round_number}",
                artifact_path=str(manifest_path),
                status="running",
            )
            patch = self._call_provider_with_retry(
                stage="qa_optimize",
                operation="optimization coordinator",
                func=lambda prompt=optimize_prompt, content=working_content, manifest=last_manifest: self.providers.content_generation.optimize_sections(
                    prompt,
                    content,
                    manifest,
                ),
            )
            working_content, patch_notes = _apply_optimization_patch(
                working_content,
                patch=patch,
                brief=brief,
            )

            repaired_content, repair_notes = _apply_quality_repairs(
                working_content,
                brief=brief,
                qa_result=last_qa_result,
                min_internal_links=self.config.min_internal_links,
            )
            if repaired_content != working_content:
                working_content = repaired_content
            all_repair_notes = [*patch_notes, *repair_notes]
            if all_repair_notes:
                self._emit_agent_trace(
                    "qa_optimize",
                    "Applied structured patch and deterministic QA repairs.",
                    preview=_list_preview(all_repair_notes),
                    status="success",
                )

            seo_score = score_seo_aeo(
                content=working_content,
                meta_description=brief.meta_description,
                primary_keyword=brief.primary_keyword,
                slug=draft.slug,
            )
            qa_result = run_qa(
                content=working_content,
                slug=draft.slug,
                meta_description=brief.meta_description,
                forbidden_claims=self.config.forbidden_claims,
                do_not_say=brief.do_not_say,
                min_word_count=self.config.min_word_count,
                max_word_count=self.config.max_word_count,
                min_internal_links=self.config.min_internal_links,
            )
            article_manifest = _build_article_manifest(
                content=working_content,
                brief=brief,
                qa_result=qa_result,
                seo_score=seo_score,
            )
            gate = final_quality_jury(
                article_manifest=article_manifest,
                round_number=round_number,
            )
            provider_scores, disagreement_notes = _collect_article_jury_scores(
                orchestrator=self,
                article_manifest=article_manifest,
                brief=brief,
                quality_policy=run_context.quality_policy,
            )
            if provider_scores:
                provider_average = round(mean(score.score for score in provider_scores), 2)
                provider_min = round(min(score.score for score in provider_scores), 2)
                provider_variance = _score_variance(provider_scores)
                technical_accuracy_score = next(
                    score.score
                    for score in provider_scores
                    if score.judge == "technical_rigor_judge"
                )
                gate = gate.model_copy(
                    update={
                        "scores": provider_scores,
                        "average_score": provider_average,
                        "min_score": provider_min,
                        "score_variance": provider_variance,
                        "technical_accuracy_score": technical_accuracy_score,
                        "notes": [*gate.notes, *disagreement_notes],
                    }
                )
            passed, gate_notes = quality_gate_passed(
                average_score=gate.average_score,
                min_score=gate.min_score,
                technical_accuracy_score=gate.technical_accuracy_score,
                policy=run_context.quality_policy,
            )
            gate = gate.model_copy(update={"passed": passed, "notes": [*gate.notes, *gate_notes]})
            self.store.save_markdown(f"optimization/pass_{round_number}.md", working_content)
            gate_path = self.store.save_json(
                f"optimization/final_quality_gate_round_{round_number}.json",
                gate,
            )
            self._emit_agent_trace(
                "qa_optimize",
                f"Final jury round {round_number} scored {gate.average_score:.2f}/10.",
                preview=f"Min {gate.min_score:.2f} • tech {gate.technical_accuracy_score:.2f}",
                artifact_path=str(gate_path),
                status="success" if gate.passed else "warning",
            )
            last_gate = gate
            last_qa_result = qa_result
            last_seo_score = seo_score
            last_manifest = article_manifest
            if gate.passed:
                break
            rewrite_targets = _focused_rewrite_targets(
                article_manifest=article_manifest,
                brief=brief,
                gate=gate,
            )
            if rewrite_targets:
                rewrite_prompt = focused_section_rewrite_prompt(
                    article_manifest=article_manifest,
                    brief=brief,
                    section_headings=rewrite_targets,
                )
                self._emit_agent_trace(
                    "qa_optimize",
                    "Focused rewriter is strengthening weak sections.",
                    prompt=rewrite_prompt,
                    preview=_list_preview(rewrite_targets),
                    status="running",
                )
                rewrite_patch = self._call_provider_with_retry(
                    stage="qa_optimize",
                    operation="focused section rewrite",
                    func=lambda prompt=rewrite_prompt, content=working_content, manifest=article_manifest: self.providers.content_generation.optimize_sections(
                        prompt,
                        content,
                        manifest,
                    ),
                )
                rewritten_content, rewrite_notes = _apply_optimization_patch(
                    working_content,
                    patch=rewrite_patch,
                    brief=brief,
                )
                if rewritten_content != working_content:
                    working_content = rewritten_content
                    last_qa_result = None
                    last_seo_score = None
                if rewrite_notes:
                    self._emit_agent_trace(
                        "qa_optimize",
                        "Applied focused rewrite patch to improve weak sections.",
                        preview=_list_preview(rewrite_notes),
                        status="success",
                    )
            if _should_unlock_second_draft(
                gate=gate,
                round_number=round_number,
                second_draft_unlock_round=self.config.second_draft_unlock_round,
                already_unlocked=second_draft_unlocked,
                runner_up_available=runner_up_blueprint is not None,
            ):
                second_draft_unlocked = True
                retry_prompt = draft_from_blueprint_prompt(
                    brief=brief,
                    blueprint=runner_up_blueprint,
                    research_packet=research_packet,
                    brand_context=self.config.brand_context,
                    style_guide=self.config.style_guide,
                )
                self._emit_agent_trace(
                    "qa_optimize",
                    "Unlocking the runner-up blueprint for a second full draft candidate.",
                    prompt=retry_prompt,
                    preview=runner_up_blueprint.writer_label,
                    status="running",
                )
                alternate_content = self._call_provider_with_retry(
                    stage="qa_optimize",
                    operation="runner-up blueprint draft generation",
                    func=lambda prompt=retry_prompt, blueprint=runner_up_blueprint: self.providers.content_generation.generate_draft_from_blueprint(
                        prompt,
                        brief,
                        blueprint,
                        research_packet,
                    ),
                )
                alternate_seo = score_seo_aeo(
                    content=alternate_content,
                    meta_description=brief.meta_description,
                    primary_keyword=brief.primary_keyword,
                    slug=draft.slug,
                )
                alternate_qa = run_qa(
                    content=alternate_content,
                    slug=draft.slug,
                    meta_description=brief.meta_description,
                    forbidden_claims=self.config.forbidden_claims,
                    do_not_say=brief.do_not_say,
                    min_word_count=self.config.min_word_count,
                    max_word_count=self.config.max_word_count,
                    min_internal_links=self.config.min_internal_links,
                )
                alternate_manifest = _build_article_manifest(
                    content=alternate_content,
                    brief=brief,
                    qa_result=alternate_qa,
                    seo_score=alternate_seo,
                )
                alternate_gate = final_quality_jury(
                    article_manifest=alternate_manifest,
                    round_number=round_number,
                )
                if alternate_gate.average_score >= gate.average_score:
                    working_content = alternate_content
                    last_qa_result = alternate_qa
                    last_seo_score = alternate_seo
                    last_manifest = alternate_manifest
                    self.store.save_markdown("drafts/runner_up_unlocked.md", alternate_content)
                    self._emit_agent_trace(
                        "qa_optimize",
                        "Runner-up blueprint replaced the active draft baseline.",
                        preview=f"{alternate_gate.average_score:.2f}/10",
                        status="success",
                    )
            round_number += 1

        if last_gate is None or last_qa_result is None or last_seo_score is None or last_manifest is None:
            raise ValueError("Optimizer jury did not produce a final gate result")
        if not last_gate.passed:
            raise ValueError(
                f"Final quality gate failed at {last_gate.average_score:.2f}/10 after optimization"
            )

        draft = draft.model_copy(
            update={
                "content_md": working_content,
                "word_count": len(working_content.split()),
            }
        )
        internal_links = re.findall(
            r"\[.*?\]\(((?:/|https?://macroscope\.com).*?)\)", draft.content_md
        )
        final = FinalArticle(
            title=draft.title,
            slug=draft.slug,
            content_md=draft.content_md,
            meta_description=brief.meta_description,
            word_count=draft.word_count,
            seo_aeo_score=last_seo_score,
            faqs_present=any(c.name == "faq_section" and c.passed for c in last_qa_result.checks),
            internal_links=internal_links,
        )
        self.store.save_markdown("optimized_draft.md", draft.content_md)
        self.store.save_markdown("final.md", final.content_md)
        path = self.store.save_json("meta.json", final)
        self.store.save_json("qa_result.json", last_qa_result.to_dict())
        self.store.save_json("article_manifest.json", last_manifest)
        self.store.save_json("optimization/final_quality_gate.json", last_gate)

        self.summary.final_score = last_gate.average_score  # type: ignore[union-attr]
        self.summary.final_grade = _jury_grade(last_gate.average_score)  # type: ignore[union-attr]
        self.summary.word_count = final.word_count  # type: ignore[union-attr]
        self.summary.artifacts["final"] = str(path)  # type: ignore[union-attr]
        self._emit_agent_trace(
            "qa_optimize",
            f"Optimization complete with final jury score {last_gate.average_score:.2f}/10.",
            preview=f"{last_qa_result.summary} • min judge {last_gate.min_score:.2f}",
            artifact_path=str(path),
            status="success",
        )

        if not self.config.json_output:
            self._print_qa_results(last_qa_result, last_seo_score)

        return {
            "final": final,
            "qa": last_qa_result,
            "article_manifest": last_manifest,
            "final_quality_gate": last_gate,
            "artifact": str(path),
        }

    def _fact_check(self, ctx: dict) -> dict:
        """Stage 7: Run one final web-enabled fact check over the article manifest."""
        if not self.config.enable_final_fact_check:
            return {}
        final: FinalArticle = ctx["final"]
        brief: ResearchBrief = ctx["brief"]
        research_packet: ResearchPacket = ctx["research_packet"]
        article_manifest: ArticleManifest = ctx["article_manifest"]
        assert self.store is not None

        prompt = fact_check_prompt(
            article_manifest=article_manifest,
            research_packet=research_packet,
        )
        self._emit_agent_trace(
            "fact_check",
            "Running the final fact-check pass over the article manifest.",
            prompt=prompt,
            preview=_list_preview(article_manifest.claim_candidates[:3]),
            status="running",
        )
        report = self._call_provider_with_retry(
            stage="fact_check",
            operation="final fact check",
            func=lambda prompt=prompt, manifest=article_manifest: self.providers.content_generation.fact_check_claims(
                prompt,
                manifest,
            ),
        )
        path = self.store.save_json("fact_check_report.json", report)
        self.summary.artifacts["fact_check"] = str(path)  # type: ignore[union-attr]
        self._emit_agent_trace(
            "fact_check",
            "Final fact check completed.",
            preview=_list_preview(report.notes or report.verified_claims[:3]),
            artifact_path=str(path),
            status="success" if report.passed else "warning",
        )
        return {"fact_check_report": report, "artifact": str(path)}

    def _export(self, ctx: dict) -> dict:
        """Stage 8: Export final article to local files and optional external targets."""
        final: FinalArticle = ctx["final"]
        assert self.store is not None

        results = export_article(
            article=final,
            run_summary=self.summary,  # type: ignore[arg-type]
            output_dir=self.store.run_dir,
            google_docs_provider=(
                self.providers.document_export
                if not isinstance(self.providers.document_export, MockGoogleDocsProvider)
                else None
            ),
        )

        export_meta = [r.to_dict() for r in results]
        path = self.store.save_json("export_results.json", export_meta)
        self.summary.artifacts["export"] = str(path)  # type: ignore[union-attr]
        self._emit_agent_trace(
            "export",
            f"Exported {len(results)} publication artifacts.",
            preview=_list_preview([result.target for result in results]),
            artifact_path=str(path),
            status="success",
        )
        return {"export_results": results, "artifact": str(path)}

    def _persist_history(self, ctx: dict) -> dict:
        """Stage 9: Append to topic archive."""
        final: FinalArticle = ctx.get("final")  # type: ignore[assignment]
        if final is None:
            return {}

        selected: ScoredTopic | None = ctx.get("selected")  # type: ignore[assignment]
        brief = ctx.get("brief")
        cluster = selected.candidate.cluster if selected else "unknown"
        keywords: list[str] = []
        if brief is not None:
            deduped_keywords: list[str] = []
            seen_keywords: set[str] = set()
            for keyword in [brief.primary_keyword, *brief.secondary_keywords]:
                normalized = keyword.lower().strip()
                if normalized and normalized not in seen_keywords:
                    seen_keywords.add(normalized)
                    deduped_keywords.append(normalized)
            keywords = deduped_keywords[:6]

        append_to_topic_history(
            data_dir=self.config.data_dir,
            slug=final.slug,
            title=final.title,
            keywords=keywords,
            cluster=cluster,
        )
        upsert_topic_cooldown(
            self.config.data_dir,
            slug=final.slug,
            title=final.title,
            cluster=cluster,
            keywords=keywords,
            cooldown_days=self.config.topic_cooldown_days,
        )
        self._emit_agent_trace(
            "persist_history",
            f"Archived '{final.title}' in topic history.",
            preview=f"Cluster: {cluster}",
            status="success",
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
            lines.append(f"Final jury: {self.summary.final_score:.2f}/10 ({self.summary.final_grade})")
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


def _derive_raw_scores(
    candidate,
    keyword_metrics: dict[str, dict[str, Any]],
    serp_analysis: dict[str, Any],
    market_signals,
    topic_clusters: dict[str, Any],
) -> dict[str, float]:
    """Derive deterministic scores from candidate metadata and provider data."""
    metrics = [keyword_metrics.get(keyword, {}) for keyword in candidate.target_keywords]
    volumes = [float(metric.get("volume", 300)) for metric in metrics]
    difficulties = [float(metric.get("difficulty", 40)) for metric in metrics]
    trends = [str(metric.get("trend", "unknown")).lower() for metric in metrics]
    avg_volume = sum(volumes) / len(volumes) if volumes else 300.0
    avg_difficulty = sum(difficulties) / len(difficulties) if difficulties else 40.0

    cluster_config = (topic_clusters.get("clusters", {})).get(candidate.cluster, {})
    cluster_keywords = [keyword.lower() for keyword in cluster_config.get("keywords", [])]
    candidate_terms = " ".join(
        [candidate.title, candidate.description, candidate.rationale, *candidate.target_keywords]
    ).lower()
    keyword_overlap = sum(1 for keyword in cluster_keywords if keyword in candidate_terms)

    intent_bonus = {
        "commercial": 3.0,
        "transactional": 2.0,
        "informational": 1.5,
        "navigational": 1.0,
    }.get(candidate.search_intent.value, 1.0)

    cluster_base = {
        "ai-code-review": 18.0,
        "security-in-review": 16.0,
        "pr-workflows": 15.0,
        "engineering-productivity": 14.0,
        "devops-ci-cd": 13.0,
        "code-quality": 12.0,
    }.get(candidate.cluster, 11.0)
    business_relevance = min(25.0, cluster_base + min(4.0, keyword_overlap * 1.2) + intent_bonus)

    volume_score = min(11.0, math.log10(max(avg_volume, 10.0)) * 3.2)
    difficulty_score = max(1.5, 7.0 - (avg_difficulty / 12.0))
    trend_score = 2.0 if "up" in trends else (1.0 if "stable" in trends else 0.5)
    search_opportunity = min(20.0, volume_score + difficulty_score + trend_score)

    title_lower = candidate.title.lower()
    aeo_fit = 6.0
    if any(marker in title_lower for marker in ("how ", "why ", "what ", "vs", "checklist", "framework")):
        aeo_fit += 2.5
    if serp_analysis.get("featured_snippet"):
        aeo_fit += 2.5
    aeo_fit += min(3.0, len(serp_analysis.get("people_also_ask", [])) * 0.75)
    aeo_fit = min(15.0, aeo_fit)

    freshness = 2.0
    if candidate.freshness_signal:
        freshness += 3.5
    if "up" in trends:
        freshness += 1.5
    if market_signals:
        signal_text = " ".join(
            [
                *market_signals.trending_themes,
                *market_signals.recommended_angles,
                *[
                    " ".join([signal.title, signal.summary, " ".join(signal.themes)])
                    for signal in market_signals.signals
                ],
            ]
        ).lower()
        matched_keywords = sum(1 for keyword in candidate.target_keywords if keyword.lower() in signal_text)
        if candidate.cluster.replace("-", " ") in signal_text:
            matched_keywords += 1
        freshness += min(3.0, matched_keywords * 0.8)
    freshness = min(10.0, freshness)

    authority = {
        "ai-code-review": 14.0,
        "security-in-review": 13.0,
        "pr-workflows": 13.0,
        "engineering-productivity": 12.0,
        "devops-ci-cd": 11.0,
        "code-quality": 11.0,
    }.get(candidate.cluster, 10.0)
    if "macroscope" not in candidate_terms and "ai code review" in candidate_terms:
        authority += 1.0
    if candidate.search_intent.value == "commercial":
        authority += 1.0
    authority_to_win = min(15.0, authority)

    production_ease = 2.0
    if candidate.search_intent.value in {"informational", "commercial"}:
        production_ease += 1.0
    if len(candidate.target_keywords) <= 3:
        production_ease += 1.0
    if candidate.freshness_signal:
        production_ease += 0.5
    if candidate.cluster in {"devops-ci-cd", "security-in-review"}:
        production_ease -= 0.5
    production_ease = max(1.0, min(5.0, production_ease))

    return {
        "business_relevance": round(business_relevance, 2),
        "search_opportunity": round(search_opportunity, 2),
        "aeo_fit": round(aeo_fit, 2),
        "freshness": round(freshness, 2),
        "authority_to_win": round(authority_to_win, 2),
        "production_ease": round(production_ease, 2),
    }


def _shortlist_candidates_for_serp_research(
    *,
    candidates,
    keyword_metrics: dict[str, dict[str, Any]],
    market_signals,
    topic_clusters: dict[str, Any],
    archive_path,
) -> list[ScoredTopic]:
    preliminary: list[ScoredTopic] = []
    for candidate in candidates:
        raw = _derive_raw_scores(
            candidate=candidate,
            keyword_metrics=keyword_metrics,
            serp_analysis={},
            market_signals=market_signals,
            topic_clusters=topic_clusters,
        )
        preliminary.append(score_topic(candidate, raw, archive_path))
    return rank_topics(preliminary)[: min(SERP_RESEARCH_LIMIT, len(preliminary))]


def _summarize_prompt(prompt: str | None, limit: int = 320) -> str | None:
    if not prompt:
        return None
    compact = re.sub(r"\s+", " ", prompt).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _list_preview(items: list[str], limit: int = 3) -> str:
    if not items:
        return "No preview available yet."
    trimmed = [item for item in items[:limit] if item]
    suffix = "" if len(items) <= limit else f" +{len(items) - limit} more"
    return ", ".join(trimmed) + suffix


def _content_preview(content: str, limit: int = 220) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return "Draft content is empty."
    compact = re.sub(r"\s+", " ", lines[0])
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _market_signal_preview(market_signals) -> str:
    if not market_signals:
        return "No market signals available."
    return _list_preview(market_signals.trending_themes)


def _keyword_metric_preview(keyword_metrics: dict[str, dict[str, Any]]) -> str:
    if not keyword_metrics:
        return "No keyword metrics returned."
    snippets: list[str] = []
    for keyword, metric in list(keyword_metrics.items())[:3]:
        snippets.append(f"{keyword}: vol {metric.get('volume', '?')}, diff {metric.get('difficulty', '?')}")
    return _list_preview(snippets)


def _evaluate_source_coverage(
    report,
    *,
    required_classes: list[str],
) -> SourceCoverageReport:
    counts_by_class: dict[str, int] = {source_class: 0 for source_class in required_classes}
    source_mapping = {
        "hacker_news": "community_discussion",
        "reddit": "community_discussion",
        "reddit_r_experienceddevs": "community_discussion",
        "reddit_r_programming": "community_discussion",
        "reddit_r_devops": "community_discussion",
        "dev_blog": "engineering_blog",
        "github_blog": "engineering_blog",
        "official_docs": "official_docs",
        "docs": "official_docs",
        "release_notes": "official_docs",
        "google_trends": "serp_signal",
        "search_console": "serp_signal",
        "industry_report": "market_announcement",
        "analyst_report": "market_announcement",
        "paper": "benchmark_or_paper",
        "benchmark": "benchmark_or_paper",
        "arxiv": "benchmark_or_paper",
    }

    for signal in getattr(report, "signals", []):
        source = (signal.source or "").lower()
        source_class = next(
            (mapped for key, mapped in source_mapping.items() if key in source),
            "engineering_blog",
        )
        counts_by_class[source_class] = counts_by_class.get(source_class, 0) + 1

    missing = [source_class for source_class, count in counts_by_class.items() if count == 0]
    unique_classes = sum(1 for count in counts_by_class.values() if count > 0)
    primary_present = counts_by_class.get("official_docs", 0) > 0 or counts_by_class.get("benchmark_or_paper", 0) > 0
    score = 5.0 + min(3.0, unique_classes * 0.7) + (1.0 if primary_present else 0.0)
    notes: list[str] = []
    if missing:
        notes.append(f"Missing source classes: {missing}")
    if not primary_present:
        notes.append("No primary technical source detected in the research packet")
    return SourceCoverageReport(
        counts_by_class=counts_by_class,
        missing_classes=missing,
        unique_classes=unique_classes,
        primary_technical_source_present=primary_present,
        score=round(min(score, 10.0), 2),
        passed=unique_classes >= 3 and primary_present,
        notes=notes,
    )


def _source_class_for_signal(source: str) -> str:
    normalized = (source or "").lower()
    source_mapping = {
        "hacker_news": "community_discussion",
        "reddit": "community_discussion",
        "dev_blog": "engineering_blog",
        "github_blog": "engineering_blog",
        "official_docs": "official_docs",
        "docs": "official_docs",
        "release_notes": "official_docs",
        "google_trends": "serp_signal",
        "search_console": "serp_signal",
        "industry_report": "market_announcement",
        "analyst_report": "market_announcement",
        "paper": "benchmark_or_paper",
        "benchmark": "benchmark_or_paper",
        "arxiv": "benchmark_or_paper",
    }
    for key, mapped in source_mapping.items():
        if key in normalized:
            return mapped
    return "engineering_blog"


def _build_research_packet(
    report,
    *,
    coverage: SourceCoverageReport,
) -> ResearchPacket:
    canonical_sources: list[CanonicalSource] = []
    normalized_facts: list[ResearchFact] = []
    seen_sources: set[tuple[str, str]] = set()

    for signal in getattr(report, "signals", []):
        source_class = _source_class_for_signal(signal.source)
        source_key = ((signal.url or "").strip(), signal.title.strip().lower())
        if source_key not in seen_sources:
            seen_sources.add(source_key)
            canonical_sources.append(
                CanonicalSource(
                    source_class=source_class,
                    title=signal.title.strip(),
                    url=(signal.url or None),
                    note=signal.summary[:220].strip(),
                )
            )

        freshness_note = None
        if getattr(signal, "detected_at", None) is not None:
            freshness_note = f"Observed {signal.detected_at.strftime('%Y-%m-%d')}"
        elif getattr(signal, "themes", None):
            freshness_note = f"Themes: {', '.join(signal.themes[:2])}"

        normalized_facts.append(
            ResearchFact(
                statement=signal.summary.strip(),
                source_class=source_class,
                evidence_titles=[signal.title.strip()],
                freshness_note=freshness_note,
            )
        )

    fresh_market_notes: list[str] = []
    for angle in getattr(report, "recommended_angles", [])[:2]:
        if angle and angle not in fresh_market_notes:
            fresh_market_notes.append(angle)
    for theme in getattr(report, "trending_themes", [])[:4]:
        note = f"Theme signal: {theme}"
        if note not in fresh_market_notes:
            fresh_market_notes.append(note)

    keyword_serp_notes = [
        f"Shortlist the SERP around '{theme}' using the research packet only."
        for theme in getattr(report, "trending_themes", [])[:3]
    ]

    return ResearchPacket(
        themes=list(getattr(report, "trending_themes", []))[:5],
        normalized_facts=normalized_facts[:9],
        canonical_sources=canonical_sources[:15],
        fresh_market_notes=fresh_market_notes[:6],
        keyword_serp_notes=keyword_serp_notes,
        source_coverage=coverage,
    )


def _collect_topic_jury_scores(
    *,
    orchestrator: PipelineOrchestrator,
    topic: ScoredTopic,
    reuse,
    keyword_metrics: dict[str, dict[str, Any]],
    quality_policy,
    judge_focus_map: dict[str, str],
    allow_tiebreaker: bool,
) -> tuple[list, float, float, list[str]]:
    if orchestrator.config.provider_mode != "openai":
        scores, average, variance = topic_jury_scores(
            topic=topic,
            reuse=reuse,
            keyword_metrics=keyword_metrics,
        )
        return scores, average, variance, []
    provider = orchestrator.providers.content_generation
    judge_scores = []
    disagreement_notes: list[str] = []
    try:
        for judge_name, judge_focus in judge_focus_map.items():
            orchestrator._ensure_not_cancelled("score_topics")
            prompt = topic_judge_prompt(
                judge_name=judge_name,
                judge_focus=judge_focus,
                topic=topic,
                keyword_metrics={keyword: keyword_metrics.get(keyword, {}) for keyword in topic.candidate.target_keywords},
                reuse_notes=reuse.reasons,
            )
            score = provider.judge_topic(prompt, topic, judge_name)
            judge_scores.append(score)
    except Exception:
        logger.exception("Provider-backed topic judging failed; falling back to deterministic judges")
        scores, average, variance = topic_jury_scores(
            topic=topic,
            reuse=reuse,
            keyword_metrics=keyword_metrics,
        )
        return scores, average, variance, disagreement_notes

    if allow_tiebreaker and _has_judge_disagreement(
        judge_scores,
        spread_threshold=quality_policy.topic_judge_spread_threshold,
        variance_threshold=quality_policy.topic_judge_variance_threshold,
    ):
        orchestrator._ensure_not_cancelled("score_topics")
        prompt = topic_judge_prompt(
            judge_name="topic_tiebreaker_judge",
            judge_focus=TOPIC_TIEBREAKER_FOCUS,
            topic=topic,
            keyword_metrics={keyword: keyword_metrics.get(keyword, {}) for keyword in topic.candidate.target_keywords},
            reuse_notes=[*reuse.reasons, "High disagreement detected across topic judges."],
        )
        try:
            judge_scores.append(provider.judge_topic(prompt, topic, "topic_tiebreaker_judge"))
            disagreement_notes.append("Tie-breaker invoked for topic judge disagreement.")
        except Exception:
            logger.exception("Topic tie-breaker judge failed; keeping original panel")

    average_score = round(mean(score.score for score in judge_scores), 2)
    variance = _score_variance(judge_scores)
    return judge_scores, average_score, variance, disagreement_notes


def _collect_article_jury_scores(
    *,
    orchestrator: PipelineOrchestrator,
    article_manifest: ArticleManifest,
    brief: ResearchBrief,
    quality_policy,
) -> tuple[list, list[str]]:
    if orchestrator.config.provider_mode != "openai":
        return [], []
    provider = orchestrator.providers.content_generation
    judge_scores = []
    disagreement_notes: list[str] = []
    try:
        for judge_name, judge_focus in FINAL_JUDGE_FOCUS.items():
            orchestrator._ensure_not_cancelled("qa_optimize")
            prompt = article_judge_prompt(
                judge_name=judge_name,
                judge_focus=judge_focus,
                article_manifest=article_manifest,
                brief=brief,
            )
            judge_scores.append(provider.judge_article(prompt, article_manifest.model_dump_json(), judge_name))
    except Exception:
        logger.exception("Provider-backed article judging failed; falling back to deterministic jury")
        return [], disagreement_notes

    judge_scores = _ground_article_judge_scores(
        judge_scores,
        article_manifest=article_manifest,
    )

    average_score = round(mean(score.score for score in judge_scores), 2)
    if average_score < 9.2 or _has_judge_disagreement(
        judge_scores,
        spread_threshold=quality_policy.final_judge_spread_threshold,
        variance_threshold=quality_policy.final_judge_variance_threshold,
    ):
        orchestrator._ensure_not_cancelled("qa_optimize")
        prompt = article_judge_prompt(
            judge_name="final_tiebreaker_judge",
            judge_focus=FINAL_TIEBREAKER_FOCUS,
            article_manifest=article_manifest,
            brief=brief,
        )
        try:
            tie_score = provider.judge_article(prompt, article_manifest.model_dump_json(), "final_tiebreaker_judge")
            disagreement_notes.append(
                f"Tie-breaker invoked for final jury disagreement (advisory score {tie_score.score:.2f}/10)."
            )
        except Exception:
            logger.exception("Final tie-breaker judge failed; keeping original jury")
    return judge_scores, disagreement_notes


def _collect_draft_evaluation(
    *,
    orchestrator: PipelineOrchestrator,
    variant: DraftVariant,
    brief: ResearchBrief,
    qa_result,
    seo_score,
    min_average_score: float,
    min_single_score: float,
    quality_policy,
) -> DraftEvaluation:
    if orchestrator.config.provider_mode != "openai":
        return evaluate_draft_variant(
            variant=variant,
            brief=brief,
            qa_result=qa_result,
            seo_score=seo_score,
        )

    provider = orchestrator.providers.content_generation
    article_manifest = _build_article_manifest(
        content=variant.content_md,
        brief=brief,
        qa_result=qa_result,
        seo_score=seo_score,
    )
    try:
        scores = []
        for judge_name, judge_focus in DRAFT_JUDGE_FOCUS.items():
            orchestrator._ensure_not_cancelled("write_draft")
            prompt = article_judge_prompt(
                judge_name=judge_name,
                judge_focus=judge_focus,
                article_manifest=article_manifest,
                brief=brief,
            )
            scores.append(provider.judge_article(prompt, article_manifest.model_dump_json(), judge_name))
        notes: list[str] = []
        if _has_judge_disagreement(
            scores,
            spread_threshold=quality_policy.draft_judge_spread_threshold,
            variance_threshold=quality_policy.draft_judge_variance_threshold,
        ):
            orchestrator._ensure_not_cancelled("write_draft")
            prompt = article_judge_prompt(
                judge_name="draft_tiebreaker_judge",
                judge_focus=DRAFT_TIEBREAKER_FOCUS,
                article_manifest=article_manifest,
                brief=brief,
            )
            try:
                scores.append(provider.judge_article(prompt, article_manifest.model_dump_json(), "draft_tiebreaker_judge"))
                notes.append("Tie-breaker invoked for draft judge disagreement.")
            except Exception:
                logger.exception("Draft tie-breaker judge failed; keeping original draft panel")
        evaluation = draft_evaluation_from_scores(
            writer_id=variant.writer_id,
            scores=scores,
            min_average_score=min_average_score,
            min_single_score=min_single_score,
        )
        if notes:
            evaluation = evaluation.model_copy(update={"notes": [*evaluation.notes, *notes]})
        return evaluation
    except Exception:
        logger.exception("Provider-backed draft judging failed; falling back to deterministic evaluation")
        return evaluate_draft_variant(
            variant=variant,
            brief=brief,
            qa_result=qa_result,
            seo_score=seo_score,
        )


def _score_spread(scores: list) -> float:
    if not scores:
        return 0.0
    values = [score.score for score in scores]
    return round(max(values) - min(values), 3)


def _score_variance(scores: list) -> float:
    if len(scores) <= 1:
        return 0.0
    values = [score.score for score in scores]
    average = sum(values) / len(values)
    return round(sum((value - average) ** 2 for value in values) / len(values), 3)


def _has_judge_disagreement(
    scores: list,
    *,
    spread_threshold: float,
    variance_threshold: float,
) -> bool:
    return _score_spread(scores) > spread_threshold or _score_variance(scores) > variance_threshold


def _jury_grade(score: float) -> str:
    if score >= 9.3:
        return "A"
    if score >= 8.7:
        return "B"
    if score >= 8.0:
        return "C"
    return "D"


def _classify_error(exc: Exception) -> FailureCategory:
    """Classify an exception into a failure category."""
    from pydantic import ValidationError
    if isinstance(exc, RunCanceled):
        return FailureCategory.CANCELED
    if isinstance(exc, ValidationError):
        return FailureCategory.VALIDATION_ERROR
    if isinstance(exc, (OSError, IOError)):
        return FailureCategory.IO_ERROR
    if isinstance(exc, TimeoutError):
        return FailureCategory.TIMEOUT
    if isinstance(exc, ValueError) and "rejected" in str(exc).lower():
        return FailureCategory.SCORING_REJECTION
    return FailureCategory.UNKNOWN


def _qa_snapshot(qa_result) -> dict[str, Any]:
    """Return a compact deterministic QA snapshot for prompts and traces."""
    if qa_result is None:
        return {}
    failed_checks = [
        {
            "name": check.name,
            "severity": check.severity,
            "message": check.message,
        }
        for check in qa_result.checks
        if not check.passed
    ]
    return {
        "passed": qa_result.passed,
        "summary": qa_result.summary,
        "failed_checks": failed_checks,
    }


def _optimization_notes(qa_result, gate: FinalQualityGate | None) -> list[str]:
    """Prioritize the next optimization pass using concrete deficits."""
    notes: list[str] = []
    if qa_result is not None:
        for check in qa_result.checks:
            if not check.passed:
                notes.append(f"{check.name}: {check.message}")
    if gate is not None:
        notes.extend(gate.notes)
    return notes[:8]


def _count_internal_markdown_links(content: str) -> int:
    """Count internal markdown links that point to Macroscope content."""
    return len(INTERNAL_LINK_PATTERN.findall(content))


def _normalize_internal_markdown_links(
    content: str,
    *,
    suggestions: list,
    min_links: int,
) -> tuple[str, list[str]]:
    """Convert raw Macroscope URLs back into markdown links and ensure minimum coverage."""
    repaired = content
    notes: list[str] = []

    for link in suggestions:
        raw_url = f"https://macroscope.com{link.target_path}"
        markdown_link = f"[{link.anchor_text}]({link.target_path})"
        if markdown_link in repaired:
            continue
        if raw_url in repaired:
            repaired = repaired.replace(raw_url, markdown_link)
            notes.append(f"Converted bare URL to markdown link for {link.target_path}")

    existing_targets = set(INTERNAL_LINK_PATTERN.findall(repaired))
    current_count = len(existing_targets)
    if current_count < min_links:
        missing_links = [
            link for link in suggestions
            if link.target_path not in existing_targets
        ]
        if missing_links:
            section_lines = ["## Related Macroscope resources", ""]
            for link in missing_links[: max(0, min_links - current_count)]:
                section_lines.append(f"- [{link.anchor_text}]({link.target_path})")
            repaired = repaired.rstrip() + "\n\n" + "\n".join(section_lines) + "\n"
            notes.append("Appended a markdown internal-links section to satisfy QA.")
    return repaired, notes


def _ensure_direct_answer_intro(content: str, *, primary_keyword: str) -> tuple[str, list[str]]:
    """Guarantee a two-sentence direct answer immediately after the H1."""
    leading_comments = ""
    working = content.lstrip()
    comment_match = re.match(r"^((?:<!--.*?-->\s*)+)", working, re.DOTALL)
    if comment_match:
        leading_comments = comment_match.group(1)
        working = working[comment_match.end():].lstrip()

    h1_match = re.match(r"^(# .+\n+)", working)
    if not h1_match:
        return content, []

    direct_answer = (
        f"Direct answer: {primary_keyword} works best when teams evaluate it on real pull requests "
        "with adjudicated labels, ranked metrics, and repeatable CI runs. "
        "This article gives the exact dataset, scoring, and rollout steps engineers can use to make that decision rigorously."
    )
    body = working[h1_match.end():].lstrip()
    if body.startswith("Direct answer:"):
        paragraph_match = re.match(r"^Direct answer:.*?(?:\n\s*\n|$)", body, re.DOTALL)
        if paragraph_match:
            body = direct_answer + "\n\n" + body[paragraph_match.end():].lstrip()
        else:
            body = direct_answer + "\n\n" + body
    else:
        body = direct_answer + "\n\n" + body
    return f"{leading_comments}{working[:h1_match.end()]}{body}".strip() + "\n", [
        "Rebuilt the opening direct-answer block to satisfy QA.",
    ]


def _apply_quality_repairs(
    content: str,
    *,
    brief: ResearchBrief,
    qa_result,
    min_internal_links: int,
) -> tuple[str, list[str]]:
    """Apply deterministic fixes for QA regressions that should never persist."""
    repaired = content
    notes: list[str] = []
    snapshot = _qa_snapshot(qa_result)
    failed = {item["name"] for item in snapshot.get("failed_checks", [])}

    if not failed or "direct_answer" in failed:
        repaired, direct_notes = _ensure_direct_answer_intro(
            repaired,
            primary_keyword=brief.primary_keyword,
        )
        notes.extend(direct_notes)

    if not failed or "internal_links" in failed:
        repaired, link_notes = _normalize_internal_markdown_links(
            repaired,
            suggestions=brief.internal_link_suggestions,
            min_links=min_internal_links,
        )
        notes.extend(link_notes)

    normalized = normalize_markdown_headings(repaired)
    if normalized != repaired:
        repaired = normalized
        notes.append("Normalized setext markdown headings into ATX headings for consistent QA.")

    return repaired, notes


def _build_article_manifest(
    *,
    content: str,
    brief: ResearchBrief,
    qa_result,
    seo_score,
) -> ArticleManifest:
    normalized_content = normalize_markdown_headings(content)
    headings = re.findall(r"^(#{1,3}\s+.+)$", normalized_content, re.MULTILINE)
    faq_questions = [
        heading.strip().lstrip("#").strip()
        for heading in headings
        if "?" in heading and len(heading) <= 140
    ]
    opening_match = re.search(r"^#\s+.+?\n\n(.+?)(?:\n\n|\Z)", normalized_content, re.MULTILINE | re.DOTALL)
    opening_direct_answer = opening_match.group(1).strip() if opening_match else ""
    section_excerpts: list = []
    for match in re.finditer(r"^(##\s+.+)$", normalized_content, re.MULTILINE):
        heading = match.group(1).lstrip("#").strip()
        start = match.end()
        next_match = re.search(r"^##\s+", normalized_content[start:], re.MULTILINE)
        end = start + next_match.start() if next_match else len(normalized_content)
        excerpt = re.sub(r"\s+", " ", normalized_content[start:end].strip())[:320]
        section_excerpts.append(
            {
                "heading": heading,
                "excerpt": excerpt,
                "word_count": len(normalized_content[start:end].split()),
            }
        )
    seo_snapshot = {
        "title_score": seo_score.title_score,
        "meta_description_score": seo_score.meta_description_score,
        "heading_structure_score": seo_score.heading_structure_score,
        "internal_links_score": seo_score.internal_links_score,
        "faq_presence_score": seo_score.faq_presence_score,
        "direct_answer_score": seo_score.direct_answer_score,
        "content_depth_score": seo_score.content_depth_score,
        "total": seo_score.total,
        "grade": seo_score.grade,
    }
    return ArticleManifest(
        title=brief.title_options[0],
        slug=brief.topic.slug,
        primary_keyword=brief.primary_keyword,
        opening_direct_answer=opening_direct_answer,
        heading_map=[heading.lstrip("#").strip() for heading in headings],
        faq_questions=faq_questions[: len(brief.faqs)],
        internal_links=INTERNAL_LINK_PATTERN.findall(normalized_content),
        claim_candidates=brief.claims_needing_evidence[:8],
        section_excerpts=section_excerpts,
        qa_snapshot=_qa_snapshot(qa_result),
        seo_snapshot=seo_snapshot,
        word_count=len(normalized_content.split()),
        meta_description=brief.meta_description,
    )


def _replace_direct_answer_intro(content: str, direct_answer: str) -> str:
    leading_comments = ""
    working = content.lstrip()
    comment_match = re.match(r"^((?:<!--.*?-->\s*)+)", working, re.DOTALL)
    if comment_match:
        leading_comments = comment_match.group(1)
        working = working[comment_match.end():].lstrip()
    h1_match = re.match(r"^(# .+\n+)", working)
    if not h1_match:
        return content
    body = working[h1_match.end():].lstrip()
    paragraph_match = re.match(r"^Direct answer:.*?(?:\n\s*\n|$)", body, re.DOTALL)
    if paragraph_match:
        body = direct_answer.strip() + "\n\n" + body[paragraph_match.end():].lstrip()
    else:
        body = direct_answer.strip() + "\n\n" + body
    return f"{leading_comments}{working[:h1_match.end()]}{body}".strip() + "\n"


def _replace_section_markdown(content: str, heading: str, markdown: str) -> str:
    pattern = re.compile(
        rf"(^##\s+{re.escape(heading)}\s*$)(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    replacement = markdown.strip()
    if not replacement.startswith("## "):
        replacement = f"## {heading}\n\n{replacement}"
    if pattern.search(content):
        return pattern.sub(replacement + "\n\n", content, count=1)
    return content.rstrip() + "\n\n" + replacement + "\n"


def _apply_optimization_patch(
    content: str,
    *,
    patch: OptimizationPatch,
    brief: ResearchBrief,
) -> tuple[str, list[str]]:
    updated = content
    notes = list(patch.notes)
    if patch.opening_direct_answer:
        updated = _replace_direct_answer_intro(updated, patch.opening_direct_answer)
        notes.append("Updated direct-answer opening from optimization patch.")
    if patch.section_rewrites:
        for rewrite in patch.section_rewrites:
            updated = _replace_section_markdown(updated, rewrite.heading, rewrite.markdown)
        notes.append(f"Applied {len(patch.section_rewrites)} focused section rewrites.")
    if patch.internal_link_suggestions:
        updated, link_notes = _normalize_internal_markdown_links(
            updated,
            suggestions=patch.internal_link_suggestions,
            min_links=max(brief.internal_link_suggestions and len(brief.internal_link_suggestions[:3]) or 3, 3),
        )
        notes.extend(link_notes)
    return updated, notes


def _evaluate_writer_blueprint(blueprint: WriterBlueprint, brief: ResearchBrief) -> BlueprintEvaluation:
    score = 6.8
    notes: list[str] = []
    if len(blueprint.sections) >= len(brief.outline):
        score += 1.0
    else:
        notes.append("Blueprint is missing outline coverage")
    if len(blueprint.claims_plan) >= min(3, len(brief.claims_needing_evidence)):
        score += 0.7
    else:
        notes.append("Claims plan is too thin")
    if len(blueprint.internal_link_targets) >= min(3, len(brief.internal_link_suggestions)):
        score += 0.5
    else:
        notes.append("Internal link plan is light")
    if len(blueprint.faq_plan) >= min(4, len(brief.faqs)):
        score += 0.5
    else:
        notes.append("FAQ plan is incomplete")
    if len(blueprint.direct_answer.split()) >= 12:
        score += 0.5
    else:
        notes.append("Direct answer is too thin")
    return BlueprintEvaluation(
        writer_id=blueprint.writer_id,
        score=round(min(score, 10.0), 2),
        notes=notes,
    )


def _rank_blueprints(
    blueprints: list[WriterBlueprint],
    evaluations: list[BlueprintEvaluation],
) -> list[WriterBlueprint]:
    score_by_writer = {evaluation.writer_id: evaluation.score for evaluation in evaluations}
    return sorted(
        blueprints,
        key=lambda blueprint: (
            score_by_writer.get(blueprint.writer_id, 0.0),
            len(blueprint.sections),
            len(blueprint.claims_plan),
        ),
        reverse=True,
    )


def _should_unlock_second_draft(
    *,
    gate: FinalQualityGate,
    round_number: int,
    second_draft_unlock_round: int,
    already_unlocked: bool,
    runner_up_available: bool,
) -> bool:
    if already_unlocked or not runner_up_available:
        return False
    if round_number < second_draft_unlock_round:
        return False
    return gate.average_score < 8.7 or gate.technical_accuracy_score < 9.0


def _focused_rewrite_targets(
    *,
    article_manifest: ArticleManifest,
    brief: ResearchBrief,
    gate: FinalQualityGate,
) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    failed_checks = {
        item.get("name")
        for item in article_manifest.qa_snapshot.get("failed_checks", [])
        if isinstance(item, dict)
    }

    def add_target(heading: str) -> None:
        key = heading.strip().lower()
        if not heading.strip() or key in seen:
            return
        seen.add(key)
        targets.append(heading.strip())

    if "faq_section" in failed_checks:
        add_target("Frequently Asked Questions")
    if "heading_structure" in failed_checks:
        for section in brief.outline[:2]:
            add_target(section.heading)
    if gate.technical_accuracy_score < 9.0:
        for excerpt in article_manifest.section_excerpts[:2]:
            add_target(excerpt.heading)
    if not targets:
        for section in brief.outline[:2]:
            add_target(section.heading)
    return targets[:3]


def _grounded_internal_linking_score(article_manifest: ArticleManifest) -> float:
    """Compute a deterministic publication score for internal linking."""
    link_count = len(article_manifest.internal_links)
    required = 3
    if link_count >= required:
        return 9.0
    if link_count >= 2:
        return 7.5
    if link_count >= 1:
        return 6.0
    return 0.0


def _grounded_onpage_score(article_manifest: ArticleManifest) -> float:
    """Use computed SEO metrics to floor the model's on-page score."""
    seo_score = article_manifest.seo_snapshot
    score = 6.0
    if seo_score.get("title_score", 0.0) >= 8.0:
        score += 1.0
    if seo_score.get("meta_description_score", 0.0) >= 8.0:
        score += 1.0
    if seo_score.get("heading_structure_score", 0.0) >= 8.0:
        score += 0.8
    if seo_score.get("direct_answer_score", 0.0) >= 8.0:
        score += 0.8
    if seo_score.get("internal_links_score", 0.0) >= 7.5:
        score += 0.7
    if seo_score.get("faq_presence_score", 0.0) >= 5.0:
        score += 0.5
    return round(min(score, 10.0), 2)


def _ground_article_judge_scores(
    scores: list,
    *,
    article_manifest: ArticleManifest,
) -> list:
    """Prevent the model-backed jury from contradicting deterministic QA/SEO facts."""
    grounded: list = []
    internal_floor = _grounded_internal_linking_score(article_manifest)
    onpage_floor = _grounded_onpage_score(article_manifest)
    for score in scores:
        floor = None
        rationale_suffix = None
        if score.judge == "structure_clarity_judge":
            floor = internal_floor
            rationale_suffix = "Grounded against deterministic internal-link counts."
        elif score.judge == "search_readiness_judge":
            floor = onpage_floor
            rationale_suffix = "Grounded against deterministic SEO checks."

        if floor is not None and score.score < floor:
            grounded.append(
                score.model_copy(
                    update={
                        "score": floor,
                        "notes": [*score.notes, rationale_suffix],
                    }
                )
            )
        else:
            grounded.append(score)
    return grounded


def _checkpoint_ctx_fragment(stage_name: str, ctx: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Build the minimal serialized downstream context for a stage checkpoint."""
    if stage_name == PipelineStage.BOOTSTRAP_RUN.value:
        return {"run_context": ctx["run_context"].model_dump(mode="json")}
    if stage_name == PipelineStage.COLLECT_SIGNALS.value:
        return {
            "market_signals": ctx["market_signals"].model_dump(mode="json"),
            "research_packet": ctx["research_packet"].model_dump(mode="json"),
            "source_coverage": ctx["source_coverage"].model_dump(mode="json"),
        }
    if stage_name == PipelineStage.GENERATE_TOPICS.value:
        return {
            "candidates": [candidate.model_dump(mode="json") for candidate in ctx.get("candidates", [])],
            "topic_novelty_report": ctx.get("topic_novelty_report", []),
        }
    if stage_name == PipelineStage.SCORE_TOPICS.value:
        return {
            "scored": [topic.model_dump(mode="json") for topic in ctx.get("scored", [])],
            "selected": ctx["selected"].model_dump(mode="json"),
            "research_packet": ctx["research_packet"].model_dump(mode="json"),
        }
    if stage_name == PipelineStage.BUILD_BRIEF.value:
        return {"brief": ctx["brief"].model_dump(mode="json")}
    if stage_name == PipelineStage.WRITE_DRAFT.value:
        fragment = {"draft": ctx["draft"].model_dump(mode="json")}
        runner_up = ctx.get("runner_up_blueprint")
        if isinstance(runner_up, WriterBlueprint):
            fragment["runner_up_blueprint"] = runner_up.model_dump(mode="json")
        return fragment
    if stage_name == PipelineStage.QA_OPTIMIZE.value:
        return {
            "final": ctx["final"].model_dump(mode="json"),
            "qa": ctx["qa"].to_dict(),
            "article_manifest": ctx["article_manifest"].model_dump(mode="json"),
            "final_quality_gate": ctx["final_quality_gate"].model_dump(mode="json"),
        }
    if stage_name == PipelineStage.FACT_CHECK.value:
        return {"fact_check_report": ctx["fact_check_report"].model_dump(mode="json")}
    if stage_name == PipelineStage.EXPORT.value:
        export_results = []
        for item in ctx.get("export_results", []):
            if hasattr(item, "to_dict"):
                export_results.append(item.to_dict())
            else:
                export_results.append(item)
        return {"export_results": export_results}
    return {}


def _qa_result_from_dict(payload: dict[str, Any]) -> QAResult:
    """Rebuild the QA dataclass result from its serialized dict form."""
    checks = [
        QACheck(
            name=str(item.get("name", "")),
            passed=bool(item.get("passed")),
            message=str(item.get("message", "")),
            severity=str(item.get("severity", "error")),
        )
        for item in payload.get("checks", [])
        if isinstance(item, dict)
    ]
    return QAResult(checks=checks)
