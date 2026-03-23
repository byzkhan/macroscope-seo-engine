# Macroscope SEO Engine

Macroscope SEO Engine is a stateless, multi-stage content pipeline that researches, selects, writes, optimizes, fact-checks, and exports technically serious blog posts for Macroscope.

It is built for engineering audiences, not generic marketing content. The system uses multiple specialized subagents, durable run artifacts, explicit quality gates, and a dashboard that can start, stop, inspect, and now resume interrupted runs from the same stage boundary.

Repo-level model and agent operating instructions live in `AGENTS.md`.

## What The System Does

One pipeline run:
- bootstraps a fresh run context with no hidden memory
- gathers technical market signals from multiple source classes
- generates and scores candidate topics through a strict funnel
- builds a structured research brief
- creates writer blueprints and expands the best one into a full draft
- optimizes the draft until it clears a final `9.0` publication gate
- fact-checks the final article
- exports markdown and JSON artifacts
- persists archive history so published topics get cooldowns instead of permanent exclusion

## Current Architecture

```text
CLI / Dashboard
    |
    v
PipelineOrchestrator
    |
    +--> durable execution journal
    |      - events.jsonl
    |      - execution_state.json
    |      - checkpoints/*.json
    |
    +--> ensemble stages
    |      - research scouts
    |      - topic judges
    |      - blueprint writers
    |      - optimizer jury
    |      - final fact check
    |
    +--> providers
           - market signals
           - content generation
           - keyword / SERP data
           - export
```

## End-To-End Flow

```text
bootstrap_run
  -> collect_signals
  -> generate_topics
  -> score_topics
  -> build_brief
  -> write_draft
  -> qa_optimize
  -> fact_check
  -> export
  -> persist_history
```

## Stage-By-Stage Architecture

### 1. `bootstrap_run`
- Creates the immutable `RunContext`.
- Freezes quality policy, source policy, and agent manifest for the run.
- Enforces stateless execution rules.

Artifacts:
- `run_context.json`
- `quality_policy.json`
- `source_policy.json`
- `agent_manifest.json`

### 2. `collect_signals`
- Uses three bundled research scouts instead of many expensive parallel search passes:
  - `community_scout`
  - `primary_source_scout`
  - `practitioner_scout`
- Merges those into one reusable `ResearchPacket`.
- Research is the main stage allowed to use web search.

Artifacts:
- `market_signals.json`
- `research/raw/*.json`
- `research/source_coverage_report.json`
- `research/research_packet.json`

### 3. `generate_topics`
- Uses multiple topic personas, but inside a strict cap.
- Candidate generation is merged and deduped, then hard-capped at `12` candidates.
- Topics are filtered against shortlist history and cooldown policy.

Artifacts:
- `topic_candidates.json`
- `topics/raw/*.json`
- `topics/topic_novelty_report.json`

### 4. `score_topics`
- Runs cheap deterministic scoring across all capped candidates.
- Only the top `6` topics get model-backed judging.
- Only the top `2` get the full topic jury.
- Tie-breakers run only when disagreement is high.

Artifacts:
- `scored_topics.json`
- `selected_topic.json`
- `scoring/topic_scorecards.json`

### 5. `build_brief`
- Uses a bundled brief composer instead of many expensive specialist passes.
- Can fall back to a critic/revision pass if brief quality is below threshold.
- Produces a structured `ResearchBrief` with outline, entities, FAQ, links, claims, CTA, and metadata.

Artifacts:
- `brief/research_brief.json`
- `brief/brief_quality_report.json`
- `brief/attempt_*.json`

### 6. `write_draft`
- Creates multiple low-token writer blueprints first.
- Scores those blueprints locally.
- Expands only the winning blueprint into a full draft by default.
- A second full draft is unlocked only after later optimization rounds if quality is still too low.

Artifacts:
- `drafts/blueprints/*.json`
- `drafts/blueprint_scorecards.json`
- `drafts/selected_blueprint.json`
- `draft.md`
- `draft_meta.json`

### 7. `qa_optimize`
- Builds an `ArticleManifest` instead of re-judging the full raw markdown every round.
- Uses a coordinator + patch loop, then targeted rewrites only where necessary.
- Final publication gate stays strict:
  - average score `>= 9.0`
  - min judge `>= 8.0`
  - technical rigor `>= 9.0`
- The loop continues until the article passes or the user stops the run.

Artifacts:
- `optimization/article_manifest_round_*.json`
- `optimization/pass_*.md`
- `optimization/final_quality_gate_round_*.json`
- `article_manifest.json`
- `qa_result.json`
- `optimized_draft.md`
- `final.md`
- `meta.json`

### 8. `fact_check`
- Runs one final manifest-based fact check.
- This is the second stage, after research, that is allowed to use web search.

Artifacts:
- `fact_check_report.json`

### 9. `export`
- Writes the final markdown and JSON export locally.
- External export providers are pluggable.
- Local export overwrites safely, which makes stage-boundary resume safe.

Artifacts:
- `<slug>.md`
- `<slug>.json`
- `export_results.json`

### 10. `persist_history`
- Adds the final topic to history.
- Applies cooldown behavior rather than permanently killing similar strong topics.

## Guardrails And Quality Model

The pipeline is designed to prevent hidden context bleed and low-quality content:

- Every run starts with a fresh `RunContext`.
- Downstream stages only consume explicit artifacts, not implicit conversation memory.
- Provider calls are stateless.
- Topic reuse is controlled by cooldowns and shortlist memory.
- Final publication requires the optimizer jury gate to pass.
- The user can stop a live run from the dashboard.

## Token-Reduction Design

The current implementation intentionally reduces token burn without changing the default model:

- `3` bundled research scouts instead of a larger search fan-out
- one reusable `ResearchPacket` for downstream stages
- capped topic funnel: `12` candidates -> `6` judged -> `2` full-panel topics
- writer blueprints first, one full draft by default
- `ArticleManifest`-based judging instead of repeatedly sending the full article everywhere
- `RunUsageLedger` persisted per run

Artifacts:
- `usage/run_usage_ledger.json`

## Resumable Runs

Interrupted dashboard runs are now durable and manually resumable.

### How it works
- Dashboard runs execute in a dedicated subprocess, not an in-memory thread.
- Each completed stage writes a checkpoint under `checkpoints/`.
- Each run also writes `execution_state.json`.
- If the dashboard process or machine dies, the next dashboard startup reconciles the old run:
  - live worker still exists -> run remains active
  - worker is gone -> run becomes `Interrupted`
- Resume restarts from the same stage boundary, not from the middle of an API call.

### Important limitation
- Only runs created after this checkpoint system was added are resumable.
- Older incomplete runs are shown as `Interrupted` but not `Resumable`.

## Dashboard

Launch the dashboard:

```bash
python -m app.main dashboard
```

Open:

```text
http://127.0.0.1:8051
```

The dashboard supports:
- `Run pipeline`
- `Stop run`
- `Resume run` for checkpointed interrupted runs
- a compact recent-runs strip
- a thin expandable stage stack
- final markdown export from the last stage

Status model:
- `Running`
- `Stopping`
- `Resuming`
- `Interrupted`
- `Completed`
- `Failed`
- `Canceled`

## CLI Commands

### Full run

```bash
python -m app.main run
python -m app.main --json run
```

### Resume an interrupted run

```bash
python -m app.main resume --run-id 2026-03-18_123456
```

### Dashboard

```bash
python -m app.main dashboard
```

### List runs

```bash
python -m app.main list-runs
```

## Setup

```bash
cd macroscope-seo-engine
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## OpenAI Configuration

Real runs are expected to use OpenAI, not mock mode.

Use a project `.env` or shell environment:

```bash
OPENAI_API_KEY=sk-...
SEO_ENGINE_PROVIDER=openai
```

Optional tuning:

```bash
OPENAI_MODEL=gpt-5-mini
OPENAI_MARKET_MODEL=gpt-5-mini
OPENAI_CONTENT_MODEL=gpt-5-mini
OPENAI_REASONING_EFFORT=medium
OPENAI_ENABLE_WEB_SEARCH=true
OPENAI_SEARCH_CONTEXT_SIZE=medium
```

The dashboard and CLI will now refuse to silently fall back to mock mode for real runs.

## Configuration Surface

The main runtime configuration lives in:
- [config/brand_context.md](/Users/zaid/Documents/Playground/macroscope-seo-engine/config/brand_context.md)
- [config/style_guide.md](/Users/zaid/Documents/Playground/macroscope-seo-engine/config/style_guide.md)
- [config/seo_rules.md](/Users/zaid/Documents/Playground/macroscope-seo-engine/config/seo_rules.md)
- [config/aeo_rules.md](/Users/zaid/Documents/Playground/macroscope-seo-engine/config/aeo_rules.md)
- [config/topic_clusters.yaml](/Users/zaid/Documents/Playground/macroscope-seo-engine/config/topic_clusters.yaml)
- [config/competitors.yaml](/Users/zaid/Documents/Playground/macroscope-seo-engine/config/competitors.yaml)
- [config/forbidden_claims.yaml](/Users/zaid/Documents/Playground/macroscope-seo-engine/config/forbidden_claims.yaml)

Important runtime knobs come from env/config loading in [app/config.py](/Users/zaid/Documents/Playground/macroscope-seo-engine/app/config.py):
- topic funnel caps
- writer blueprint count
- final fact-check toggle
- web-search stages
- quality thresholds
- optimizer behavior

## Run Directory Layout

Each run lives under:

```text
data/runs/<run_id>/
```

Typical contents:

```text
events.jsonl
execution_state.json
checkpoints/
run_context.json
market_signals.json
topic_candidates.json
scored_topics.json
brief/
drafts/
optimization/
fact_check_report.json
export_results.json
run_summary.json
usage/run_usage_ledger.json
```

## Testing

Run the full suite:

```bash
pytest
```

Current coverage areas include:
- topic funnel and token-reduction behavior
- provider selection and runtime config
- dashboard stale-state handling
- stop/resume runtime behavior
- checkpoint and resume planning
- QA/SEO scoring and optimizer patching

## Implementation Notes

Core modules:
- [app/orchestrator.py](/Users/zaid/Documents/Playground/macroscope-seo-engine/app/orchestrator.py): main stage execution
- [app/storage.py](/Users/zaid/Documents/Playground/macroscope-seo-engine/app/storage.py): artifacts, execution journal, checkpoints
- [app/dashboard_runtime.py](/Users/zaid/Documents/Playground/macroscope-seo-engine/app/dashboard_runtime.py): subprocess worker management and UI read models
- [app/openai_providers.py](/Users/zaid/Documents/Playground/macroscope-seo-engine/app/openai_providers.py): OpenAI-backed providers
- [app/providers.py](/Users/zaid/Documents/Playground/macroscope-seo-engine/app/providers.py): provider interfaces and mock implementations
- [app/schemas.py](/Users/zaid/Documents/Playground/macroscope-seo-engine/app/schemas.py): typed contracts

Longer design notes:
- [docs/ensemble-pipeline-blueprint.md](/Users/zaid/Documents/Playground/macroscope-seo-engine/docs/ensemble-pipeline-blueprint.md)
