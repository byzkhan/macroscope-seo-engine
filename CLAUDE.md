# Macroscope SEO Engine

Multi-agent content orchestration system that produces one high-quality SEO/AEO-focused blog per day for macroscope.com.

## Quick Reference

```bash
# Full pipeline run (interactive)
python -m app.main run

# Full pipeline run (CI/JSON output)
python -m app.main --json run

# Score topics only
python -m app.main score-topics

# List past runs
python -m app.main list-runs

# Run tests
pytest

# Lint
ruff check app/ tests/
```

## Architecture

Central orchestrator (`app/orchestrator.py`) controls all execution. Agents do not coordinate directly — each stage takes structured Pydantic input and returns structured output.

### Pipeline stages
1. `collect_signals` → `MarketSignalReport`
2. `generate_topics` → `list[TopicCandidate]`
3. `score_topics` → `ScoredTopic` (selected)
4. `build_brief` → `ResearchBrief`
5. `write_draft` → `DraftArticle`
6. `qa_optimize` → `FinalArticle` + `SEOAEOScore`
7. `export` → markdown + JSON files
8. `persist_history` → updates `data/topic_history.json`

### Key modules
| Module | Purpose |
|--------|---------|
| `app/schemas.py` | All Pydantic models — source of truth for data contracts |
| `app/providers.py` | Provider interfaces (market signals, keywords, export, content generation) |
| `app/scoring.py` | Topic scoring with 7 weighted criteria (100 points total) |
| `app/qa.py` | Article QA checks + SEO/AEO scoring |
| `app/orchestrator.py` | Pipeline orchestration — runs all stages |
| `app/storage.py` | Run directory management and artifact persistence |
| `app/prompts.py` | Agent prompt templates |
| `app/config.py` | Configuration loading from `config/` |
| `app/docs_export.py` | Local markdown/JSON export |
| `app/main.py` | CLI entrypoint (click) |

### Provider architecture
All external integrations go through `app/providers.py` interfaces:
- `MarketSignalProvider` — market/social signals (mock: synthetic data)
- `KeywordDataProvider` — search volume, SERP analysis (mock: static metrics)
- `DocumentExportProvider` — Google Docs, CMS export (mock: log-only)
- `ContentGenerationProvider` — LLM-backed content generation (mock: pre-built content)

Swap providers via `ProviderRegistry` — no changes to business logic.

### Scoring weights
business_relevance=25, search_opportunity=20, aeo_fit=15, freshness=10, authority_to_win=15, uniqueness_vs_archive=10, production_ease=5. Total=100.

### Run artifacts
Each run saves to `data/runs/YYYY-MM-DD_HHMMSS/`:
`market_signals.json`, `topic_candidates.json`, `scored_topics.json`, `selected_topic.json`, `research_brief.json`, `draft.md`, `draft_meta.json`, `final.md`, `meta.json`, `qa_result.json`, `run_summary.json`

### Exit codes
- 0: success
- 1: pipeline error (non-fatal stage failure)
- 2: fatal error (pipeline could not complete)
- 3: configuration error

## Conventions
- All data models in `schemas.py` — never define models elsewhere
- Prompts in `app/prompts.py` — never hardcode prompts in business logic
- Config files in `config/` are YAML or Markdown
- Tests cover scoring, schema validation, and QA logic
- External integrations are behind provider interfaces in `providers.py`
- Mock providers ship by default — the pipeline runs without any external APIs
