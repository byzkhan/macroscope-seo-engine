# Macroscope SEO Engine

Multi-agent content orchestration system that produces one high-quality SEO/AEO-focused blog per day for [macroscope.com](https://macroscope.com).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI (app/main.py)                        │
│                   click commands + JSON mode                    │
├─────────────────────────────────────────────────────────────────┤
│                   Orchestrator (app/orchestrator.py)            │
│              controls all stages sequentially                   │
├──────────┬──────────┬──────────┬──────────┬─────────────────────┤
│ Provider │ Provider │ Provider │ Provider │                     │
│ Market   │ Keyword  │ Doc      │ Content  │  ProviderRegistry   │
│ Signals  │ Data     │ Export   │ Gen      │  (app/providers.py) │
├──────────┴──────────┴──────────┴──────────┴─────────────────────┤
│                                                                 │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────┐          │
│  │Scoring  │  │QA       │  │Storage  │  │Prompts   │  Core    │
│  │Engine   │  │Engine   │  │Layer    │  │Templates │  Modules │
│  └─────────┘  └─────────┘  └─────────┘  └──────────┘          │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                    Schemas (app/schemas.py)                     │
│              Pydantic models — data contracts                   │
└─────────────────────────────────────────────────────────────────┘
```

### Pipeline

```
Market Signals ──► Topic Candidates ──► Score & Select ──► Research Brief
                                                               │
     Export ◄── SEO/AEO QA ◄── Write Draft ◄──────────────────┘
       │
       └──► Persist to Archive
```

## Setup

```bash
# Clone and enter project
cd macroscope-seo-engine

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"
```

## Usage

### Full pipeline run

```bash
# Interactive mode (rich console output)
python -m app.main run

# Headless / CI mode (JSON output only)
python -m app.main --json run

# Verbose logging
python -m app.main -v run
```

### Individual commands

```bash
# Score and rank topics (stages 1-3)
python -m app.main score-topics

# List past runs
python -m app.main list-runs

# Export a run's final article
python -m app.main export --run-id 2026-03-12_143022
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Pipeline error (non-fatal stage failure) |
| 2 | Fatal error (pipeline aborted) |
| 3 | Configuration error |

### CI / Scheduler integration

```bash
# Cron job or CI step
python -m app.main --json run > /tmp/seo-run.json 2>/dev/null
EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
  echo "Pipeline failed with exit code $EXIT_CODE"
  cat /tmp/seo-run.json
fi
```

## Pipeline Stages

| # | Stage | Input | Output | Fatal? |
|---|-------|-------|--------|--------|
| 1 | Collect Signals | Market themes | `MarketSignalReport` | No |
| 2 | Generate Topics | Signals + archive | `list[TopicCandidate]` | No |
| 3 | Score Topics | Candidates + archive | `ScoredTopic` (selected) | **Yes** |
| 4 | Build Brief | Selected topic | `ResearchBrief` | **Yes** |
| 5 | Write Draft | Brief | `DraftArticle` | **Yes** |
| 6 | QA + Optimize | Draft + brief | `FinalArticle` + `SEOAEOScore` | No |
| 7 | Export | Final article | Markdown + JSON files | No |
| 8 | Persist History | Final article | Updated archive | No |

## Scoring Weights

| Criterion | Max Score | Description |
|-----------|-----------|-------------|
| Business Relevance | 25 | Alignment with Macroscope's product |
| Search Opportunity | 20 | Search volume and ranking potential |
| AEO Fit | 15 | Featured snippet / AI citation potential |
| Freshness | 10 | Timeliness, news hooks |
| Authority to Win | 15 | Can Macroscope credibly own this? |
| Uniqueness vs Archive | 10 | Differentiation from published content |
| Production Ease | 5 | Can this be produced quickly? |

Topics scoring below 45/100 are auto-rejected.

## Configuration

All config lives in `config/`:

| File | Purpose |
|------|---------|
| `brand_context.md` | Macroscope brand positioning and voice |
| `style_guide.md` | Content formatting and writing rules |
| `seo_rules.md` | SEO technical requirements |
| `aeo_rules.md` | Answer Engine Optimization rules |
| `competitors.yaml` | Competitor domains and focus areas |
| `topic_clusters.yaml` | Topic cluster definitions and keywords |
| `forbidden_claims.yaml` | Claims that must not appear in content |

## Provider Architecture

External integrations are behind abstract interfaces in `app/providers.py`. The engine ships with mock providers — no API keys or external services needed to run.

```python
from app.providers import ProviderRegistry, MockMarketSignalProvider

# Default: all mocks
registry = ProviderRegistry()

# Swap one provider for a real implementation
registry = ProviderRegistry(
    market_signals=RealHackerNewsProvider(api_key="..."),
    # other providers remain mocked
)
```

### Integration seam documentation

| Provider | Interface | Mock | Real Implementation Notes |
|----------|-----------|------|--------------------------|
| Market Signals | `MarketSignalProvider` | `MockMarketSignalProvider` | HN Algolia API, Reddit API, or MCP servers |
| Keyword Data | `KeywordDataProvider` | `MockKeywordDataProvider` | Google Search Console API, Ahrefs, SEMrush |
| Document Export | `DocumentExportProvider` | `MockGoogleDocsProvider` | Google Docs API, Notion API, or MCP servers |
| Content Generation | `ContentGenerationProvider` | `MockContentGenerationProvider` | Anthropic SDK (claude-opus-4-6 / claude-sonnet-4-6) |

## Testing

```bash
# Run all tests
pytest

# With coverage
pytest --cov=app --cov-report=term-missing

# Specific test file
pytest tests/test_scoring.py -v
```

Tests cover:
- **Scoring**: Weight validation, generic detection, archive deduplication, ranking, selection
- **Schemas**: Pydantic validation, computed fields, field constraints, normalization
- **QA**: All individual checks, SEO/AEO scoring, full QA suite

## Claude Code Integration

### Custom Commands (`.claude/commands/`)
- `daily-blog` — Execute a full pipeline run
- `score-topics` — Score and rank topic candidates
- `run-research` — Build a research brief for a specific topic
- `export-doc` — Export a completed run for publishing

### Skills (`.claude/skills/`)
- `serp-audit` — Analyze SERP landscape for a keyword
- `competitor-gap` — Find untapped topics vs. competitors
- `faq-extractor` — Extract FAQs from search/forum data
- `internal-linker` — Suggest internal links from existing content

### Agents (`.claude/agents/`)
- `topic-researcher` — Generates 15-25 topic candidates
- `market-watcher` — Monitors market signals
- `topic-scorer` — Ranks topics with weighted scoring
- `research-brief-writer` — Produces comprehensive briefs
- `blog-writer` — Writes markdown articles from briefs
- `seo-aeo-editor` — Optimizes for search and answer engines
- `publisher` — Exports and publishes final content

## What's Mocked

The pipeline runs end-to-end without external APIs. All mock data is realistic and domain-specific:

| Component | What's Mocked | What's Real |
|-----------|---------------|-------------|
| Market signals | 5 synthetic signals about AI code review trends | Signal aggregation logic |
| Topic generation | 18 pre-built candidates across 6 clusters | - |
| Topic scoring | Raw scores derived from candidate metadata | Scoring engine, archive dedup, generic detection |
| Research brief | Complete brief with outline, FAQs, entities | - |
| Draft article | ~1,800-word article about AI code review vs linters | - |
| SEO/AEO scoring | - | Full heuristic scoring engine (10 dimensions) |
| QA checks | - | All 9 QA checks run against real content |
| Export | Local markdown/JSON (real), Google Docs (placeholder) | File I/O, frontmatter generation |

## Productionization Roadmap

### Phase 1: Real Content Generation
- [ ] Implement `ContentGenerationProvider` using Anthropic SDK (claude-sonnet-4-6 for drafts, claude-opus-4-6 for editing)
- [ ] Add structured output parsing for topic candidates and research briefs
- [ ] Add retry logic with tenacity for LLM API calls
- [ ] Add cost tracking per run (token usage)

### Phase 2: Real Market Data
- [ ] Implement `MarketSignalProvider` with Hacker News Algolia API
- [ ] Add Reddit API integration for r/programming, r/ExperiencedDevs
- [ ] Implement `KeywordDataProvider` with Google Search Console API
- [ ] Add keyword volume data from Ahrefs or SEMrush API

### Phase 3: Publishing Pipeline
- [ ] Implement `DocumentExportProvider` with Google Docs API
- [ ] Add CMS integration (WordPress REST API or Ghost API)
- [ ] Add Search Console indexing requests for new URLs
- [ ] Add social media post generation for new articles

### Phase 4: Feedback Loop
- [ ] Track article performance via Search Console (impressions, clicks, position)
- [ ] Feed performance data back into scoring weights
- [ ] Auto-update topic_history.json with performance metrics
- [ ] Identify underperforming content for refresh

### Phase 5: Operational Hardening
- [ ] Add structured logging with correlation IDs
- [ ] Add Prometheus metrics for pipeline monitoring
- [ ] Add alerting for pipeline failures
- [ ] Add A/B testing for title options
- [ ] Add human-in-the-loop approval step before publishing

## Project Structure

```
macroscope-seo-engine/
├── .claude/
│   ├── settings.json              # Claude Code permissions
│   ├── commands/                   # Custom slash commands
│   │   ├── daily-blog.md
│   │   ├── score-topics.md
│   │   ├── run-research.md
│   │   └── export-doc.md
│   ├── skills/                     # Reusable skills
│   │   ├── serp-audit/SKILL.md
│   │   ├── competitor-gap/SKILL.md
│   │   ├── faq-extractor/SKILL.md
│   │   └── internal-linker/SKILL.md
│   └── agents/                     # Subagent prompts
│       ├── topic-researcher.md
│       ├── market-watcher.md
│       ├── topic-scorer.md
│       ├── research-brief-writer.md
│       ├── blog-writer.md
│       ├── seo-aeo-editor.md
│       └── publisher.md
├── app/
│   ├── __init__.py
│   ├── main.py                     # CLI entrypoint
│   ├── orchestrator.py             # Pipeline orchestration
│   ├── schemas.py                  # Pydantic models
│   ├── providers.py                # Provider interfaces + mocks
│   ├── scoring.py                  # Topic scoring engine
│   ├── qa.py                       # QA checks + SEO/AEO scoring
│   ├── storage.py                  # Run persistence
│   ├── prompts.py                  # Agent prompt templates
│   ├── config.py                   # Configuration loading
│   └── docs_export.py              # Local file export
├── config/
│   ├── brand_context.md
│   ├── style_guide.md
│   ├── seo_rules.md
│   ├── aeo_rules.md
│   ├── competitors.yaml
│   ├── topic_clusters.yaml
│   └── forbidden_claims.yaml
├── data/
│   ├── topic_history.json
│   ├── archive/
│   ├── runs/
│   ├── keywords/
│   └── signals/
├── tests/
│   ├── test_scoring.py
│   ├── test_schemas.py
│   └── test_qa.py
├── pyproject.toml
├── README.md
└── CLAUDE.md
```
