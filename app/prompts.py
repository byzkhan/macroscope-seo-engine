"""Prompt templates for pipeline agents.

All prompts are defined here to keep them out of business logic.
Each function returns a formatted prompt string ready for LLM consumption.
"""

from __future__ import annotations

from .schemas import (
    ArticleManifest,
    BriefClaimsPlan,
    BriefEntityPlan,
    BriefFAQPlan,
    BriefLinkPlan,
    BriefOutlinePlan,
    MarketSignalReport,
    ResearchPacket,
    ResearchBrief,
    ScoredTopic,
    TopicCandidate,
    WriterBlueprint,
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
Generate 10-12 specific, high-quality blog topic candidates about AI code review,
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


def topic_researcher_persona_prompt(
    *,
    persona_name: str,
    persona_goal: str,
    brand_context: str,
    topic_clusters: dict,
    topic_history: list[dict],
    market_signals: MarketSignalReport | None = None,
) -> str:
    """Generate a stateless prompt for one topic ideation persona."""
    base_prompt = topic_researcher_prompt(
        brand_context=brand_context,
        topic_clusters=topic_clusters,
        topic_history=topic_history,
        market_signals=market_signals,
    )
    return (
        f"You are the {persona_name}. {persona_goal}\n\n"
        "This is a stateless ideation pass. Do not assume any prior agent outputs.\n\n"
        f"{base_prompt}\n\n"
        "Generate exactly 4 candidates optimized for your specialty while still obeying all quality rules."
    )


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


def market_source_scout_prompt(
    *,
    brand_context: str,
    competitors: dict,
    scout_name: str,
    source_focus: str,
    themes: list[str],
    lookback_days: int,
) -> str:
    """Generate a prompt for one specialized research scout."""
    competitor_list = competitors.get("competitors", [])
    competitor_names = [c.get("name", "") for c in competitor_list]

    return f"""You are the {scout_name} for Macroscope's engineering content pipeline.
This is a stateless research assignment. Use only the inputs in this prompt and fresh web research.

## Brand Context
{brand_context}

## Competitors to monitor
{', '.join(name for name in competitor_names if name)}

## Themes
{', '.join(themes)}

## Source Focus
{source_focus}

## Task
Collect recent signals from the last {lookback_days} days that are technically relevant to engineers.
Prioritize engineering depth, concrete workflows, benchmarks, release notes, real user pain, and practitioner evidence.
Limit the output to:
- at most 3 high-signal research items
- at most 5 trending themes
- at most 2 recommended angles

Return:
- signals
- trending_themes
- recommended_angles

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


def brief_outline_prompt(
    *,
    topic: ScoredTopic,
    brand_context: str,
    style_guide: str,
) -> str:
    """Prompt for the outline specialist."""
    return f"""You are the Outline Architect for Macroscope's content engine.
This is a stateless assignment.

## Topic
{topic.candidate.model_dump()}

## Brand Context
{brand_context}

## Style Guide
{style_guide}

## Task
Return a BriefOutlinePlan with:
- 5-8 sections
- clear technical progression
- useful target word counts
- 2-4 strong title options

Favor engineer-respecting structure over generic SEO filler.
"""


def brief_entity_prompt(
    *,
    topic: ScoredTopic,
    brand_context: str,
) -> str:
    """Prompt for keyword/entity planning."""
    return f"""You are the Entity Researcher for Macroscope's content engine.
This is a stateless assignment.

## Topic
{topic.candidate.model_dump()}

## Brand Context
{brand_context}

## Task
Return a BriefEntityPlan with:
- primary_keyword
- secondary_keywords
- entities
- meta_description

Use technically precise phrasing and avoid fluff keywords.
"""


def brief_faq_prompt(
    *,
    topic: ScoredTopic,
    brand_context: str,
) -> str:
    """Prompt for FAQ planning."""
    return f"""You are the FAQ Builder for Macroscope's content engine.
This is a stateless assignment.

## Topic
{topic.candidate.model_dump()}

## Brand Context
{brand_context}

## Task
Return a BriefFAQPlan with 4-7 real questions engineers would ask when evaluating this topic.
Suggested answers should be direct, technically grounded, and snippet-friendly.
"""


def brief_link_prompt(
    *,
    topic: ScoredTopic,
    topic_history: list[dict],
) -> str:
    """Prompt for internal linking and CTA planning."""
    history_links = [{"slug": t.get("slug"), "title": t.get("title")} for t in topic_history]
    return f"""You are the Internal Link Planner for Macroscope's content engine.
This is a stateless assignment.

## Topic
{topic.candidate.model_dump()}

## Existing Content
{history_links}

## Task
Return a BriefLinkPlan with:
- 3-6 realistic internal links to prior Macroscope content
- a strong CTA

Infer plausible target paths from the existing content list when needed.
"""


def brief_claims_prompt(
    *,
    topic: ScoredTopic,
    forbidden_claims: list[str],
    requires_evidence: list[dict],
) -> str:
    """Prompt for evidence and claims-risk planning."""
    return f"""You are the Claims Risk Reviewer for Macroscope's content engine.
This is a stateless assignment.

## Topic
{topic.candidate.model_dump()}

## Forbidden Claims
{forbidden_claims}

## Evidence Rules
{requires_evidence}

## Task
Return a BriefClaimsPlan with:
- claims_needing_evidence
- do_not_say

Be conservative about technical claims, performance claims, and benchmark claims.
"""


def brief_composer_prompt(
    *,
    topic: ScoredTopic,
    research_packet: ResearchPacket,
    brand_context: str,
    style_guide: str,
    forbidden_claims: list[str],
    topic_history: list[dict],
) -> str:
    """Prompt for the single-call brief composer."""
    history_links = [{"slug": t.get("slug"), "title": t.get("title")} for t in topic_history]
    return f"""You are the BriefComposer for Macroscope's content engine.
This is a stateless assignment. Use only the selected topic and structured research packet.

## Selected Topic
{topic.candidate.model_dump()}

## Research Packet
{research_packet.model_dump()}

## Brand Context
{brand_context}

## Style Guide
{style_guide}

## Forbidden Claims
{forbidden_claims}

## Existing Content
{history_links}

## Task
Return a complete ResearchBrief.
Keep it compact, technically sound, and grounded in the research packet. Do not invent unsupported entities or links.
"""


def brief_critic_prompt(
    *,
    brief: ResearchBrief,
    research_packet: ResearchPacket,
    notes: list[str],
) -> str:
    """Prompt for the fallback brief critic/repair pass."""
    return f"""You are the BriefCritic for Macroscope's content engine.
This is a stateless repair pass.

## Current Brief
{brief.model_dump()}

## Research Packet
{research_packet.model_dump()}

## Deficits To Fix
{notes}

## Task
Return a stronger ResearchBrief that fixes the listed deficits without expanding into fluff.
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


def blog_writer_persona_prompt(
    *,
    brief: ResearchBrief,
    brand_context: str,
    style_guide: str,
    persona_name: str,
    persona_focus: str,
) -> str:
    """Generate a stateless prompt for one writer persona."""
    return (
        f"You are the {persona_name} for Macroscope. {persona_focus}\n\n"
        "This is a stateless writing assignment. You are not aware of any other writer outputs.\n\n"
        f"{blog_writer_prompt(brief, brand_context, style_guide)}\n\n"
        "Lean into your specialty while staying faithful to the brief."
    )


def writer_blueprint_prompt(
    *,
    brief: ResearchBrief,
    research_packet: ResearchPacket,
    brand_context: str,
    persona_name: str,
    persona_focus: str,
) -> str:
    """Prompt for a low-token writer blueprint."""
    return f"""You are the {persona_name} for Macroscope. {persona_focus}
This is a stateless blueprint assignment. Do not write the full article.

## Brief
{brief.model_dump()}

## Research Packet
{research_packet.model_dump()}

## Brand Context
{brand_context}

## Task
Return a WriterBlueprint with:
- opening_hook
- direct_answer
- 5-8 sections with bullets
- faq_plan
- internal_link_targets
- claims_plan

Be concise and technically grounded.
"""


def draft_from_blueprint_prompt(
    *,
    brief: ResearchBrief,
    blueprint: WriterBlueprint,
    research_packet: ResearchPacket,
    brand_context: str,
    style_guide: str,
) -> str:
    """Prompt for turning one blueprint into a full draft."""
    return f"""You are the Blog Writer for Macroscope.
This is a stateless writing assignment. Write the full article from the selected blueprint only.

## Selected Blueprint
{blueprint.model_dump()}

## Brief
{brief.model_dump()}

## Research Packet
{research_packet.model_dump()}

## Brand Context
{brand_context}

## Style Guide
{style_guide}

## Task
Return the complete markdown article. Preserve the blueprint structure, keep internal links as markdown links, and keep the direct answer immediately after the H1.
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
10. Preserve markdown links exactly as markdown links; never replace internal links with bare URLs
11. Keep a strong two-sentence direct answer immediately after the H1

Return the optimized markdown article only, no commentary.
"""


def optimizer_persona_prompt(
    *,
    draft_content: str,
    brief: ResearchBrief,
    seo_rules: str,
    aeo_rules: str,
    optimizer_name: str,
    optimizer_focus: str,
    qa_snapshot: dict | None = None,
    optimization_notes: list[str] | None = None,
    round_number: int | None = None,
) -> str:
    """Generate a stateless prompt for one optimization persona."""
    notes_block = ""
    if qa_snapshot or optimization_notes or round_number is not None:
        notes_block = (
            "\n\n## Current Quality Snapshot\n"
            f"- round: {round_number if round_number is not None else 'unknown'}\n"
            f"- qa_snapshot: {qa_snapshot or {}}\n"
            f"- priority_fixes: {optimization_notes or []}\n"
            "Treat these facts as authoritative. Fix the listed issues directly.\n"
        )
    return (
        f"You are the {optimizer_name}. {optimizer_focus}\n\n"
        "This is a stateless optimization pass. Improve only the article markdown.\n\n"
        f"{seo_aeo_editor_prompt(draft_content, brief, seo_rules, aeo_rules)}"
        f"{notes_block}\n\n"
        "Preserve technical depth while improving the dimensions in your specialty. "
        "Do not remove existing markdown internal links, FAQ headings, or the direct-answer opening."
    )


def optimization_coordinator_prompt(
    *,
    article_manifest: ArticleManifest,
    brief: ResearchBrief,
    seo_rules: str,
    aeo_rules: str,
    optimization_notes: list[str],
    round_number: int,
) -> str:
    """Prompt for the single structured optimization coordinator."""
    return f"""You are the Optimization Coordinator for Macroscope.
This is a stateless optimization pass. You do not rewrite the whole article.

## Article Manifest
{article_manifest.model_dump()}

## Brief Snapshot
{{
  "primary_keyword": "{brief.primary_keyword}",
  "secondary_keywords": {brief.secondary_keywords},
  "faq_count": {len(brief.faqs)},
  "internal_links": {[link.target_path for link in brief.internal_link_suggestions]},
  "do_not_say": {brief.do_not_say}
}}

## SEO Rules
{seo_rules}

## AEO Rules
{aeo_rules}

## Priority Fixes
round={round_number}
{optimization_notes}

## Task
Return an OptimizationPatch with:
- opening_direct_answer if it should be replaced
- internal_link_suggestions to add or restore
- section_rewrites only for sections that truly need focused rewriting
- faq_questions_to_strengthen
- notes

Keep the patch minimal. Prefer local structural fixes over large rewrites.
"""


def focused_section_rewrite_prompt(
    *,
    article_manifest: ArticleManifest,
    brief: ResearchBrief,
    section_headings: list[str],
) -> str:
    """Prompt for focused section-only rewrites."""
    return f"""You are the Focused Section Rewriter for Macroscope.
This is a stateless rewrite pass. Rewrite only the requested sections.

## Article Manifest
{article_manifest.model_dump()}

## Brief
{brief.model_dump()}

## Sections To Rewrite
{section_headings}

## Task
Return an OptimizationPatch whose section_rewrites contain only the rewritten markdown for the requested headings.
Do not include unchanged sections.
"""


def topic_judge_prompt(
    *,
    judge_name: str,
    judge_focus: str,
    topic: ScoredTopic,
    keyword_metrics: dict[str, dict],
    reuse_notes: list[str],
) -> str:
    """Prompt for one topic judge."""
    return f"""You are the {judge_name} for Macroscope's content engine.
This is a stateless judging assignment. Judge independently from first principles.

## Judge Focus
{judge_focus}

## Topic Candidate
{topic.candidate.model_dump()}

## Deterministic Context
- raw score total: {topic.total_score}
- keyword metrics: {keyword_metrics}
- archive/reuse notes: {reuse_notes}

## Task
Return a JudgeScore with:
- judge
- score from 0 to 10
- concise rationale
- short notes

Be strict. A strong score must be earned.
"""


def article_judge_prompt(
    *,
    judge_name: str,
    judge_focus: str,
    article_manifest: ArticleManifest,
    brief: ResearchBrief,
) -> str:
    """Prompt for one article judge."""
    return f"""You are the {judge_name} for Macroscope's content engine.
This is a stateless judging assignment. Judge independently from first principles.

## Judge Focus
{judge_focus}

## Research Brief Snapshot
{{
  "primary_keyword": "{brief.primary_keyword}",
  "secondary_keywords": {brief.secondary_keywords},
  "target_word_count": {brief.target_word_count},
  "faq_count": {len(brief.faqs)},
  "do_not_say": {brief.do_not_say}
}}

## Article Manifest
{article_manifest.model_dump()}

## Task
Return a JudgeScore with:
- judge
- score from 0 to 10
- concise rationale
- short notes

Do not praise weak content. Score only what is actually present in the article.
Treat the QA and SEO snapshots inside the manifest as authoritative computed facts. Do not contradict them.
"""


def fact_check_prompt(
    *,
    article_manifest: ArticleManifest,
    research_packet: ResearchPacket,
) -> str:
    """Prompt for the final fact-check stage."""
    return f"""You are the Final Fact Checker for Macroscope.
This is a stateless verification pass that may use fresh web search.

## Article Manifest
{article_manifest.model_dump()}

## Research Packet
{research_packet.model_dump()}

## Task
Check only meaningful technical and market-facing claims.
Return a FactCheckReport with:
- checked_claims
- verified_claims
- flagged_claims
- required_revisions
- notes
- passed

Only flag claims that are clearly unsupported or contradicted by fresh primary/public evidence.
"""
