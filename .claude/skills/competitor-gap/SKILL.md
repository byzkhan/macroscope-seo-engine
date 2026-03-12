# Competitor Gap Analysis Skill

Compares Macroscope's blog coverage against competitors to find content opportunities.

## What It Does

- Reads competitor definitions from `config/competitors.yaml`
- For each competitor, checks which topic clusters they cover
- Identifies gaps where competitors have content but Macroscope does not
- Identifies advantages where Macroscope covers topics competitors miss
- Scores opportunity value based on topic relevance and competitor coverage density
- Flags high-priority gaps that align with Macroscope's product positioning

## Configuration

Define competitors in `config/competitors.yaml` with their blog URLs and known
topic coverage. The skill cross-references this against `topic_history.json`
to determine Macroscope's existing coverage.

## Usage

Called during topic candidate generation to boost scores for gap-filling topics.
Standalone: `python -m app.main competitor-gap --cluster "ai-code-review"`

## Output

Returns a `CompetitorGapReport` with: coverage matrix, gap opportunities ranked
by priority, and suggested angles that differentiate from competitor content.
