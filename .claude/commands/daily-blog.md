Execute a full daily blog production run for Macroscope.

Steps:
1. Run `python -m app.main run` from the macroscope-seo-engine project root
2. Monitor each pipeline stage for errors
3. If any stage fails, diagnose the issue and suggest fixes
4. Once complete, read the final article from the latest run directory in `data/runs/`
5. Summarize: topic selected, word count, SEO/AEO score and grade, QA check results
6. Show the path to the final markdown file
7. If the SEO/AEO score is below 60/100, flag it for manual review with specific improvement suggestions

For CI/headless execution, use: `python -m app.main --json run`
