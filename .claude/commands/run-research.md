Run the research phase for a specific topic.

Steps:
1. If no topic slug is provided, show the latest scored topics and ask which to research
2. Run `python -m app.main research --topic "$TOPIC_SLUG"` from the project root
3. Read the generated research brief from the run directory
4. Validate the brief contains: outline (5+ sections), FAQs (4+), entities, claims needing evidence, internal links (3+), CTA, do-not-say list
5. Summarize the brief and flag any gaps

Arguments:
- $TOPIC_SLUG — the slug of the topic to research
