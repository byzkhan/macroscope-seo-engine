Export the latest (or specified) final article for publishing.

Steps:
1. Find the most recent run in `data/runs/` or use the specified run ID
2. Run `python -m app.main export --run "$RUN_ID"` from the project root
3. Verify the export produced both markdown (with YAML frontmatter) and JSON outputs
4. Show the meta description, title, slug, word count, and SEO grade
5. List any QA warnings from the run summary
6. Provide the file paths for the exported content

Arguments:
- $RUN_ID — optional run directory name (defaults to latest)
