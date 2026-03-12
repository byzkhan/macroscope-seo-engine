Score and rank topic candidates for the Macroscope blog.

Steps:
1. Run `python -m app.main score-topics` from the project root
2. Display the ranked list of topics with their scores
3. For the top 3 topics, explain why they scored well
4. For any rejected topics, explain the rejection reasons
5. If no topics pass the minimum threshold (45/100), suggest adjustments

Optional arguments:
- $TOPIC_CLUSTER — filter by cluster (e.g., "ai-code-review", "pr-workflows")

For JSON output: `python -m app.main --json score-topics`
