# Internal Linker Skill

Suggests internal links to existing Macroscope blog posts from new content.

## What It Does

- Reads the published article index from `data/topic_history.json`
- Analyzes the current article's topics, entities, and keyword clusters
- Matches against existing posts by semantic relevance and cluster overlap
- Suggests 3-7 internal links with recommended anchor text and placement
- Checks for orphaned posts that could benefit from new inbound links
- Validates that suggested URLs are live and not redirected

## Data Source

Primary source is `data/topic_history.json`, which tracks all published articles
with their slugs, titles, clusters, publish dates, and URLs.

## Usage

Called automatically during the research brief and SEO/AEO editing phases.
Standalone: `python -m app.main suggest-links --topic "your-topic-slug"`

## Output

Returns a list of `InternalLinkSuggestion` objects, each with: target URL,
target title, suggested anchor text, relevance score, and recommended
placement (intro, body section, or related reading).
