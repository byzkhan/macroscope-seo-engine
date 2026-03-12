"""Local file export and CMS integration.

Handles writing final articles as clean markdown with YAML frontmatter
and structured JSON metadata for downstream consumption.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .providers import ExportResult
from .schemas import FinalArticle, RunSummary

logger = logging.getLogger(__name__)


class LocalExporter:
    """Exports articles as clean markdown and JSON to a local directory."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export(self, article: FinalArticle) -> ExportResult:
        """Write markdown with YAML frontmatter and a JSON metadata sidecar."""
        # Markdown with frontmatter
        md_path = self.output_dir / f"{article.slug}.md"
        frontmatter = (
            "---\n"
            f'title: "{article.title}"\n'
            f"slug: {article.slug}\n"
            f'meta_description: "{article.meta_description}"\n'
            f"date: {article.created_at.strftime('%Y-%m-%d')}\n"
            f"seo_score: {article.seo_aeo_score.total:.1f}\n"
            f"seo_grade: {article.seo_aeo_score.grade}\n"
            f"word_count: {article.word_count}\n"
            "---\n\n"
        )
        md_path.write_text(frontmatter + article.content_md, encoding="utf-8")

        # JSON metadata sidecar
        json_path = self.output_dir / f"{article.slug}.json"
        meta = {
            "title": article.title,
            "slug": article.slug,
            "meta_description": article.meta_description,
            "word_count": article.word_count,
            "seo_aeo_score": article.seo_aeo_score.model_dump(),
            "faqs_present": article.faqs_present,
            "internal_links": article.internal_links,
            "created_at": article.created_at.isoformat(),
        }
        json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        logger.info("Exported: %s (md) + %s (json)", md_path, json_path)
        return ExportResult(
            target="local",
            success=True,
            message=f"Exported to {md_path}",
            metadata={"markdown_path": str(md_path), "json_path": str(json_path)},
        )


def export_article(
    article: FinalArticle,
    run_summary: RunSummary,
    output_dir: Path,
    google_docs_provider=None,
) -> list[ExportResult]:
    """Run all export operations for a final article.

    The local export always runs. External providers (Google Docs, CMS)
    are only invoked if a real provider instance is passed.
    """
    results: list[ExportResult] = []

    # Local export — always runs
    local = LocalExporter(output_dir)
    results.append(local.export(article))

    # Google Docs — only if a real provider is injected
    if google_docs_provider is not None:
        results.append(google_docs_provider.export(article, {"run_id": run_summary.run_id}))

    return results
