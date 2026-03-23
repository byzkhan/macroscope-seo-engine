# Macroscope SEO Engine Agent Guide

This repo runs a stateless OpenAI-backed content pipeline for engineering-facing SEO/AEO articles.
Treat schemas, run artifacts, and checkpoints as the source of truth. Do not rely on hidden conversational memory.

## Fast Commands

```bash
python -m app.main run
python -m app.main --json run
python -m app.main resume --run-id <run_id>
python -m app.main dashboard
python -m app.main list-runs
pytest
ruff check app/ tests/
```

## Pipeline Stages

1. `bootstrap_run`
2. `collect_signals`
3. `generate_topics`
4. `score_topics`
5. `build_brief`
6. `write_draft`
7. `qa_optimize`
8. `fact_check`
9. `export`
10. `persist_history`

Important runtime behavior:
- runs are durable and resumable from the same stage boundary
- `collect_signals` and `fact_check` are the only web-search stages
- topic reuse is controlled by cooldowns, not permanent exclusion
- token usage is reduced through research packets, topic caps, writer blueprints, and article manifests

## Hard Rules For Models

- Keep provider calls stateless.
- Never let hidden cross-run context influence a new run.
- Pass data forward through artifacts and schemas, not implicit memory.
- Do not add prompt logic outside `app/prompts.py`.
- Do not bypass provider interfaces in `app/providers.py`.
- Preserve checkpoints, execution state, and stop/resume behavior.
- Final export must not happen until the publication gate passes:
  - average score >= 9.0
  - min judge >= 8.0
  - technical rigor >= 9.0

## Source Quality Expectations

- Write for engineers, not generic marketing audiences.
- Prefer primary technical sources, engineering blogs, changelogs, benchmarks, docs, and practitioner discussions.
- Avoid hype, shallow SEO filler, and unsupported claims.
- Keep examples concrete and technically defensible.

## Where Core Logic Lives

- `app/orchestrator.py`: pipeline flow, retries, gates, checkpoints
- `app/providers.py`: provider interfaces and mock implementations
- `app/openai_providers.py`: OpenAI-backed providers and caching
- `app/prompts.py`: prompt construction
- `app/schemas.py`: run models and contracts
- `app/dashboard_runtime.py`: dashboard lifecycle, stop/resume, state reconciliation

## Contributor Expectations

- Update tests when behavior changes.
- Preserve deterministic guardrails and artifact contracts unless intentionally migrating them.
- Prefer artifacts over UI state when debugging.
- Keep instruction changes here concise and operational; keep user-facing system explanation in `README.md`.
