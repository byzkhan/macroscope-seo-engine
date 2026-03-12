"""Prompt templates for pipeline agents.

All prompts are defined here to keep them out of business logic.
Each function returns a formatted prompt string ready for LLM consumption.
"""

from __future__ import annotations

from .schemas import (
    MarketSignalReport,
    ResearchBrief,
    ScoredTopic,
    TopicCandidate,
)


def topic_researcher_prompt(
    brand_context: str,
    topic_clusters: dict,
    topic_history: list[dict],
    market_signals: MarketSignalReport | None = None,
) -> str:
    """Generate the prompt for the topic researcher agent."""
    history_slugs = [t.get("slug", "") for t in topic_history]
    history_titles = [t.get("title", "") for t in topic_history]
    clusters_summary = "\n".join(
        f"- {k}: {v.get('description', '')}"
        for k, v in (topic_clusters.get("clusters", {})).items()
    )
    signals_section = ""
    if market_signals:
        signals_section = f"""
## Current Market Signals
Trending themes: {', '.join(market_signals.trending_themes)}
Recommended angles: {', '.join(market_signals.recommended_angles)}
"""

    return f"""You are the Topic Researcher for Macroscope's SEO content engine.
Macroscope is an AI-powered code review platform that integrates with GitHub and GitLab.
It catches bugs, enforces team standards, and accelerates PR workflows using AI.

## Brand Context
{brand_context}

## Topic Clusters
{clusters_summary}

## Already Published (do NOT duplicate these slugs or near-duplicate titles)
Slugs: {', '.join(history_slugs)}
Titles: {'; '.join(history_titles)}
{signals_section}
## Task
Generate 15-25 specific, high-quality blog topic candidates about AI code review,
code quality, PR workflows, engineering productivity, security in review, and DevOps/CI-CD.

Each candidate must have:
- A compelling, specific title (not generic — no "Introduction to", "Beginner's Guide", "What is")
- A URL slug (lowercase, hyphenated)
- Topic cluster assignment from the list above
- 2-3 sentence description of the unique angle
- 3-5 target keywords (lowercase)
- Search intent: informational, navigational, transactional, or commercial
- Freshness signal if applicable (news hook, trending discussion, seasonal angle)
- Source attribution (your reasoning, competitor gap, market signal)
- Rationale for why this topic will rank and convert

## Quality Rules
- NO generic titles. Every topic must have a clear, defensible angle.
- NO duplicates of published slugs or near-duplicate titles.
- Include at least 3 topics with freshness signals.
- Mix across topic clusters — don't over-index on one area.
- Prioritize topics where Macroscope can credibly claim authority.
- Every topic must have AEO potential (could generate featured snippets or AI citations).

Return valid JSON: a list of objects with keys: title, slug, cluster, description,
target_keywords, search_intent, freshness_signal, source, rationale.
"""


def market_watcher_prompt(brand_context: str, competitors: dict) -> str:
    """Generate the prompt for the market watcher agent."""
    competitor_list = competitors.get("competitors", [])
    competitor_names = [c.get("name", "") for c in competitor_list]
    competitor_domains = [c.get("domain", "") for c in competitor_list]

    return f"""You are the Market Watcher for Macroscope's content engine.
Macroscope is an AI-powered code review platform. You monitor the developer tools
landscape for signals that should inform our content strategy.

## Brand Context
{brand_context}

## Competitors to Monitor
{', '.join(f'{n} ({d})' for n, d in zip(competitor_names, competitor_domains))}

## Focus Areas
- AI code review tools and announcements
- Developer productivity trends and research
- PR workflow automation developments
- Code quality and security tooling
- Engineering metrics and measurement
- Competitor content, product launches, and positioning changes

## Task
Collect recent signals (last 14 days) from:
1. Hacker News and tech news sites
2. Reddit r/programming, r/ExperiencedDevs, r/devops
3. Competitor blogs and product pages
4. Industry reports and analyst coverage
5. Conference announcements and CFPs

For each signal: source, title, url (if available), summary (2-3 sentences),
relevance_score (0.0-1.0), detected_at (ISO format), themes (list of tags).

Also provide:
- trending_themes: top 5 aggregated themes
- recommended_angles: 3-5 specific content angles based on signals

Return valid JSON matching the MarketSignalReport schema.
"""


def topic_scorer_prompt(
    candidates: list[TopicCandidate],
    topic_history: list[dict],
    clusters: dict,
) -> str:
    """Generate the prompt for the topic scorer agent."""
    candidates_json = [c.model_dump() for c in candidates]
    history_info = [
        {"slug": t.get("slug"), "title": t.get("title"), "keywords": t.get("keywords", [])}
        for t in topic_history
    ]

    return f"""You are the Topic Scorer for Macroscope's content engine.
Evaluate and rank topic candidates using the weighted scoring system below.

## Scoring Criteria (100 points total)
| Criterion | Max | Description |
|-----------|-----|-------------|
| business_relevance | 25 | Alignment with Macroscope's AI code review platform and audience |
| search_opportunity | 20 | Realistic search volume and ranking potential |
| aeo_fit | 15 | Potential for featured snippets, PAA, AI citations |
| freshness | 10 | Timeliness, news hooks, trending relevance |
| authority_to_win | 15 | Can Macroscope credibly own this topic? |
| uniqueness_vs_archive | 10 | Differentiation from published content |
| production_ease | 5 | Can this be produced quickly and well? |

## Already Published
{history_info}

## Candidates to Score
{candidates_json}

## Auto-Rejection Rules
- Total score < 45
- Slug matches an archived slug
- Title word overlap > 70% with any archived title
- Contains generic indicators ("introduction to", "beginner's guide", "101")

## Output
Score each candidate. Select exactly ONE topic: the highest-scoring non-rejected candidate.
Return valid JSON: list of objects with keys: candidate, score (all 7 dimensions),
rejection_reasons, selected (exactly one true).
"""


def research_brief_prompt(
    topic: ScoredTopic,
    brand_context: str,
    style_guide: str,
    forbidden_claims: list[str],
    topic_history: list[dict],
) -> str:
    """Generate the prompt for the research brief writer agent."""
    topic_data = topic.candidate.model_dump()
    history_links = [
        {"slug": t.get("slug"), "title": t.get("title")} for t in topic_history
    ]

    return f"""You are the Research Brief Writer for Macroscope's content engine.
Create a comprehensive brief that gives the blog writer everything needed to produce
a high-quality AI code review article without additional research.

## Selected Topic
{topic_data}
Score: {topic.total_score}/100

## Brand Context
{brand_context}

## Style Guide
{style_guide}

## Forbidden Claims (must never appear without evidence)
{forbidden_claims}

## Existing Content (for internal linking to macroscope.com/blog/slug)
{history_links}

## Required Brief Components
1. outline: 5-8 sections (heading, description, target_word_count, key_points)
2. target_word_count: 1500-2500 words
3. primary_keyword: highest-value target keyword
4. secondary_keywords: 5-10 related keywords
5. entities: companies, tools, people, concepts to mention
6. faqs: 4-8 questions with concise 40-60 word answers (optimized for featured snippets)
7. claims_needing_evidence: factual claims that require citations
8. internal_link_suggestions: 3-7 links to existing Macroscope content
9. cta: specific call to action for Macroscope
10. do_not_say: phrases to avoid (include all forbidden claims + topic-specific)
11. meta_description: 120-155 chars, includes primary keyword
12. title_options: 3-5 options under 60 chars, front-load keyword

Return valid JSON matching the ResearchBrief schema.
"""


def blog_writer_prompt(
    brief: ResearchBrief,
    brand_context: str,
    style_guide: str,
) -> str:
    """Generate the prompt for the blog writer agent."""
    brief_data = brief.model_dump()

    return f"""You are the Blog Writer for Macroscope, an AI-powered code review platform.
Write a high-quality markdown article following the research brief exactly.

## Research Brief
{brief_data}

## Brand Context
{brand_context}

## Style Guide
{style_guide}

## Requirements
- H1 title using the best title option from the brief
- Direct answer to the core question within the first 2 paragraphs
- Follow the outline sections in order
- H2 for major sections, H3 for subsections only
- FAQ section with all questions from the brief
- FAQ answers: 40-60 words, direct and complete
- Include all internal links naturally using markdown links
- End with conclusion and CTA
- Target: {brief.target_word_count} words
- Primary keyword "{brief.primary_keyword}" in H1, first paragraph, and at least 2 H2s
- DO NOT use any phrase from: {brief.do_not_say}
- Short paragraphs (3-4 sentences max)
- Code examples where relevant (fenced markdown blocks)
- Confident, direct voice — no hedging ("might", "perhaps", "it's possible")
- Every claim must be supportable

Return the complete markdown article with no wrapping or frontmatter.
"""


def seo_aeo_editor_prompt(
    draft_content: str,
    brief: ResearchBrief,
    seo_rules: str,
    aeo_rules: str,
) -> str:
    """Generate the prompt for the SEO/AEO editor agent."""
    return f"""You are the SEO/AEO Editor for Macroscope, specializing in optimizing articles
for both traditional search engines and AI answer engines.

## Draft Article
{draft_content}

## Key Parameters
Primary keyword: {brief.primary_keyword}
Secondary keywords: {brief.secondary_keywords}
Do not say: {brief.do_not_say}
Expected FAQs: {len(brief.faqs)}
Expected internal links: {len(brief.internal_link_suggestions)}
Meta description: {brief.meta_description}

## SEO Rules
{seo_rules}

## AEO Rules
{aeo_rules}

## Audit & Optimize
1. Verify keyword in title, H1, first 100 words, and at least 2 H2s
2. Check keyword density (target 0.5-2.5%)
3. Verify heading hierarchy (H1→H2→H3, no skips)
4. Verify internal links (minimum 3 to macroscope.com paths)
5. Verify direct answer within first 2 paragraphs
6. Verify FAQ section with 4+ questions, 40-60 word answers
7. Check for forbidden phrases
8. Tighten prose — remove filler and hedging
9. Strengthen weak FAQ answers to be featured-snippet-ready

Return the optimized markdown article only, no commentary.
"""
