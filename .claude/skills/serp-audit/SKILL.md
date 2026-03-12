# SERP Audit Skill

Analyzes search engine results pages for a target keyword to inform content strategy.

## What It Does

- Fetches and parses the top 10-20 SERP results for a given keyword
- Identifies content types ranking (blog posts, docs, videos, forums)
- Extracts featured snippet formats and People Also Ask questions
- Maps SERP features present (knowledge panel, image pack, video carousel)
- Estimates keyword difficulty based on domain authority of ranking pages
- Returns structured data for use by the topic scorer and research brief writer

## Interface

Uses the `KeywordDataProvider` interface defined in `app/providers/keyword_data.py`.
The default implementation uses mock data. Connect a live provider (e.g., via MCP)
for production SERP data from tools like Ahrefs, SEMrush, or SerpAPI.

## Usage

Called automatically during topic scoring and research phases.
Can also be invoked standalone: `python -m app.main serp-audit --keyword "your keyword"`

## Output

Returns a `SERPAuditResult` with: keyword, search volume estimate, difficulty score,
SERP features, top results summary, and content gap opportunities.
