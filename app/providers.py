"""Provider interfaces for external integrations.

Defines abstract protocols for market signals, keyword data, document export,
and content generation. Each protocol has a mock implementation that ships
with the engine and can be swapped for real implementations without changing
business logic.

Integration points for MCP or direct APIs are documented inline.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from .schemas import (
    DraftArticle,
    FinalArticle,
    MarketSignal,
    MarketSignalReport,
    ResearchBrief,
    ScoredTopic,
    SearchIntent,
    TopicCandidate,
)

logger = logging.getLogger(__name__)


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
    def collect(self, themes: list[str], lookback_days: int = 14) -> MarketSignalReport:
        """Collect market signals for the given themes."""
        ...


class MockMarketSignalProvider(MarketSignalProvider):
    """Returns realistic synthetic market signals for pipeline testing."""

    def collect(self, themes: list[str], lookback_days: int = 14) -> MarketSignalReport:
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
    def generate_draft(self, prompt: str, brief: ResearchBrief) -> str:
        """Generate a draft article from a brief. Returns markdown."""
        ...

    @abstractmethod
    def optimize_draft(self, prompt: str, draft: str) -> str:
        """SEO/AEO optimize a draft. Returns improved markdown."""
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

    def generate_draft(self, prompt: str, brief: ResearchBrief) -> str:
        """Return a full mock article as markdown."""
        return _mock_article_content(brief)

    def optimize_draft(self, prompt: str, draft: str) -> str:
        """Return the draft unchanged (mock optimizer is a no-op)."""
        return draft


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


def _mock_research_brief(topic: TopicCandidate) -> ResearchBrief:
    """Build a complete research brief for the given topic."""
    from .schemas import FAQ, InternalLink, OutlineSection

    return ResearchBrief(
        topic=topic,
        outline=[
            OutlineSection(
                heading="Introduction: The Linter Ceiling",
                description="Set up the problem — linters are valuable but have a fundamental ceiling. They check syntax and patterns, not intent. AI review breaks through this ceiling.",
                target_word_count=250,
                key_points=[
                    "Linters catch ~15-20% of defects (formatting, simple anti-patterns)",
                    "Logic bugs, race conditions, and incomplete error handling slip through",
                    "AI review understands what code is supposed to do, not just how it's written",
                ],
            ),
            OutlineSection(
                heading="What Linters Actually Catch (and What They Don't)",
                description="Concrete breakdown of linter capabilities and limitations with examples.",
                target_word_count=300,
                key_points=[
                    "Syntax errors, formatting, unused imports, simple complexity metrics",
                    "Cannot: cross-function logic, business rule violations, race conditions",
                    "Example: linter sees valid Python, misses missing return branch",
                ],
            ),
            OutlineSection(
                heading="How AI Code Review Detects Logic Bugs",
                description="Technical explanation of how AI models trace execution paths and detect logical inconsistencies that pattern-matching tools cannot.",
                target_word_count=350,
                key_points=[
                    "Semantic understanding of code intent",
                    "Cross-function and cross-file analysis",
                    "Context-aware: understands what the PR is trying to accomplish",
                    "Code example: missing return, off-by-one, unchecked error",
                ],
            ),
            OutlineSection(
                heading="Real Bug Categories AI Catches That Linters Miss",
                description="Categorized examples with code snippets for each bug type.",
                target_word_count=400,
                key_points=[
                    "Incomplete error handling",
                    "Race conditions and concurrency bugs",
                    "Business logic violations",
                    "Security vulnerabilities beyond pattern matching",
                    "Type confusion in dynamic languages",
                ],
            ),
            OutlineSection(
                heading="Integrating AI Review Into Your Existing Workflow",
                description="Practical guidance on adding AI review alongside linters, not replacing them.",
                target_word_count=250,
                key_points=[
                    "AI review as a complement, not replacement",
                    "Where in the CI pipeline to run each tool",
                    "Handling overlapping findings",
                    "Tuning sensitivity to reduce noise",
                ],
            ),
            OutlineSection(
                heading="Measuring the Impact",
                description="How to measure whether AI review is actually catching more bugs.",
                target_word_count=200,
                key_points=[
                    "Track bugs caught per review tool",
                    "Measure production defect rates before/after",
                    "PR cycle time as a proxy metric",
                    "Developer satisfaction surveys",
                ],
            ),
            OutlineSection(
                heading="Frequently Asked Questions",
                description="FAQ section for AEO optimization.",
                target_word_count=300,
                key_points=["5 FAQs with concise, direct answers"],
            ),
            OutlineSection(
                heading="Conclusion",
                description="Summarize and CTA.",
                target_word_count=150,
                key_points=["Linters necessary but not sufficient", "AI review fills the gap", "CTA"],
            ),
        ],
        target_word_count=2200,
        primary_keyword="ai code review",
        secondary_keywords=[
            "linters vs ai",
            "logic bugs detection",
            "automated code review",
            "code review automation",
            "ai pull request review",
            "code quality tools",
        ],
        entities=[
            "Macroscope",
            "GitHub Copilot",
            "ESLint",
            "Pylint",
            "SonarQube",
            "OWASP",
            "DORA metrics",
        ],
        faqs=[
            FAQ(
                question="What types of bugs does AI code review catch that linters miss?",
                suggested_answer="AI code review catches logic errors, race conditions, incomplete error handling, business logic violations, and security vulnerabilities that require understanding code intent — not just syntax patterns.",
            ),
            FAQ(
                question="Does AI code review replace linters?",
                suggested_answer="No. AI code review complements linters. Linters handle formatting, syntax, and simple anti-patterns efficiently. AI review adds semantic analysis that catches logic bugs and architectural issues linters cannot detect.",
            ),
            FAQ(
                question="How accurate is AI code review compared to human reviewers?",
                suggested_answer="AI code review catches certain bug categories — like missing error handling and type confusion — more consistently than human reviewers. Humans remain better at architectural judgment and business context evaluation.",
            ),
            FAQ(
                question="How does AI code review fit into a CI/CD pipeline?",
                suggested_answer="AI code review runs as a PR check alongside linters and tests. It triggers on PR creation or update, analyzes the diff, and posts review comments. Most teams run it non-blocking initially, then promote to blocking.",
            ),
            FAQ(
                question="What is the ROI of adding AI code review?",
                suggested_answer="Teams typically see 25-40% reduction in PR cycle time and measurable drops in production defect rates within 3-6 months. ROI depends on team size, defect cost, and current review process maturity.",
            ),
        ],
        claims_needing_evidence=[
            "Linters catch 15-20% of defects",
            "25-40% reduction in PR cycle time",
            "Production defect rate improvements within 3-6 months",
            "Gartner prediction on AI code review adoption",
        ],
        internal_link_suggestions=[
            InternalLink(
                anchor_text="AI code review best practices",
                target_path="/blog/ai-code-review-best-practices",
                context="Link when discussing how to configure AI review effectively",
            ),
            InternalLink(
                anchor_text="reduce PR cycle time",
                target_path="/blog/reducing-pr-cycle-time",
                context="Link when discussing speed improvements from AI review",
            ),
            InternalLink(
                anchor_text="static analysis vs AI review",
                target_path="/blog/static-analysis-vs-ai-review",
                context="Link in the linter comparison section for deeper analysis",
            ),
            InternalLink(
                anchor_text="engineering productivity metrics",
                target_path="/blog/engineering-productivity-metrics",
                context="Link when discussing how to measure AI review impact",
            ),
        ],
        cta="See how Macroscope catches the bugs your linters miss — try AI code review on your next PR.",
        do_not_say=[
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
        ],
        meta_description="Learn how AI code review catches logic bugs, race conditions, and security issues that traditional linters miss. Practical examples and integration guide.",
        title_options=[
            "How AI Code Review Catches Bugs That Linters Miss",
            "AI vs. Linters: What Your Code Review Stack Is Missing",
            "Beyond Linting: AI Code Review for Logic Bug Detection",
        ],
    )


def _mock_article_content(brief: ResearchBrief) -> str:
    """Return a full mock article (~1800 words) as markdown."""
    return """# How AI Code Review Catches Bugs That Linters Miss

AI-powered code review catches logic errors, race conditions, and security vulnerabilities that traditional linters fundamentally cannot detect. While linters verify syntax and enforce formatting rules, AI review understands what your code is *supposed to do* — and flags when it doesn't.

Every engineering team runs linters. They catch real issues and enforce consistency. But if your quality strategy stops at linting, you're missing the bugs that actually cause production incidents. Here's how AI code review fills that gap, with concrete examples.

## What Linters Actually Catch (and What They Don't)

Linters are pattern-matching tools. They excel at:

- **Syntax errors** — missing brackets, invalid tokens, malformed expressions
- **Style enforcement** — indentation, naming conventions, line length
- **Simple anti-patterns** — unused variables, unreachable code, redundant comparisons
- **Import hygiene** — unused imports, circular dependency warnings

Tools like [ESLint](https://eslint.org), Pylint, and RuboCop handle these reliably and fast. They're essential infrastructure.

But linters operate on *structure*, not *meaning*. They cannot answer:

- Does this function return the correct value for all input combinations?
- Is this error handling complete, or does it silently swallow failures?
- Does this concurrent code have a race condition?
- Does this authorization check actually protect the endpoint it's guarding?

These questions require understanding intent — which is exactly what AI code review provides.

## How AI Code Review Detects Logic Bugs

AI code review models analyze code semantically. Instead of matching patterns, they build an understanding of:

1. **What the PR is changing** — the diff in context of the full file
2. **What the code should do** — inferred from function names, comments, types, and surrounding logic
3. **Where the code falls short** — gaps between intent and implementation

This allows AI reviewers to catch bugs that are invisible to pattern-matching tools.

Consider this Python function:

```python
def calculate_discount(price: float, user: User) -> float:
    if user.is_premium:
        return price * 0.8
    if user.has_coupon:
        return price * 0.9
```

A linter sees syntactically valid Python. No warnings. But AI review catches the missing return: regular users without a coupon get `None` instead of the full price. This is a logic bug that causes a `TypeError` downstream — and linters will never flag it.

Another example — incomplete error handling in Go:

```go
func fetchUser(id string) (*User, error) {
    resp, err := http.Get(apiURL + "/users/" + id)
    if err != nil {
        return nil, err
    }
    // Missing: resp.Body.Close() and status code check
    var user User
    json.NewDecoder(resp.Body).Decode(&user)
    return &user, nil
}
```

A linter might flag the unused `err` from `Decode`, but it won't catch the missing `resp.Body.Close()`, the unchecked HTTP status code, or the ignored decode error. AI review understands the HTTP client contract and flags all three.

## Real Bug Categories AI Catches

Based on analysis of review findings across production codebases, AI code review consistently catches these categories that linters miss:

### Incomplete Error Handling

The most common category. Functions that handle some error cases but not all, `try/catch` blocks that swallow exceptions silently, and API calls with unchecked response codes.

### Race Conditions

Concurrent access to shared state without proper synchronization. AI review traces data flow across goroutines, threads, or async handlers to identify unprotected mutations.

### Business Logic Violations

Code that runs without errors but produces wrong results. Off-by-one errors in pagination, incorrect boundary conditions in pricing logic, and authorization checks that don't cover all code paths.

### Security Vulnerabilities Beyond SAST

While SAST tools catch known vulnerability patterns (SQL injection templates, XSS sinks), AI review catches context-dependent security issues: insecure deserialization with user-controlled types, SSRF through indirect URL construction, and broken access control in complex authorization flows.

For a deeper comparison, see our analysis of [static analysis vs AI review](/blog/static-analysis-vs-ai-review).

### Type Confusion in Dynamic Languages

Python, JavaScript, and Ruby code where a variable can hold different types depending on the code path. AI review traces type flow and catches cases where a string is treated as a number, or where `None`/`null`/`undefined` reaches code that doesn't handle it.

## Integrating AI Review Into Your Existing Workflow

AI code review is not a replacement for linters — it's an additional layer. The most effective setup runs both:

1. **Linters first** — fast, deterministic, catches the easy stuff
2. **Tests** — verify behavior against known cases
3. **AI review** — catches the logic bugs that survived steps 1 and 2

Most teams integrate AI review as a PR check in their CI pipeline. Tools like [Macroscope](https://macroscope.com) integrate with GitHub and GitLab, running automatically when a PR is opened or updated.

Start with AI review in **non-blocking mode** — it posts comments but doesn't prevent merging. Once your team trusts the signal quality and has tuned sensitivity, promote it to a blocking check.

For setup patterns across different CI systems, see our guide on [AI code review best practices](/blog/ai-code-review-best-practices).

To reduce noise during onboarding, configure severity thresholds so only high-confidence findings appear initially. Expand coverage as the model learns your codebase patterns.

## Measuring the Impact

Adding AI review is an investment. Measure its return:

- **Bugs caught per tool** — track which tool (linter, tests, AI review, human reviewer) catches which bugs. This shows where AI review adds unique value.
- **Production defect rate** — measure defects reaching production before and after AI review adoption. Teams report measurable drops within 3-6 months.
- **PR cycle time** — counterintuitively, adding AI review often [reduces PR cycle time](/blog/reducing-pr-cycle-time) because human reviewers spend less time on mechanical checks.
- **Developer satisfaction** — survey your team. Good AI review should feel like a helpful colleague, not a noisy alarm.

Track these as part of your broader [engineering productivity metrics](/blog/engineering-productivity-metrics) framework.

## Frequently Asked Questions

### What types of bugs does AI code review catch that linters miss?

AI code review catches logic errors, race conditions, incomplete error handling, business logic violations, and security vulnerabilities that require understanding code intent — not just syntax patterns. These are the bugs that cause production incidents.

### Does AI code review replace linters?

No. AI code review complements linters. Linters handle formatting, syntax, and simple anti-patterns efficiently and deterministically. AI review adds semantic analysis that catches logic bugs and architectural issues linters cannot detect. Run both.

### How accurate is AI code review compared to human reviewers?

AI code review catches certain bug categories — like missing error handling and type confusion — more consistently than human reviewers, who may overlook them under time pressure. Humans remain better at architectural judgment, design review, and business context evaluation.

### How does AI code review fit into a CI/CD pipeline?

AI code review runs as a PR check alongside linters and tests. It triggers on PR creation or update, analyzes the diff in context, and posts review comments directly on the PR. Most teams start non-blocking and promote to blocking after tuning.

### What is the ROI of adding AI code review?

Teams typically see 25-40% reduction in PR cycle time and measurable drops in production defect rates within 3-6 months. The return depends on team size, cost per production defect, and current review process maturity.

## Conclusion

Linters are necessary but not sufficient. They catch syntax and style issues reliably — but the bugs that cause production incidents are logic errors, race conditions, and security vulnerabilities that require understanding what code *means*.

AI code review fills this gap. It analyzes code semantically, catches bug categories that pattern-matching tools miss, and integrates into the workflows your team already uses.

See how Macroscope catches the bugs your linters miss — [try AI code review on your next PR](https://macroscope.com).
"""
