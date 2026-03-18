"""OpenAI-backed provider implementations.

Uses the Responses API plus built-in web search to replace the mock research
and content-generation stages with live model-backed behavior.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import BaseModel, Field, ValidationError
from slugify import slugify
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import EngineConfig
from .providers import ContentGenerationProvider, KeywordDataProvider, MarketSignalProvider
from .schemas import (
    ArticleManifest,
    BriefClaimsPlan,
    BriefEntityPlan,
    BriefFAQPlan,
    BriefLinkPlan,
    BriefOutlinePlan,
    FactCheckReport,
    JudgeScore,
    MarketSignalReport,
    OptimizationPatch,
    ProviderCallUsage,
    ResearchPacket,
    ResearchBrief,
    ScoredTopic,
    TopicCandidate,
    WriterBlueprint,
)

logger = logging.getLogger(__name__)
_STRUCTURED_TOKEN_MULTIPLIERS = (1.0, 1.5, 2.0)


class TopicCandidateBatch(BaseModel):
    topics: list[TopicCandidate]


class KeywordMetricItem(BaseModel):
    keyword: str
    volume: int = Field(..., ge=0)
    difficulty: int = Field(..., ge=0, le=100)
    cpc: float = Field(default=0.0, ge=0.0)
    trend: str = Field(default="unknown")
    note: str = ""


class KeywordMetricBatch(BaseModel):
    metrics: list[KeywordMetricItem]


class SERPResult(BaseModel):
    position: int = Field(..., ge=1)
    title: str
    url: str


class SERPAnalysis(BaseModel):
    keyword: str
    top_results: list[SERPResult]
    featured_snippet: bool = False
    people_also_ask: list[str] = Field(default_factory=list)
    note: str = ""


def _retryable_errors() -> Any:
    return (APIConnectionError, APITimeoutError, RateLimitError)


class OpenAIProviderBase:
    """Shared OpenAI client helpers for the provider implementations."""

    def __init__(self, config: EngineConfig, model: str):
        client_args: dict[str, Any] = {
            "api_key": config.openai_api_key,
            "timeout": config.openai_timeout_seconds,
        }
        if config.openai_base_url:
            client_args["base_url"] = config.openai_base_url
        self.client = OpenAI(**client_args)
        self.config = config
        self.model = model
        self._usage_records: list[ProviderCallUsage] = []
        self.cache_dir = config.data_dir / "cache" / "openai"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def drain_usage_records(self) -> list[ProviderCallUsage]:
        """Return and clear buffered usage records."""
        records = list(self._usage_records)
        self._usage_records.clear()
        return records

    def _day_bucket(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _cache_path(self, namespace: str, cache_key: str) -> Path:
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        path = self.cache_dir / namespace / f"{self._day_bucket()}_{digest}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _load_cache(self, namespace: str, cache_key: str) -> Any | None:
        path = self._cache_path(namespace, cache_key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _save_cache(self, namespace: str, cache_key: str, payload: Any) -> None:
        path = self._cache_path(namespace, cache_key)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _record_cached_usage(self, operation: str, *, web_search_used: bool) -> None:
        self._usage_records.append(
            ProviderCallUsage(
                provider=self.__class__.__name__,
                operation=operation,
                model=self.model,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                web_search_used=web_search_used,
                cached=True,
            )
        )

    def _record_response_usage(
        self,
        *,
        operation: str,
        response: Any,
        web_search_used: bool,
    ) -> None:
        usage = getattr(response, "usage", None)
        input_tokens = _usage_value(usage, "input_tokens")
        output_tokens = _usage_value(usage, "output_tokens")
        total_tokens = _usage_value(usage, "total_tokens")
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens
        self._usage_records.append(
            ProviderCallUsage(
                provider=self.__class__.__name__,
                operation=operation,
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                web_search_used=web_search_used,
                cached=False,
            )
        )

    def _web_search_tool(
        self,
        *,
        search_context_size: str | None = None,
        allowed_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        tool: dict[str, Any] = {
            "type": "web_search",
            "search_context_size": search_context_size or self.config.openai_search_context_size,
        }
        if allowed_domains:
            tool["filters"] = {"allowed_domains": allowed_domains}
        return tool

    def _base_request_kwargs(
        self,
        *,
        use_web_search: bool,
        max_output_tokens: int,
        reasoning_effort: str | None = None,
        search_context_size: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_output_tokens": max_output_tokens,
            "reasoning": {"effort": reasoning_effort or self.config.openai_reasoning_effort},
        }
        if use_web_search and self.config.openai_enable_web_search:
            kwargs["tools"] = [
                self._web_search_tool(search_context_size=search_context_size)
            ]
            kwargs["include"] = ["web_search_call.action.sources"]
        return kwargs

    @retry(
        retry=retry_if_exception_type(_retryable_errors()),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _parse(
        self,
        *,
        text_format: type[BaseModel],
        operation: str,
        prompt: str,
        use_web_search: bool,
        max_output_tokens: int,
        reasoning_effort: str = "low",
        verbosity: str = "low",
        search_context_size: str | None = None,
    ) -> BaseModel:
        response_format = type_to_text_format_param(text_format)
        formatted_prompt = _structured_output_prompt(prompt)
        last_error: Exception | None = None

        for token_budget in _structured_token_budgets(max_output_tokens):
            response = self.client.responses.create(
                input=formatted_prompt,
                text={"format": response_format, "verbosity": verbosity},
                **self._base_request_kwargs(
                    use_web_search=use_web_search,
                    max_output_tokens=token_budget,
                    reasoning_effort=reasoning_effort,
                    search_context_size=search_context_size,
                ),
            )
            self._record_response_usage(
                operation=operation,
                response=response,
                web_search_used=use_web_search and self.config.openai_enable_web_search,
            )
            output_text = (getattr(response, "output_text", "") or "").strip()
            if not output_text:
                last_error = ValueError(
                    f"OpenAI response did not contain structured text output "
                    f"(status={response.status}, incomplete={response.incomplete_details})"
                )
                if response.status == "incomplete":
                    continue
                break

            try:
                return text_format.model_validate_json(output_text)
            except ValidationError as exc:
                last_error = ValueError(
                    f"OpenAI structured output failed validation for {text_format.__name__}: "
                    f"{exc.errors()[0]['msg']}"
                )
                if response.status == "incomplete" or _looks_truncated(exc):
                    logger.warning(
                        "Retrying structured output for %s after validation failure "
                        "(status=%s, tokens=%s): %s",
                        text_format.__name__,
                        response.status,
                        token_budget,
                        exc.errors()[0]["msg"],
                    )
                    continue
                break

        raise last_error or ValueError("OpenAI structured output failed unexpectedly")

    @retry(
        retry=retry_if_exception_type(_retryable_errors()),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _text(
        self,
        *,
        operation: str,
        prompt: str,
        use_web_search: bool,
        max_output_tokens: int,
        search_context_size: str | None = None,
    ) -> str:
        response = self.client.responses.create(
            input=prompt,
            **self._base_request_kwargs(
                use_web_search=use_web_search,
                max_output_tokens=max_output_tokens,
                reasoning_effort=self.config.openai_reasoning_effort,
                search_context_size=search_context_size,
            ),
        )
        self._record_response_usage(
            operation=operation,
            response=response,
            web_search_used=use_web_search and self.config.openai_enable_web_search,
        )
        output_text = getattr(response, "output_text", "").strip()
        if not output_text:
            raise ValueError("OpenAI response did not contain text output")
        return output_text


class OpenAIMarketSignalProvider(OpenAIProviderBase, MarketSignalProvider):
    """Collects market signals via web-enabled OpenAI research."""

    def __init__(self, config: EngineConfig):
        super().__init__(config, config.openai_market_model)

    def collect(
        self,
        themes: list[str],
        lookback_days: int = 14,
        prompt: str | None = None,
    ) -> MarketSignalReport:
        cache_key = json.dumps(
            {"themes": sorted(themes), "lookback_days": lookback_days, "prompt": prompt or ""},
            sort_keys=True,
        )
        cached = self._load_cache("research", cache_key)
        if cached is not None:
            self._record_cached_usage("collect_market_signals", web_search_used=True)
            return MarketSignalReport.model_validate(cached)

        base_prompt = prompt or (
            "Collect recent market signals relevant to these themes: "
            f"{', '.join(themes)}."
        )
        full_prompt = (
            f"{base_prompt}\n\nUse live web research from the last {lookback_days} days when available. "
            "Return a MarketSignalReport with at most 3 signals, at most 5 trending themes, and at most 2 recommended angles. "
            "Prefer primary sources, recent product announcements, engineering blogs, and credible community discussions."
        )
        parsed = self._parse(
            text_format=MarketSignalReport,
            operation="collect_market_signals",
            prompt=full_prompt,
            use_web_search=True,
            max_output_tokens=2200,
            search_context_size="medium",
        )
        trimmed = _trim_market_signal_report(parsed)
        self._save_cache("research", cache_key, trimmed.model_dump())
        return trimmed  # type: ignore[return-value]


class OpenAIKeywordDataProvider(OpenAIProviderBase, KeywordDataProvider):
    """Approximates keyword metrics and SERP analysis from web research."""

    def __init__(self, config: EngineConfig):
        super().__init__(config, config.openai_market_model)
        self._metrics_cache: dict[str, dict[str, Any]] = {}
        self._serp_cache: dict[str, dict[str, Any]] = {}

    def get_keyword_metrics(self, keywords: list[str]) -> dict[str, dict[str, Any]]:
        missing = [keyword for keyword in keywords if keyword not in self._metrics_cache]
        if missing:
            cache_key = json.dumps({"keywords": sorted(missing)}, sort_keys=True)
            cached = self._load_cache("keyword_metrics", cache_key)
            if cached is not None:
                self._record_cached_usage("keyword_metrics_lookup", web_search_used=False)
                for keyword, metric in cached.items():
                    self._metrics_cache[keyword] = metric
            else:
                prompt = (
                    "Estimate keyword opportunity from the provided search terms using concise heuristics.\n"
                    f"{missing}\n\n"
                    "Return one metric item per keyword with:\n"
                    "- volume: estimated monthly searches as an integer\n"
                    "- difficulty: 0-100 estimated ranking difficulty\n"
                    "- cpc: estimated commercial value in USD\n"
                    "- trend: one of up, stable, down, unknown\n"
                    "- note: a short explanation grounded in the query wording only\n"
                )
                parsed = self._parse(
                    text_format=KeywordMetricBatch,
                    operation="keyword_metrics_lookup",
                    prompt=prompt,
                    use_web_search=False,
                    max_output_tokens=1800,
                    search_context_size="low",
                )
                bundle: dict[str, dict[str, Any]] = {}
                for item in parsed.metrics:
                    bundle[item.keyword] = {
                        "volume": item.volume,
                        "difficulty": item.difficulty,
                        "cpc": item.cpc,
                        "trend": item.trend.lower().strip(),
                        "note": item.note,
                    }
                    self._metrics_cache[item.keyword] = bundle[item.keyword]
                self._save_cache("keyword_metrics", cache_key, bundle)

        return {
            keyword: self._metrics_cache.get(
                keyword,
                {
                    "volume": 300,
                    "difficulty": 40,
                    "cpc": 0.0,
                    "trend": "unknown",
                    "note": "fallback estimate",
                },
            )
            for keyword in keywords
        }

    def get_serp_analysis(self, keyword: str) -> dict[str, Any]:
        if keyword in self._serp_cache:
            return self._serp_cache[keyword]

        cache_key = json.dumps({"keyword": keyword}, sort_keys=True)
        cached = self._load_cache("serp_analysis", cache_key)
        if cached is not None:
            self._record_cached_usage("serp_analysis_lookup", web_search_used=False)
            self._serp_cache[keyword] = cached
            return cached

        prompt = (
            f"Estimate the search intent and likely SERP shape for the keyword '{keyword}'.\n\n"
            "Return:\n"
            "- top_results: the top 5 organic results with title and url\n"
            "- featured_snippet: whether a featured snippet or direct answer appears\n"
            "- people_also_ask: 3-6 related follow-up questions if visible or implied\n"
            "- note: 1-2 sentences on likely search intent and SERP shape\n"
        )
        parsed = self._parse(
            text_format=SERPAnalysis,
            operation="serp_analysis_lookup",
            prompt=prompt,
            use_web_search=False,
            max_output_tokens=1400,
            search_context_size="low",
        )
        result = parsed.model_dump()
        self._serp_cache[keyword] = result
        self._save_cache("serp_analysis", cache_key, result)
        return result


class OpenAIContentGenerationProvider(OpenAIProviderBase, ContentGenerationProvider):
    """Generates topics, briefs, drafts, and edits with OpenAI models."""

    def __init__(self, config: EngineConfig):
        super().__init__(config, config.openai_content_model)

    def generate_topics(
        self,
        prompt: str,
        market_signals: MarketSignalReport,
    ) -> list[TopicCandidate]:
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Use only the provided research packet and signals. Do not browse the web.\n"
            "Return an object with a single key `topics` containing the topic list."
        )
        parsed = self._parse(
            text_format=TopicCandidateBatch,
            operation="generate_topics",
            prompt=wrapped_prompt,
            use_web_search=False,
            max_output_tokens=2400,
            search_context_size="low",
        )
        return _normalize_topic_candidates(parsed.topics)

    def generate_brief(self, prompt: str, topic: ScoredTopic) -> ResearchBrief:
        parsed = self._parse(
            text_format=ResearchBrief,
            operation="generate_brief",
            prompt=prompt,
            use_web_search=False,
            max_output_tokens=3200,
            search_context_size="low",
        )
        brief = parsed.model_copy(update={"topic": topic.candidate})
        return brief  # type: ignore[return-value]

    def generate_brief_outline(self, prompt: str, topic: ScoredTopic) -> BriefOutlinePlan:
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Return a BriefOutlinePlan with the article outline, target word count, and 2-4 title options."
        )
        parsed = self._parse(
            text_format=BriefOutlinePlan,
            operation="generate_brief_outline",
            prompt=wrapped_prompt,
            use_web_search=False,
            max_output_tokens=1800,
            search_context_size="low",
        )
        return parsed  # type: ignore[return-value]

    def generate_brief_entities(self, prompt: str, topic: ScoredTopic) -> BriefEntityPlan:
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Use only the provided topic and research context. Return a BriefEntityPlan."
        )
        parsed = self._parse(
            text_format=BriefEntityPlan,
            operation="generate_brief_entities",
            prompt=wrapped_prompt,
            use_web_search=False,
            max_output_tokens=1500,
            search_context_size="low",
        )
        return parsed  # type: ignore[return-value]

    def generate_brief_faqs(self, prompt: str, topic: ScoredTopic) -> BriefFAQPlan:
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Use only the provided topic and research context. Return a BriefFAQPlan."
        )
        parsed = self._parse(
            text_format=BriefFAQPlan,
            operation="generate_brief_faqs",
            prompt=wrapped_prompt,
            use_web_search=False,
            max_output_tokens=1800,
            search_context_size="low",
        )
        return parsed  # type: ignore[return-value]

    def generate_brief_links(self, prompt: str, topic: ScoredTopic) -> BriefLinkPlan:
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Return a BriefLinkPlan. Suggest realistic internal links even if you must infer path slugs from existing titles."
        )
        parsed = self._parse(
            text_format=BriefLinkPlan,
            operation="generate_brief_links",
            prompt=wrapped_prompt,
            use_web_search=False,
            max_output_tokens=1200,
            search_context_size="low",
        )
        return parsed  # type: ignore[return-value]

    def generate_brief_claims(self, prompt: str, topic: ScoredTopic) -> BriefClaimsPlan:
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Use only the provided topic and research context. Return a BriefClaimsPlan."
        )
        parsed = self._parse(
            text_format=BriefClaimsPlan,
            operation="generate_brief_claims",
            prompt=wrapped_prompt,
            use_web_search=False,
            max_output_tokens=1500,
            search_context_size="low",
        )
        return parsed  # type: ignore[return-value]

    def generate_brief_bundle(
        self,
        prompt: str,
        topic: ScoredTopic,
        research_packet: ResearchPacket,
    ) -> ResearchBrief:
        parsed = self._parse(
            text_format=ResearchBrief,
            operation="generate_brief_bundle",
            prompt=prompt,
            use_web_search=False,
            max_output_tokens=3200,
            search_context_size="low",
        )
        return parsed.model_copy(update={"topic": topic.candidate})  # type: ignore[return-value]

    def generate_writer_blueprint(
        self,
        prompt: str,
        brief: ResearchBrief,
        research_packet: ResearchPacket,
        writer_id: str,
        writer_label: str,
    ) -> WriterBlueprint:
        parsed = self._parse(
            text_format=WriterBlueprint,
            operation=f"generate_writer_blueprint:{writer_id}",
            prompt=prompt,
            use_web_search=False,
            max_output_tokens=1600,
            search_context_size="low",
        )
        return parsed.model_copy(update={"writer_id": writer_id, "writer_label": writer_label})  # type: ignore[return-value]

    def generate_draft_from_blueprint(
        self,
        prompt: str,
        brief: ResearchBrief,
        blueprint: WriterBlueprint,
        research_packet: ResearchPacket,
    ) -> str:
        article = self._text(
            operation=f"generate_draft_from_blueprint:{blueprint.writer_id}",
            prompt=prompt,
            use_web_search=False,
            max_output_tokens=5000,
            search_context_size="low",
        )
        return _ensure_h1(article, blueprint.title or brief.title_options[0])

    def generate_draft(self, prompt: str, brief: ResearchBrief) -> str:
        article = self._text(
            operation="generate_draft",
            prompt=prompt,
            use_web_search=False,
            max_output_tokens=5000,
            search_context_size="low",
        )
        return _ensure_h1(article, brief.title_options[0])

    def optimize_draft(self, prompt: str, draft: str) -> str:
        return self._text(
            operation="optimize_draft",
            prompt=prompt,
            use_web_search=False,
            max_output_tokens=3000,
            search_context_size="low",
        )

    def optimize_sections(
        self,
        prompt: str,
        content: str,
        manifest: ArticleManifest,
    ) -> OptimizationPatch:
        parsed = self._parse(
            text_format=OptimizationPatch,
            operation="optimize_sections",
            prompt=prompt,
            use_web_search=False,
            max_output_tokens=2200,
            search_context_size="low",
        )
        return parsed  # type: ignore[return-value]

    def judge_topic(self, prompt: str, topic: ScoredTopic, judge_name: str) -> JudgeScore:
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Return a JudgeScore with the provided judge name, a 0-10 score, a concise rationale, and short notes."
        )
        parsed = self._parse(
            text_format=JudgeScore,
            operation=f"judge_topic:{judge_name}",
            prompt=wrapped_prompt,
            use_web_search=False,
            max_output_tokens=900,
            search_context_size="low",
        )
        return parsed.model_copy(update={"judge": judge_name})  # type: ignore[return-value]

    def judge_article(self, prompt: str, content: str, judge_name: str) -> JudgeScore:
        wrapped_prompt = (
            f"{prompt}\n\n"
            "Return a JudgeScore with the provided judge name, a 0-10 score, a concise rationale, and short notes."
        )
        parsed = self._parse(
            text_format=JudgeScore,
            operation=f"judge_article:{judge_name}",
            prompt=wrapped_prompt,
            use_web_search=False,
            max_output_tokens=900,
            search_context_size="low",
        )
        return parsed.model_copy(update={"judge": judge_name})  # type: ignore[return-value]

    def fact_check_claims(
        self,
        prompt: str,
        manifest: ArticleManifest,
    ) -> FactCheckReport:
        cache_key = json.dumps(
            {"slug": manifest.slug, "claims": manifest.claim_candidates, "headings": manifest.heading_map},
            sort_keys=True,
        )
        cached = self._load_cache("fact_check", cache_key)
        if cached is not None:
            self._record_cached_usage("fact_check_claims", web_search_used=True)
            return FactCheckReport.model_validate(cached)

        parsed = self._parse(
            text_format=FactCheckReport,
            operation="fact_check_claims",
            prompt=prompt,
            use_web_search=True,
            max_output_tokens=1800,
            search_context_size="medium",
        )
        self._save_cache("fact_check", cache_key, parsed.model_dump())
        return parsed  # type: ignore[return-value]


def _normalize_topic_candidates(candidates: list[TopicCandidate]) -> list[TopicCandidate]:
    deduped: list[TopicCandidate] = []
    seen_slugs: set[str] = set()

    for candidate in candidates:
        normalized = candidate.model_copy(
            update={
                "slug": slugify(candidate.slug or candidate.title),
                "target_keywords": [keyword.lower().strip() for keyword in candidate.target_keywords],
            }
        )
        if normalized.slug in seen_slugs:
            continue
        seen_slugs.add(normalized.slug)
        deduped.append(normalized)

    return deduped


def _trim_market_signal_report(report: MarketSignalReport) -> MarketSignalReport:
    return report.model_copy(
        update={
            "signals": report.signals[:3],
            "trending_themes": report.trending_themes[:5],
            "recommended_angles": report.recommended_angles[:2],
        }
    )


def _ensure_h1(article: str, fallback_title: str) -> str:
    cleaned = article.strip()
    if cleaned.startswith("# "):
        return cleaned
    return f"# {fallback_title}\n\n{cleaned}"


def _structured_output_prompt(prompt: str) -> str:
    return (
        f"{prompt.strip()}\n\n"
        "Return only compact JSON that matches the schema exactly. "
        "Do not wrap the JSON in markdown fences. "
        "Keep string fields concise. "
        "Do not embed markdown links, citations, or source lists inside text fields unless "
        "the schema explicitly provides a URL field for them."
    )


def _structured_token_budgets(base_budget: int) -> list[int]:
    budgets = [max(1000, int(base_budget * multiplier)) for multiplier in _STRUCTURED_TOKEN_MULTIPLIERS]
    deduped: list[int] = []
    for budget in budgets:
        if budget not in deduped:
            deduped.append(budget)
    return deduped


def _looks_truncated(exc: ValidationError) -> bool:
    return any(
        error.get("type") == "json_invalid" and "EOF while parsing" in error.get("msg", "")
        for error in exc.errors()
    )


def _usage_value(usage: Any, field: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get(field, 0) or 0)
    return int(getattr(usage, field, 0) or 0)
