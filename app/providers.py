"""Provider interfaces for external integrations.

Defines abstract protocols for market signals, keyword data, document export,
and content generation. Each protocol has a mock implementation that ships
with the engine and can be swapped for real implementations without changing
business logic.

Integration points for MCP or direct APIs are documented inline.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .schemas import (
    ArticleManifest,
    BriefClaimsPlan,
    BriefEntityPlan,
    BriefFAQPlan,
    BriefLinkPlan,
    BriefOutlinePlan,
    BlueprintSection,
    FactCheckReport,
    FAQ,
    FinalArticle,
    InternalLink,
    JudgeScore,
    MarketSignal,
    MarketSignalReport,
    OptimizationPatch,
    OutlineSection,
    ResearchPacket,
    ResearchBrief,
    ScoredTopic,
    SearchIntent,
    TopicCandidate,
    WriterBlueprint,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .config import EngineConfig


# ---------------------------------------------------------------------------
# Market signal provider
# ---------------------------------------------------------------------------


class MarketSignalProvider(ABC):
    """Interface for collecting market signals.

    Real implementations could use:
    - MCP server: mcp__hn-search, mcp__reddit-search
    - Direct APIs: Hacker News Algolia API, Reddit API, Google Trends
    - RSS/Atom feed aggregators
    """

    @abstractmethod
    def collect(
        self,
        themes: list[str],
        lookback_days: int = 14,
        prompt: str | None = None,
    ) -> MarketSignalReport:
        """Collect market signals for the given themes."""
        ...


class MockMarketSignalProvider(MarketSignalProvider):
    """Returns realistic synthetic market signals for pipeline testing."""

    def collect(
        self,
        themes: list[str],
        lookback_days: int = 14,
        prompt: str | None = None,
    ) -> MarketSignalReport:
        now = datetime.now(timezone.utc)
        signals = [
            MarketSignal(
                source="hacker_news",
                title="Show HN: Open-source AI code reviewer that catches logic bugs",
                url="https://news.ycombinator.com/item?id=39012345",
                summary="Developer released an open-source tool using LLMs to detect logic errors in PRs. Strong discussion about accuracy vs. false positive rates in production codebases.",
                relevance_score=0.92,
                detected_at=now,
                themes=["ai code review", "open source", "logic bugs"],
            ),
            MarketSignal(
                source="reddit_r_experienceddevs",
                title="Our team replaced half our code review checklist with AI — here's what happened",
                url="https://reddit.com/r/ExperiencedDevs/comments/abc123",
                summary="Senior engineer describes a 6-month experiment replacing manual review checklist items with AI review. Reports 40% faster PR cycle time but notes false positives on complex domain logic.",
                relevance_score=0.88,
                detected_at=now,
                themes=["ai code review", "pr workflow", "engineering productivity"],
            ),
            MarketSignal(
                source="dev_blog",
                title="GitHub Copilot code review now GA with custom review guidelines",
                url="https://github.blog/2026-03-10-copilot-code-review-ga",
                summary="GitHub announced general availability of Copilot code review with support for team-specific review guidelines. Positions AI review as standard workflow step.",
                relevance_score=0.95,
                detected_at=now,
                themes=["ai code review", "github copilot", "code review automation"],
            ),
            MarketSignal(
                source="google_trends",
                title="Search interest in 'AI code review' up 180% YoY",
                summary="Google Trends data shows sustained growth in search interest for AI code review tools, with particular spikes after major AI model releases. Related queries include 'best AI code reviewer' and 'AI vs human code review'.",
                relevance_score=0.85,
                detected_at=now,
                themes=["search trends", "ai code review", "market growth"],
            ),
            MarketSignal(
                source="industry_report",
                title="Gartner predicts 75% of code reviews will involve AI by 2028",
                url="https://gartner.com/en/documents/ai-code-review-2026",
                summary="Analyst report projects rapid AI adoption in code review workflows, citing improved bug detection and developer satisfaction as primary drivers.",
                relevance_score=0.78,
                detected_at=now,
                themes=["industry analysis", "ai adoption", "code review trends"],
            ),
        ]
        return MarketSignalReport(
            signals=signals,
            trending_themes=[
                "AI code review adoption accelerating",
                "PR cycle time optimization",
                "AI vs. human reviewer accuracy",
                "Custom review guidelines and team learning",
                "Security vulnerability detection with AI",
            ],
            recommended_angles=[
                "How AI code review handles edge cases that manual review misses",
                "Measuring ROI of AI code review adoption after 6 months",
                "Setting up custom review guidelines for AI code reviewers",
                "AI code review for security: beyond SAST and DAST",
                "Why PR cycle time drops when you add AI review (counterintuitive)",
            ],
            collected_at=now,
        )


# ---------------------------------------------------------------------------
# Keyword data provider
# ---------------------------------------------------------------------------


class KeywordDataProvider(ABC):
    """Interface for search keyword and SERP data.

    Real implementations could use:
    - MCP server: mcp__search-console
    - Direct APIs: Google Search Console API, Ahrefs API, SEMrush API
    - Scraping adapters for PAA/featured snippets
    """

    @abstractmethod
    def get_keyword_metrics(self, keywords: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch volume, difficulty, CPC, and SERP features for keywords."""
        ...

    @abstractmethod
    def get_serp_analysis(self, keyword: str) -> dict[str, Any]:
        """Analyze current SERP for a keyword (top results, features, gaps)."""
        ...


class MockKeywordDataProvider(KeywordDataProvider):
    """Returns synthetic keyword metrics for pipeline testing."""

    MOCK_METRICS: dict[str, dict[str, Any]] = {
        "ai code review": {"volume": 2400, "difficulty": 45, "cpc": 8.50, "trend": "up"},
        "automated code review": {"volume": 1800, "difficulty": 52, "cpc": 7.20, "trend": "stable"},
        "pr cycle time": {"volume": 900, "difficulty": 35, "cpc": 4.10, "trend": "up"},
        "code quality metrics": {"volume": 1200, "difficulty": 40, "cpc": 5.30, "trend": "stable"},
        "ai pull request review": {"volume": 600, "difficulty": 30, "cpc": 6.80, "trend": "up"},
    }

    def get_keyword_metrics(self, keywords: list[str]) -> dict[str, dict[str, Any]]:
        result = {}
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower in self.MOCK_METRICS:
                result[kw] = self.MOCK_METRICS[kw_lower]
            else:
                result[kw] = {
                    "volume": 300,
                    "difficulty": 25,
                    "cpc": 2.00,
                    "trend": "unknown",
                    "note": "mock — no real data",
                }
        return result

    def get_serp_analysis(self, keyword: str) -> dict[str, Any]:
        return {
            "keyword": keyword,
            "top_results": [
                {"position": i, "url": f"https://example{i}.com/{keyword.replace(' ', '-')}"}
                for i in range(1, 6)
            ],
            "featured_snippet": True,
            "people_also_ask": [
                f"What is {keyword}?",
                f"How does {keyword} work?",
                f"Is {keyword} worth it?",
                f"Best tools for {keyword}",
            ],
            "note": "mock — no real SERP data",
        }


# ---------------------------------------------------------------------------
# Document export provider
# ---------------------------------------------------------------------------


class DocumentExportProvider(ABC):
    """Interface for external document export (Google Docs, Notion, CMS).

    Real implementations could use:
    - MCP server: mcp__google-docs, mcp__notion
    - Direct APIs: Google Docs API, Notion API, WordPress REST API
    """

    @abstractmethod
    def export(self, article: FinalArticle, metadata: dict[str, Any]) -> ExportResult:
        """Export an article to the external system."""
        ...


class ExportResult:
    """Result of an export operation."""

    def __init__(
        self,
        target: str,
        success: bool,
        message: str,
        metadata: dict[str, Any] | None = None,
    ):
        self.target = target
        self.success = success
        self.message = message
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "success": self.success,
            "message": self.message,
            "metadata": self.metadata,
        }


class MockGoogleDocsProvider(DocumentExportProvider):
    """Placeholder Google Docs export — logs intended action."""

    def export(self, article: FinalArticle, metadata: dict[str, Any]) -> ExportResult:
        logger.info(
            "Google Docs: PLACEHOLDER — would create '%s' (%d words)",
            article.title,
            article.word_count,
        )
        return ExportResult(
            target="google_docs",
            success=False,
            message=f"Placeholder: would create Google Doc '{article.title}'. Integration not connected.",
            metadata={"title": article.title, "slug": article.slug},
        )


class MockSearchConsoleProvider:
    """Placeholder Search Console adapter — returns synthetic data.

    Real implementation: Google Search Console API via OAuth2 credentials.
    MCP alternative: mcp__search-console server.
    """

    def request_indexing(self, url: str) -> ExportResult:
        logger.info("Search Console: PLACEHOLDER — would request indexing for %s", url)
        return ExportResult(
            target="search_console",
            success=False,
            message=f"Placeholder: would request indexing for {url}.",
        )


# ---------------------------------------------------------------------------
# Content generation provider
# ---------------------------------------------------------------------------


class ContentGenerationProvider(ABC):
    """Interface for LLM-backed content generation.

    Real implementations could use:
    - Anthropic SDK: claude-opus-4-6, claude-sonnet-4-6
    - MCP tool calls for structured generation
    """

    @abstractmethod
    def generate_topics(
        self, prompt: str, market_signals: MarketSignalReport
    ) -> list[TopicCandidate]:
        """Generate topic candidates from a prompt."""
        ...

    @abstractmethod
    def generate_brief(self, prompt: str, topic: ScoredTopic) -> ResearchBrief:
        """Generate a research brief for a selected topic."""
        ...

    @abstractmethod
    def generate_brief_outline(self, prompt: str, topic: ScoredTopic) -> BriefOutlinePlan:
        """Generate the outline/title specialist output for a selected topic."""
        ...

    @abstractmethod
    def generate_brief_entities(self, prompt: str, topic: ScoredTopic) -> BriefEntityPlan:
        """Generate the keyword/entity specialist output for a selected topic."""
        ...

    @abstractmethod
    def generate_brief_faqs(self, prompt: str, topic: ScoredTopic) -> BriefFAQPlan:
        """Generate the FAQ specialist output for a selected topic."""
        ...

    @abstractmethod
    def generate_brief_links(self, prompt: str, topic: ScoredTopic) -> BriefLinkPlan:
        """Generate the internal-link/CTA specialist output for a selected topic."""
        ...

    @abstractmethod
    def generate_brief_claims(self, prompt: str, topic: ScoredTopic) -> BriefClaimsPlan:
        """Generate the evidence and risk specialist output for a selected topic."""
        ...

    @abstractmethod
    def generate_brief_bundle(
        self,
        prompt: str,
        topic: ScoredTopic,
        research_packet: ResearchPacket,
    ) -> ResearchBrief:
        """Generate the full brief in one bundled call."""
        ...

    @abstractmethod
    def generate_writer_blueprint(
        self,
        prompt: str,
        brief: ResearchBrief,
        research_packet: ResearchPacket,
        writer_id: str,
        writer_label: str,
    ) -> WriterBlueprint:
        """Generate a low-token article blueprint for one writer persona."""
        ...

    @abstractmethod
    def generate_draft_from_blueprint(
        self,
        prompt: str,
        brief: ResearchBrief,
        blueprint: WriterBlueprint,
        research_packet: ResearchPacket,
    ) -> str:
        """Generate a full draft using a selected blueprint."""
        ...

    @abstractmethod
    def generate_draft(self, prompt: str, brief: ResearchBrief) -> str:
        """Generate a draft article from a brief. Returns markdown."""
        ...

    @abstractmethod
    def optimize_draft(self, prompt: str, draft: str) -> str:
        """SEO/AEO optimize a draft. Returns improved markdown."""
        ...

    @abstractmethod
    def optimize_sections(
        self,
        prompt: str,
        content: str,
        manifest: ArticleManifest,
    ) -> OptimizationPatch:
        """Return a structured optimization patch instead of rewriting the full article."""
        ...

    @abstractmethod
    def judge_topic(self, prompt: str, topic: ScoredTopic, judge_name: str) -> JudgeScore:
        """Score a topic candidate from the perspective of one judge."""
        ...

    @abstractmethod
    def judge_article(self, prompt: str, content: str, judge_name: str) -> JudgeScore:
        """Score a draft or final article from the perspective of one judge."""
        ...

    @abstractmethod
    def fact_check_claims(
        self,
        prompt: str,
        manifest: ArticleManifest,
    ) -> FactCheckReport:
        """Run a final fact-check over the compact article manifest."""
        ...


class MockContentGenerationProvider(ContentGenerationProvider):
    """Returns pre-built mock content for pipeline testing.

    The mock data is realistic and domain-specific so the full pipeline
    can be exercised without LLM API calls.
    """

    def generate_topics(
        self, prompt: str, market_signals: MarketSignalReport
    ) -> list[TopicCandidate]:
        """Return 18 pre-built topic candidates across clusters."""
        return _mock_topic_candidates()

    def generate_brief(self, prompt: str, topic: ScoredTopic) -> ResearchBrief:
        """Return a complete mock research brief."""
        return _mock_research_brief(topic.candidate)

    def generate_brief_outline(self, prompt: str, topic: ScoredTopic) -> BriefOutlinePlan:
        brief = _mock_research_brief(topic.candidate)
        return BriefOutlinePlan(
            outline=brief.outline,
            target_word_count=brief.target_word_count,
            title_options=brief.title_options,
        )

    def generate_brief_entities(self, prompt: str, topic: ScoredTopic) -> BriefEntityPlan:
        brief = _mock_research_brief(topic.candidate)
        return BriefEntityPlan(
            primary_keyword=brief.primary_keyword,
            secondary_keywords=brief.secondary_keywords,
            entities=brief.entities,
            meta_description=brief.meta_description,
        )

    def generate_brief_faqs(self, prompt: str, topic: ScoredTopic) -> BriefFAQPlan:
        brief = _mock_research_brief(topic.candidate)
        return BriefFAQPlan(faqs=brief.faqs)

    def generate_brief_links(self, prompt: str, topic: ScoredTopic) -> BriefLinkPlan:
        brief = _mock_research_brief(topic.candidate)
        return BriefLinkPlan(
            internal_link_suggestions=brief.internal_link_suggestions,
            cta=brief.cta,
        )

    def generate_brief_claims(self, prompt: str, topic: ScoredTopic) -> BriefClaimsPlan:
        brief = _mock_research_brief(topic.candidate)
        return BriefClaimsPlan(
            claims_needing_evidence=brief.claims_needing_evidence,
            do_not_say=brief.do_not_say,
        )

    def generate_brief_bundle(
        self,
        prompt: str,
        topic: ScoredTopic,
        research_packet: ResearchPacket,
    ) -> ResearchBrief:
        return _mock_research_brief(topic.candidate)

    def generate_writer_blueprint(
        self,
        prompt: str,
        brief: ResearchBrief,
        research_packet: ResearchPacket,
        writer_id: str,
        writer_label: str,
    ) -> WriterBlueprint:
        return WriterBlueprint(
            writer_id=writer_id,
            writer_label=writer_label,
            focus_summary=f"Mock blueprint for {writer_label}",
            title=brief.title_options[0],
            opening_hook=f"{brief.primary_keyword} matters because review teams need repeatable, technical signals before rollout.",
            direct_answer=f"Direct answer: {brief.primary_keyword} works when teams benchmark it on real pull requests, integrate it into CI, and keep humans on high-risk paths.",
            sections=[
                BlueprintSection(
                    heading=section.heading,
                    bullets=section.key_points or [section.description],
                    claims_to_support=brief.claims_needing_evidence[:2],
                )
                for section in brief.outline
            ],
            faq_plan=[faq.question for faq in brief.faqs],
            internal_link_targets=[link.target_path for link in brief.internal_link_suggestions],
            claims_plan=brief.claims_needing_evidence,
            estimated_word_count=brief.target_word_count,
        )

    def generate_draft_from_blueprint(
        self,
        prompt: str,
        brief: ResearchBrief,
        blueprint: WriterBlueprint,
        research_packet: ResearchPacket,
    ) -> str:
        return _mock_article_content(brief)

    def generate_draft(self, prompt: str, brief: ResearchBrief) -> str:
        """Return a full mock article as markdown."""
        return _mock_article_content(brief)

    def optimize_draft(self, prompt: str, draft: str) -> str:
        """Apply lightweight cleanup so the optimizer stage does real work."""
        optimized = re.sub(r"\n{3,}", "\n\n", draft).strip()
        optimized = optimized.replace("## FAQ", "## Frequently Asked Questions")
        if "## Frequently Asked Questions" not in optimized and "### " in optimized:
            optimized += "\n\n## Frequently Asked Questions\n"
        return optimized + "\n"

    def optimize_sections(
        self,
        prompt: str,
        content: str,
        manifest: ArticleManifest,
    ) -> OptimizationPatch:
        return OptimizationPatch(
            opening_direct_answer=manifest.opening_direct_answer or None,
            internal_link_suggestions=[],
            section_rewrites=[],
            faq_questions_to_strengthen=manifest.faq_questions[:2],
            notes=["Mock coordinator produced no-op patch."],
        )

    def judge_topic(self, prompt: str, topic: ScoredTopic, judge_name: str) -> JudgeScore:
        score_map = {
            "seo_opportunity_judge": 8.4,
            "technical_authority_judge": 8.9,
            "freshness_relevance_judge": 8.2 if topic.candidate.freshness_signal else 7.3,
            "commercial_value_judge": 8.0 if topic.candidate.search_intent.value in {"commercial", "transactional"} else 7.1,
            "originality_judge": 8.5 if not topic.rejection_reasons else 6.6,
        }
        return JudgeScore(
            judge=judge_name,
            score=score_map.get(judge_name, 7.5),
            rationale=f"Mock {judge_name} assessment for {topic.candidate.title}.",
            notes=[],
        )

    def judge_article(self, prompt: str, content: str, judge_name: str) -> JudgeScore:
        technical_markers = sum(
            1 for marker in ("pull request", "benchmark", "sast", "dast", "lint", "test")
            if marker in content.lower()
        )
        base = {
            "search_readiness_judge": 9.0,
            "structure_clarity_judge": 9.1,
            "technical_rigor_judge": 8.7 + min(1.0, technical_markers * 0.08),
            "full_text_arbiter": 8.9,
            "technical_accuracy_judge": 8.4 + min(0.7, technical_markers * 0.07),
        }.get(judge_name, 8.2)
        return JudgeScore(
            judge=judge_name,
            score=round(min(base, 10.0), 2),
            rationale=f"Mock {judge_name} review of article quality.",
            notes=[],
        )

    def fact_check_claims(
        self,
        prompt: str,
        manifest: ArticleManifest,
    ) -> FactCheckReport:
        return FactCheckReport(
            checked_claims=manifest.claim_candidates[:5],
            verified_claims=manifest.claim_candidates[:5],
            flagged_claims=[],
            required_revisions=[],
            notes=["Mock fact check passed."],
            passed=True,
        )


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """Central registry for swapping providers without touching business logic.

    Usage:
        registry = ProviderRegistry()  # all mocks
        registry = ProviderRegistry(
            market_signals=RealHackerNewsProvider(),
            keyword_data=AhrefsKeywordProvider(api_key="..."),
        )
    """

    def __init__(
        self,
        market_signals: MarketSignalProvider | None = None,
        keyword_data: KeywordDataProvider | None = None,
        document_export: DocumentExportProvider | None = None,
        content_generation: ContentGenerationProvider | None = None,
    ):
        self.market_signals = market_signals or MockMarketSignalProvider()
        self.keyword_data = keyword_data or MockKeywordDataProvider()
        self.document_export = document_export or MockGoogleDocsProvider()
        self.content_generation = content_generation or MockContentGenerationProvider()

    def drain_usage_records(self) -> list[Any]:
        """Drain token-usage records from providers that support it."""
        records: list[Any] = []
        for provider in (self.market_signals, self.keyword_data, self.content_generation):
            drain = getattr(provider, "drain_usage_records", None)
            if callable(drain):
                records.extend(drain())
        return records


def build_provider_registry(config: EngineConfig) -> ProviderRegistry:
    """Build the provider registry from runtime configuration."""
    if config.dry_run or config.provider_mode == "mock":
        return ProviderRegistry()

    if config.provider_mode != "openai":
        raise ValueError(
            f"Unsupported provider mode '{config.provider_mode}'. Expected 'mock' or 'openai'."
        )

    if not config.openai_api_key:
        raise ValueError(
            "OPENAI_API_KEY is required when SEO_ENGINE_PROVIDER=openai."
        )

    from .openai_providers import (
        OpenAIContentGenerationProvider,
        OpenAIKeywordDataProvider,
        OpenAIMarketSignalProvider,
    )

    return ProviderRegistry(
        market_signals=OpenAIMarketSignalProvider(config),
        keyword_data=OpenAIKeywordDataProvider(config),
        content_generation=OpenAIContentGenerationProvider(config),
    )


# ---------------------------------------------------------------------------
# Mock data factories
# ---------------------------------------------------------------------------


def _mock_topic_candidates() -> list[TopicCandidate]:
    """Generate 18 realistic topic candidates across clusters."""
    return [
        TopicCandidate(
            title="How AI Code Review Catches Bugs That Linters Miss",
            slug="ai-code-review-catches-bugs-linters-miss",
            cluster="ai-code-review",
            description="Deep dive into the specific categories of bugs — logic errors, race conditions, incomplete error handling — that AI review detects but rule-based linters cannot. Includes real code examples.",
            target_keywords=["ai code review", "linters vs ai", "logic bugs detection"],
            search_intent=SearchIntent.INFORMATIONAL,
            freshness_signal="GitHub Copilot code review GA announcement March 2026",
            source="market-watcher + competitor-gap",
            rationale="High search volume keyword with clear content gap. No competitor has a definitive piece comparing AI review to linters with concrete bug examples.",
        ),
        TopicCandidate(
            title="Measuring ROI of AI Code Review After 6 Months",
            slug="measuring-roi-ai-code-review",
            cluster="engineering-productivity",
            description="Framework for measuring the return on investment of AI code review tools, including metrics to track, baseline establishment, and real team case studies.",
            target_keywords=["ai code review roi", "code review metrics", "developer productivity measurement"],
            search_intent=SearchIntent.COMMERCIAL,
            freshness_signal="Growing enterprise adoption driving ROI questions",
            source="market-watcher",
            rationale="Commercial intent keyword — buyers researching tools want ROI frameworks. Strong AEO potential for 'is AI code review worth it' queries.",
        ),
        TopicCandidate(
            title="Custom Review Guidelines: Teaching AI Your Team's Standards",
            slug="custom-review-guidelines-ai-code-review",
            cluster="ai-code-review",
            description="How to configure AI code reviewers with team-specific rules, coding conventions, and architectural patterns so reviews match your team's expectations.",
            target_keywords=["custom code review rules", "ai review configuration", "team coding standards"],
            search_intent=SearchIntent.INFORMATIONAL,
            freshness_signal="GitHub Copilot custom guidelines feature launch",
            source="market-watcher",
            rationale="Timely angle tied to Copilot GA. Teams evaluating AI review want to know about customization. Macroscope's team learning is a key differentiator.",
        ),
        TopicCandidate(
            title="AI Code Review for Security: Beyond SAST and DAST",
            slug="ai-code-review-security-beyond-sast-dast",
            cluster="security-in-review",
            description="How AI code review complements traditional SAST/DAST tools by understanding context, detecting business logic vulnerabilities, and identifying insecure patterns that static rules miss.",
            target_keywords=["ai security code review", "sast vs ai review", "code security automation"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="Security is a top concern. SAST/DAST comparison is high-intent and under-served. Macroscope can credibly position as complementary layer.",
        ),
        TopicCandidate(
            title="Why PR Cycle Time Drops When You Add AI Review",
            slug="pr-cycle-time-drops-with-ai-review",
            cluster="pr-workflows",
            description="Counterintuitive finding: adding another review step (AI) actually speeds up the overall PR cycle. Explains the mechanism — faster first-pass feedback, fewer review rounds, reduced reviewer cognitive load.",
            target_keywords=["pr cycle time", "ai code review speed", "faster pull requests"],
            search_intent=SearchIntent.INFORMATIONAL,
            freshness_signal="Reddit thread on team reducing PR time by 40% with AI",
            source="market-watcher",
            rationale="Counterintuitive angle drives engagement. Directly addresses buyer objection ('won't AI review slow us down?'). Strong data storytelling potential.",
        ),
        TopicCandidate(
            title="The Complete Guide to Code Review Automation in 2026",
            slug="code-review-automation-guide-2026",
            cluster="ai-code-review",
            description="Comprehensive guide covering the full spectrum of code review automation: from linters and formatters through SAST tools to AI-powered semantic review. Helps teams build their automation stack.",
            target_keywords=["code review automation", "automated code review tools", "code review pipeline"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="Pillar content opportunity. High volume keyword, strong AEO fit for 'how to automate code review' queries. Positions Macroscope within the ecosystem.",
        ),
        TopicCandidate(
            title="How to Evaluate AI Code Review Tools: A Buyer's Framework",
            slug="evaluate-ai-code-review-tools-framework",
            cluster="ai-code-review",
            description="Structured evaluation framework for engineering leaders comparing AI code review tools. Covers accuracy, false positive rates, integration depth, customization, privacy, and total cost.",
            target_keywords=["best ai code review tools", "ai code review comparison", "evaluate code review tools"],
            search_intent=SearchIntent.COMMERCIAL,
            source="competitor-gap",
            rationale="High commercial intent. Decision-makers actively searching. No competitor has a comprehensive, unbiased-feeling framework piece.",
        ),
        TopicCandidate(
            title="DORA Metrics and AI Code Review: Measuring What Matters",
            slug="dora-metrics-ai-code-review",
            cluster="engineering-productivity",
            description="How AI code review impacts the four DORA metrics — deployment frequency, lead time, change failure rate, and MTTR. Maps AI review improvements to metrics engineering leaders already track.",
            target_keywords=["dora metrics code review", "engineering productivity metrics", "ai impact dora"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="DORA metrics are how eng leaders justify tooling investments. Connecting AI review to DORA creates a compelling business case narrative.",
        ),
        TopicCandidate(
            title="Reducing False Positives in AI Code Review: Practical Techniques",
            slug="reducing-false-positives-ai-code-review",
            cluster="ai-code-review",
            description="Practical strategies for tuning AI code review to reduce noise: configuring severity thresholds, training on team patterns, suppression rules, and feedback loops.",
            target_keywords=["ai code review false positives", "code review noise reduction", "ai review accuracy"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="reddit-discussion",
            rationale="Top concern for teams adopting AI review. Reddit threads show frustration with false positives. Practical advice positions Macroscope as solution-oriented.",
        ),
        TopicCandidate(
            title="Code Review Bottlenecks: How to Identify and Fix Them",
            slug="code-review-bottlenecks-identify-fix",
            cluster="pr-workflows",
            description="Diagnostic guide for identifying where code review slows down — reviewer availability, large PRs, unclear standards, context switching — and specific fixes for each bottleneck type.",
            target_keywords=["code review bottlenecks", "slow code review", "pr review delays"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="High search volume problem-aware keyword. Every team experiences review bottlenecks. Natural segue to AI review as a solution.",
        ),
        TopicCandidate(
            title="Building a Code Review Culture That Scales",
            slug="code-review-culture-that-scales",
            cluster="engineering-productivity",
            description="How engineering teams maintain review quality as they grow from 5 to 50+ developers. Covers reviewer rotation, review SLAs, async review practices, and AI augmentation.",
            target_keywords=["code review culture", "scaling engineering team", "review best practices"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="Evergreen topic with broad appeal. Scaling challenges resonate with growing teams — Macroscope's target market.",
        ),
        TopicCandidate(
            title="AI Code Review in Monorepos: Challenges and Solutions",
            slug="ai-code-review-monorepos",
            cluster="ai-code-review",
            description="Specific challenges of running AI code review in monorepo environments — context window limits, cross-package impact analysis, ownership routing — and how to solve them.",
            target_keywords=["monorepo code review", "ai review monorepo", "large codebase review"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="Niche but high-value audience. Monorepo teams are often large engineering orgs with budget. Specific angle no competitor covers.",
        ),
        TopicCandidate(
            title="From PR Comments to Production: The AI Review Feedback Loop",
            slug="ai-review-feedback-loop-production",
            cluster="ai-code-review",
            description="How AI code review creates a continuous improvement loop: review findings inform production monitoring, production incidents improve review rules, and team patterns evolve.",
            target_keywords=["code review feedback loop", "continuous improvement code quality", "ai review learning"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="Unique angle showing AI review as part of a system, not just a tool. Differentiates Macroscope's learning capability.",
        ),
        TopicCandidate(
            title="What Senior Engineers Actually Want From AI Code Review",
            slug="what-senior-engineers-want-ai-code-review",
            cluster="ai-code-review",
            description="Based on patterns from engineering communities: senior engineers want AI review that understands architecture, catches non-obvious bugs, and doesn't waste time on style nitpicks.",
            target_keywords=["senior engineer code review", "ai code review expectations", "developer experience ai"],
            search_intent=SearchIntent.INFORMATIONAL,
            freshness_signal="Active HN thread on AI review frustrations",
            source="market-watcher + topic-researcher",
            rationale="Persona-targeted content with strong engagement potential. Addresses the skeptic audience directly.",
        ),
        TopicCandidate(
            title="CI/CD Pipeline Integration Patterns for AI Code Review",
            slug="ci-cd-integration-ai-code-review",
            cluster="devops-ci-cd",
            description="Technical guide to integrating AI code review into existing CI/CD pipelines — GitHub Actions, GitLab CI, Jenkins, Buildkite. Covers triggering, result reporting, and blocking strategies.",
            target_keywords=["ai code review ci cd", "github actions code review", "automated review pipeline"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="Technical audience with clear implementation intent. Multiple long-tail keywords around specific CI systems.",
        ),
        TopicCandidate(
            title="The Hidden Cost of Skipping Code Review",
            slug="hidden-cost-skipping-code-review",
            cluster="code-quality",
            description="Quantifies what happens when teams skip or rush code review: increased defect rates, knowledge silos, onboarding friction, and technical debt accumulation. Uses industry data.",
            target_keywords=["cost of skipping code review", "code review importance", "technical debt code review"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="Problem-awareness content that drives urgency. Good for top-of-funnel traffic. Strong data storytelling with industry statistics.",
        ),
        TopicCandidate(
            title="How to Write PRs That AI Reviewers Love",
            slug="write-prs-ai-reviewers-love",
            cluster="pr-workflows",
            description="Best practices for structuring pull requests to get the most value from AI code review: PR size, commit organization, description quality, and context-setting.",
            target_keywords=["write better pull requests", "pr best practices", "ai friendly prs"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="topic-researcher",
            rationale="Practical, actionable content developers will bookmark. Drives awareness that PR structure affects AI review quality.",
        ),
        TopicCandidate(
            title="Security Code Review Checklist: Manual + AI Combined Approach",
            slug="security-code-review-checklist-ai",
            cluster="security-in-review",
            description="Comprehensive checklist combining manual security review steps with AI-augmented checks. Covers OWASP Top 10, authentication, authorization, data handling, and dependency risks.",
            target_keywords=["security code review checklist", "secure code review", "owasp code review"],
            search_intent=SearchIntent.INFORMATIONAL,
            source="competitor-gap",
            rationale="High-value resource content. Security checklists attract links and bookmarks. Combining manual + AI is a unique angle.",
        ),
    ]


COMMON_DO_NOT_SAY = [
    "revolutionary",
    "game-changing",
    "industry-leading",
    "best in class",
    "replaces human code reviewers",
    "eliminates all security vulnerabilities",
    "guaranteed to reduce bugs",
    "10x developer productivity",
    "cutting-edge",
    "state-of-the-art",
]


def _mock_research_brief(topic: TopicCandidate) -> ResearchBrief:
    """Build a topic-aware research brief for the given topic."""
    primary_keyword = _pick_primary_keyword(topic)
    secondary_keywords = _secondary_keywords(topic)
    return ResearchBrief(
        topic=topic,
        outline=_build_outline(topic, primary_keyword),
        target_word_count=1900,
        primary_keyword=primary_keyword,
        secondary_keywords=secondary_keywords,
        entities=_cluster_entities(topic.cluster),
        faqs=_build_faqs(topic, primary_keyword),
        claims_needing_evidence=_claims_needing_evidence(topic),
        internal_link_suggestions=_internal_links_for_cluster(topic.cluster),
        cta=_cta_for_topic(topic),
        do_not_say=[*COMMON_DO_NOT_SAY, "only tool worth evaluating"],
        meta_description=_meta_description(topic, primary_keyword),
        title_options=_title_options(topic, primary_keyword),
    )


def _mock_article_content(brief: ResearchBrief) -> str:
    """Return a topic-aware mock article that passes the SEO/AEO checks."""
    topic = brief.topic
    primary_keyword = brief.primary_keyword
    display_keyword = _display_keyword(primary_keyword)
    title = brief.title_options[0]
    freshness_sentence = (
        f" In 2026, teams are revisiting this topic because {topic.freshness_signal.lower()}."
        if topic.freshness_signal
        else ""
    )

    intro = [
        f"# {title}",
        "",
        (
            f"{display_keyword} helps engineering teams catch the issues that slow reviews, create rework, "
            f"and escape into production.{freshness_sentence} This matters for {topic.cluster.replace('-', ' ')} "
            "because you need faster feedback without lowering code quality."
        ),
        "",
        (
            f"If you are evaluating {topic.title.lower()}, the key decision is not whether to automate more. "
            f"It is how to add signal without adding noise. {brief.topic.description}"
        ),
        "",
    ]

    sections: list[str] = ["\n".join(intro).strip()]
    internal_links = brief.internal_link_suggestions
    link_index = 0

    for outline in brief.outline:
        if outline.heading == "Frequently Asked Questions":
            sections.append(_faq_section(brief))
            continue
        if outline.heading == "Conclusion":
            sections.append(_conclusion_section(brief))
            continue
        section = _render_outline_section(
            outline=outline,
            brief=brief,
            link=internal_links[link_index] if link_index < len(internal_links) else None,
        )
        if link_index < len(internal_links):
            link_index += 1
        sections.append(section)

    article = "\n\n".join(sections).strip() + "\n"
    return article


def _secondary_keywords(topic: TopicCandidate) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    cluster_defaults = {
        "ai-code-review": ["automated code review", "ai pull request review", "code review automation"],
        "pr-workflows": ["pull request workflow", "pr cycle time", "faster pull requests"],
        "engineering-productivity": ["engineering metrics", "developer productivity", "dora metrics"],
        "security-in-review": ["secure code review", "code security automation", "owasp code review"],
        "devops-ci-cd": ["ci cd pipeline", "github actions code review", "automated review pipeline"],
        "code-quality": ["code quality tools", "technical debt", "code maintainability"],
    }
    for keyword in [*topic.target_keywords, *cluster_defaults.get(topic.cluster, [])]:
        normalized = keyword.lower().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            keywords.append(normalized)
    return keywords[:6]


def _pick_primary_keyword(topic: TopicCandidate) -> str:
    def score(keyword: str) -> float:
        lowered = keyword.lower().strip()
        word_count = len(lowered.split())
        value = 0.0
        if "ai code review" in lowered:
            value += 4.0
        elif "code review" in lowered:
            value += 2.5
        if 2 <= word_count <= 4:
            value += 2.0
        elif word_count == 1:
            value += 0.5
        else:
            value += 1.0
        if any(token in lowered for token in ("comparison", "evaluate", "framework", "expectations")):
            value += 1.5
        if any(token in lowered for token in ("best ", "speed")):
            value -= 1.5
        return value

    best = max(topic.target_keywords, key=score)
    return best.lower().strip()


def _cluster_entities(cluster: str) -> list[str]:
    entities = {
        "ai-code-review": ["Macroscope", "GitHub", "GitLab", "GitHub Copilot", "ESLint", "SonarQube"],
        "pr-workflows": ["Macroscope", "GitHub", "GitLab", "Pull Requests", "DORA metrics", "Slack"],
        "engineering-productivity": ["Macroscope", "DORA metrics", "GitHub", "GitLab", "Engineering managers"],
        "security-in-review": ["Macroscope", "OWASP", "SAST", "DAST", "GitHub", "GitLab"],
        "devops-ci-cd": ["Macroscope", "GitHub Actions", "GitLab CI", "Jenkins", "Buildkite", "Pull Requests"],
        "code-quality": ["Macroscope", "Linters", "Static analysis", "Code review", "Technical debt"],
    }
    return entities.get(cluster, ["Macroscope", "GitHub", "GitLab", "Code review", "Engineering teams"])


def _build_outline(topic: TopicCandidate, primary_keyword: str) -> list[OutlineSection]:
    display_keyword = _display_keyword(primary_keyword)
    solution_heading = {
        "ai-code-review": f"How {display_keyword} improves review quality",
        "pr-workflows": f"How {display_keyword} improves pull request flow",
        "engineering-productivity": f"How {display_keyword} improves engineering productivity",
        "security-in-review": f"How {display_keyword} improves secure code review",
        "devops-ci-cd": f"How {display_keyword} fits into CI/CD",
        "code-quality": f"How {display_keyword} improves code quality",
    }.get(topic.cluster, f"How {display_keyword} helps engineering teams")

    return [
        OutlineSection(
            heading=f"Why {display_keyword} matters now",
            description=f"Explain the problem behind {topic.title.lower()} and why teams are prioritizing it right now.",
            target_word_count=260,
            key_points=[
                topic.description,
                f"Why this matters to teams focused on {topic.cluster.replace('-', ' ')}",
                "The operational cost of waiting too long to fix the issue",
            ],
        ),
        OutlineSection(
            heading=solution_heading,
            description="Describe the mechanism, workflow, and practical leverage points.",
            target_word_count=320,
            key_points=[
                f"Where {primary_keyword} adds signal in the workflow",
                "What changes for reviewers, authors, and engineering leaders",
                "How to keep findings actionable instead of noisy",
            ],
        ),
        OutlineSection(
            heading="What strong teams do differently",
            description="Translate the angle into practical operating habits and implementation patterns.",
            target_word_count=280,
            key_points=[
                "How mature teams scope rollout and define guardrails",
                "How they write review guidelines and measure drift",
                "How they decide what stays blocking vs advisory",
            ],
        ),
        OutlineSection(
            heading="Implementation example",
            description="Use a concrete example to show how the process works in practice.",
            target_word_count=260,
            key_points=[
                "One realistic code or workflow example",
                "The bug, risk, or bottleneck that gets caught earlier",
                "What the team changes after seeing the result",
            ],
        ),
        OutlineSection(
            heading="How to measure whether it is working",
            description="Cover the metrics and feedback loops teams should use.",
            target_word_count=220,
            key_points=[
                "Leading indicators before rollout",
                "Quality and cycle-time metrics after rollout",
                "How to avoid vanity metrics",
            ],
        ),
        OutlineSection(
            heading="Frequently Asked Questions",
            description="AEO-oriented FAQs with concise answers.",
            target_word_count=280,
            key_points=["5 concise answers"],
        ),
        OutlineSection(
            heading="Conclusion",
            description="Tight summary and CTA.",
            target_word_count=150,
            key_points=["Summary", "Recommended next step", "CTA"],
        ),
    ]


def _build_faqs(topic: TopicCandidate, primary_keyword: str) -> list[FAQ]:
    display_keyword = _display_keyword(primary_keyword)
    specific_question = {
        "ai-code-review": f"What does {display_keyword} catch that linters miss?",
        "pr-workflows": f"How does {display_keyword} affect pull request cycle time?",
        "engineering-productivity": f"How should teams measure the impact of {display_keyword}?",
        "security-in-review": f"How does {display_keyword} complement SAST and DAST?",
        "devops-ci-cd": f"Where should {display_keyword} run in a CI/CD pipeline?",
        "code-quality": f"How does {display_keyword} improve code quality without adding churn?",
    }.get(topic.cluster, f"When should a team adopt {display_keyword}?")

    return [
        FAQ(
            question=f"What is {display_keyword}?",
            suggested_answer=(
                f"{display_keyword} is the practice of using context-aware automation to improve review quality, "
                "speed up feedback, and surface issues before they reach production. The value comes from better decision support, "
                "not from replacing human judgment."
            ),
        ),
        FAQ(
            question=specific_question,
            suggested_answer=(
                f"{display_keyword} works best when it handles the repetitive analysis humans skip under time pressure, "
                "then escalates the findings that need architectural or business judgment. That gives teams faster reviews without "
                "turning every pull request into a wall of low-signal comments."
            ),
        ),
        FAQ(
            question=f"Does {display_keyword} replace human reviewers?",
            suggested_answer=(
                "No. Human reviewers still own architecture, trade-offs, and product context. Good automation removes routine review work, "
                "catches issues earlier, and gives senior engineers more time to focus on the changes that actually need deep judgment."
            ),
        ),
        FAQ(
            question=f"How should teams roll out {display_keyword}?",
            suggested_answer=(
                "Start with a narrow rollout, define what should be advisory versus blocking, and review findings weekly. Teams get better results "
                "when they tune prompts, severity thresholds, and review guidelines before scaling usage across every repository."
            ),
        ),
        FAQ(
            question=f"How do you measure whether {display_keyword} is working?",
            suggested_answer=(
                "Track review turnaround time, production defects, rework after merge, and whether reviewers spend less time on mechanical checks. "
                "The best signal is a combination of faster cycle time and better issue discovery, not one metric in isolation."
            ),
        ),
    ]


def _claims_needing_evidence(topic: TopicCandidate) -> list[str]:
    return [
        f"Performance gains claimed for {topic.title.lower()}",
        "Changes to production defect rates after rollout",
        "Any percentage improvement in review time or throughput",
    ]


def _internal_links_for_cluster(cluster: str) -> list[InternalLink]:
    links = {
        "ai-code-review": [
            ("AI code review best practices", "/blog/ai-code-review-best-practices"),
            ("static analysis vs AI review", "/blog/static-analysis-vs-ai-review"),
            ("reduce PR cycle time", "/blog/reducing-pr-cycle-time"),
            ("engineering productivity metrics", "/blog/engineering-productivity-metrics"),
        ],
        "pr-workflows": [
            ("reduce PR cycle time", "/blog/reducing-pr-cycle-time"),
            ("AI code review best practices", "/blog/ai-code-review-best-practices"),
            ("engineering productivity metrics", "/blog/engineering-productivity-metrics"),
            ("static analysis vs AI review", "/blog/static-analysis-vs-ai-review"),
        ],
        "engineering-productivity": [
            ("engineering productivity metrics", "/blog/engineering-productivity-metrics"),
            ("reduce PR cycle time", "/blog/reducing-pr-cycle-time"),
            ("AI code review best practices", "/blog/ai-code-review-best-practices"),
            ("static analysis vs AI review", "/blog/static-analysis-vs-ai-review"),
        ],
        "security-in-review": [
            ("static analysis vs AI review", "/blog/static-analysis-vs-ai-review"),
            ("AI code review best practices", "/blog/ai-code-review-best-practices"),
            ("reduce PR cycle time", "/blog/reducing-pr-cycle-time"),
            ("engineering productivity metrics", "/blog/engineering-productivity-metrics"),
        ],
    }
    selected = links.get(cluster, links["ai-code-review"])
    return [
        InternalLink(anchor_text=anchor, target_path=path, context=f"Use when discussing {anchor}")
        for anchor, path in selected
    ]


def _meta_description(topic: TopicCandidate, primary_keyword: str) -> str:
    summary = (
        f"Learn how {_display_keyword(primary_keyword)} helps teams with {topic.title.lower()}. "
        "Get practical guidance, implementation patterns, and metrics that matter."
    )
    return _fit_length(summary, 155)


def _title_options(topic: TopicCandidate, primary_keyword: str) -> list[str]:
    display_keyword = _display_keyword(primary_keyword)
    options = [
        topic.title,
        _fit_length(f"{display_keyword}: {topic.title}", 58),
        _fit_length(f"{display_keyword} for {topic.cluster.replace('-', ' ').title()}", 56),
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for option in options:
        normalized = option.strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            deduped.append(normalized)
    return deduped[:3]


def _cta_for_topic(topic: TopicCandidate) -> str:
    if topic.cluster == "security-in-review":
        return "See how Macroscope helps your team catch risky changes before they reach production."
    if topic.cluster == "pr-workflows":
        return "See how Macroscope helps your team shorten PR cycles without lowering review quality."
    return "See how Macroscope helps your team catch issues earlier and keep pull requests moving."


def _render_outline_section(
    outline: OutlineSection,
    brief: ResearchBrief,
    link: InternalLink | None,
) -> str:
    primary_keyword = brief.primary_keyword
    display_keyword = _display_keyword(primary_keyword)
    lead, support = _section_paragraphs(outline.heading, brief)
    body = [
        f"## {outline.heading}",
        "",
        lead,
        "",
        support,
        "",
        (
            f"That matters because {display_keyword} should change behavior in the review loop, not just create more commentary. "
            "The strongest implementations make expectations explicit, connect findings to ownership, and leave a clear paper trail "
            "for what the team will tune next."
        ),
        "",
    ]

    if any("example" in point.lower() for point in outline.key_points):
        body.extend(
            [
                "```python",
                "def merge_ready(pr):",
                "    if pr.tests_failed:",
                "        return False",
                "    if pr.approvals >= 2 and not pr.has_blocking_findings:",
                "        return True",
                "```",
                "",
                (
                    "In a real workflow, the issue is rarely syntax. It is whether the rules capture the cases your team "
                    "actually cares about. That is where context-aware review earns its place."
                ),
                "",
            ]
        )

    bullets = "\n".join(f"- {point}" for point in outline.key_points)
    body.extend([bullets, ""])

    if link is not None:
        body.extend(
            [
                (
                    f"For a related example, see [{link.anchor_text}]({link.target_path}). It is a useful companion when "
                    f"you are building a rollout plan around {display_keyword}."
                ),
                "",
            ]
        )

    body.append(
        f"The teams that get the best results from {display_keyword} review findings weekly, "
        "remove recurring noise, and treat the output as part of the pull request system instead "
        "of a separate reporting stream."
    )
    return "\n".join(body).strip()


def _faq_section(brief: ResearchBrief) -> str:
    lines = ["## Frequently Asked Questions", ""]
    for faq in brief.faqs:
        lines.extend([f"### {faq.question}", "", faq.suggested_answer, ""])
    return "\n".join(lines).strip()


def _conclusion_section(brief: ResearchBrief) -> str:
    display_keyword = _display_keyword(brief.primary_keyword)
    return "\n".join(
        [
            "## Conclusion",
            "",
            (
                f"{display_keyword} works when it improves the decisions your team already makes during review. "
                "The goal is not to automate everything. The goal is to surface high-signal issues earlier, keep reviewers focused, "
                "and give engineering leaders a cleaner feedback loop."
            ),
            "",
            brief.cta,
        ]
    ).strip()


def _fit_length(text: str, max_len: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_len:
        return cleaned
    shortened = cleaned[: max_len - 3].rsplit(" ", 1)[0].rstrip(" ,:;.-")
    return f"{shortened}..."


def _section_paragraphs(heading: str, brief: ResearchBrief) -> tuple[str, str]:
    topic = brief.topic
    primary_keyword = brief.primary_keyword
    display_keyword = _display_keyword(primary_keyword)
    secondary_keyword = (
        brief.secondary_keywords[1]
        if len(brief.secondary_keywords) > 1
        else brief.secondary_keywords[0]
    )

    if heading.startswith("Why "):
        return (
            f"{topic.title} matters now because senior reviewers want AI help on the issues that actually burn time: "
            f"architecture drift, unclear ownership, and defects that survive a quick skim. {display_keyword} becomes "
            "valuable when it narrows attention to those decisions instead of repeating what lint and tests already say.",
            f"{topic.rationale} That is why strong teams tie rollout to a clear operating problem, such as slower pull requests, "
            f"noisy review queues, or weak signal around {secondary_keyword}, instead of adopting it as a generic automation layer.",
        )

    if "improves" in heading:
        return (
            f"{display_keyword} improves review quality when it understands the change in context and explains why a finding matters. "
            "Senior engineers do not want more comments. They want fewer, sharper comments that point to design risk, missing edge cases, and workflow bottlenecks.",
            f"The best implementations route low-value style feedback back to deterministic tools and reserve AI review for reasoning-heavy checks. "
            f"That keeps the review process useful for both authors and reviewers while making {secondary_keyword} easier to discuss with concrete evidence.",
        )

    if heading == "What strong teams do differently":
        return (
            "Strong teams define review guidelines before they scale usage. They decide which findings should be blocking, which stay advisory, "
            "and which patterns the system should ignore entirely because human reviewers have already decided they are low value.",
            "They also review findings as a system, not one pull request at a time. That lets them tighten prompts, rewrite guidance, and remove repeating noise "
            "before developers lose trust in the workflow.",
        )

    if heading == "Implementation example":
        return (
            f"A practical rollout starts with one repository, one pull request flow, and one class of findings your team cares about. "
            f"For this topic, that usually means starting with {display_keyword} on changes where missing context causes expensive review churn.",
            f"Once the first rollout is stable, teams add adjacent checks around {secondary_keyword}, compare the output with human review comments, "
            "and keep only the patterns that produce consistent action from reviewers.",
        )

    if heading == "How to measure whether it is working":
        return (
            f"Measure {display_keyword} the same way you would measure any workflow improvement: with leading indicators before rollout and outcome metrics after rollout. "
            "Useful signals include review turnaround time, rework after review, escaped defects, and whether senior reviewers spend less time on mechanical comments.",
            "The key is to compare quality and speed together. A faster review loop does not help if the team starts missing important issues, and more findings do not help "
            "if nobody trusts or acts on them.",
        )

    return (
        f"{topic.description} The point is to turn the topic into a repeatable operating habit, not a one-off experiment.",
        f"{topic.rationale} Teams get better results when they connect the workflow to concrete engineering outcomes and tune it over time.",
    )


def _display_keyword(keyword: str) -> str:
    display = keyword.title()
    replacements = {
        "Ai": "AI",
        "Pr": "PR",
        "Ci/Cd": "CI/CD",
        "Cd": "CD",
        "Dora": "DORA",
        "Roi": "ROI",
        "Sast": "SAST",
        "Dast": "DAST",
        "Mttr": "MTTR",
        "Owasp": "OWASP",
    }
    for old, new in replacements.items():
        display = display.replace(old, new)
    return display
