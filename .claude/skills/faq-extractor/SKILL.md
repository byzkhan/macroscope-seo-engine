# FAQ Extractor Skill

Extracts frequently asked questions from search results, forums, and community data.

## What It Does

- Pulls "People Also Ask" questions from SERP data for a target keyword
- Scrapes relevant forum threads (Stack Overflow, Reddit, HN) for common questions
- Clusters similar questions and deduplicates
- Ranks questions by estimated search frequency and relevance to topic
- Formats questions for direct inclusion in blog FAQ sections
- Identifies question patterns suitable for featured snippet targeting

## Data Sources

- SERP "People Also Ask" boxes (via KeywordDataProvider)
- Forum and community threads (via WebSearch/WebFetch)
- Existing Macroscope blog comments and support tickets (if available)

## Usage

Called automatically during the research brief phase.
Standalone: `python -m app.main extract-faqs --keyword "ai code review"`

## Output

Returns a ranked list of FAQ items, each with: question text, estimated volume,
source, and a suggested concise answer format (paragraph, list, or table).
