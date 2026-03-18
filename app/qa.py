"""Quality assurance checks for articles.

Implements content validation rules for SEO, AEO, and editorial quality.
Each check returns a QACheck; the full suite returns a QAResult.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .schemas import SEOAEOScore


def normalize_markdown_headings(content: str) -> str:
    """Convert setext-style markdown headings into ATX headings for analysis."""
    lines = content.splitlines()
    normalized: list[str] = []
    index = 0

    while index < len(lines):
        current = lines[index].rstrip()
        if index + 1 < len(lines):
            underline = lines[index + 1].strip()
            if (
                current
                and not current.lstrip().startswith(("#", "-", "*", ">", "`"))
                and len(underline) >= 3
                and set(underline) <= {"=", "-"}
            ):
                level = "#" if "=" in underline else "##"
                normalized.append(f"{level} {current.strip()}")
                index += 2
                continue
        normalized.append(lines[index])
        index += 1

    result = "\n".join(normalized)
    if content.endswith("\n"):
        result += "\n"
    return result


@dataclass
class QACheck:
    """Result of a single QA check."""

    name: str
    passed: bool
    message: str
    severity: str = "error"  # "error" blocks publishing; "warning" is advisory


@dataclass
class QAResult:
    """Aggregated result of all QA checks."""

    checks: list[QACheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if no error-severity checks failed."""
        return all(c.passed for c in self.checks if c.severity == "error")

    @property
    def failures(self) -> list[QACheck]:
        return [c for c in self.checks if not c.passed]

    @property
    def warnings(self) -> list[QACheck]:
        return [c for c in self.checks if not c.passed and c.severity == "warning"]

    @property
    def summary(self) -> str:
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c.passed)
        return f"{passed}/{total} checks passed"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "checks": [
                {"name": c.name, "passed": c.passed, "message": c.message, "severity": c.severity}
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_word_count(content: str, min_words: int = 800, max_words: int = 4000) -> QACheck:
    """Validate article word count is within acceptable range."""
    words = len(content.split())
    passed = min_words <= words <= max_words
    return QACheck(
        name="word_count",
        passed=passed,
        message=f"Word count: {words} (expected {min_words}-{max_words})",
    )


def check_has_faq_section(content: str) -> QACheck:
    """Verify the article contains a FAQ section."""
    content = normalize_markdown_headings(content)
    pattern = r"^#{1,3}\s.*(?:FAQ|Frequently\s+Asked|Common\s+Questions)"
    has_faq = bool(re.search(pattern, content, re.MULTILINE | re.IGNORECASE))
    return QACheck(
        name="faq_section",
        passed=has_faq,
        message="FAQ section present" if has_faq else "Missing FAQ section",
    )


def check_has_direct_answer(content: str) -> QACheck:
    """Check that a direct, substantive answer appears near the article top.

    Looks for at least two sentence-ending punctuation marks in the first
    ~500 characters after the first heading.
    """
    content = normalize_markdown_headings(content)
    match = re.search(r"^#\s+.+\n(.{100,600})", content, re.MULTILINE | re.DOTALL)
    if match:
        intro = match.group(1).strip()
        sentence_ends = len(re.findall(r"[.!?]\s", intro))
        has_substance = sentence_ends >= 2
        return QACheck(
            name="direct_answer",
            passed=has_substance,
            message=(
                "Direct answer found near top"
                if has_substance
                else f"Weak intro — only {sentence_ends} sentence breaks in opening block"
            ),
        )
    return QACheck(
        name="direct_answer",
        passed=False,
        message="Could not locate article intro for direct answer check",
    )


def check_meta_description(meta: str) -> QACheck:
    """Validate meta description length (50-160 characters)."""
    length = len(meta)
    passed = 50 <= length <= 160
    return QACheck(
        name="meta_description",
        passed=passed,
        message=f"Meta description length: {length} chars (expected 50-160)",
    )


def check_heading_structure(content: str) -> QACheck:
    """Verify proper heading hierarchy without level jumps >1."""
    content = normalize_markdown_headings(content)
    headings = re.findall(r"^(#{1,6})\s+", content, re.MULTILINE)
    if not headings:
        return QACheck(name="heading_structure", passed=False, message="No headings found")

    levels = [len(h) for h in headings]

    if levels[0] != 1:
        return QACheck(
            name="heading_structure",
            passed=False,
            message=f"Article starts with H{levels[0]}, expected H1",
        )

    for i in range(1, len(levels)):
        if levels[i] > levels[i - 1] + 1:
            return QACheck(
                name="heading_structure",
                passed=False,
                message=f"Heading level jump from H{levels[i - 1]} to H{levels[i]} at heading {i + 1}",
                severity="warning",
            )

    return QACheck(
        name="heading_structure",
        passed=True,
        message=f"Heading structure valid ({len(headings)} headings)",
    )


def check_internal_links(content: str, min_links: int = 2) -> QACheck:
    """Check for internal link presence (macroscope.com domain or relative paths)."""
    links = re.findall(r"\[.*?\]\((?:/|https?://macroscope\.com).*?\)", content)
    passed = len(links) >= min_links
    return QACheck(
        name="internal_links",
        passed=passed,
        message=f"Found {len(links)} internal links (minimum: {min_links})",
        severity="warning",
    )


def check_slug(slug: str) -> QACheck:
    """Validate slug format (lowercase alphanumeric with hyphens)."""
    valid = bool(re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug))
    return QACheck(
        name="slug_format",
        passed=valid,
        message=f"Slug '{slug}' is valid" if valid else f"Invalid slug format: '{slug}'",
    )


def check_forbidden_claims(content: str, forbidden: list[str]) -> QACheck:
    """Check that no forbidden claims appear in the content."""
    found = [claim for claim in forbidden if claim.lower() in content.lower()]
    return QACheck(
        name="forbidden_claims",
        passed=len(found) == 0,
        message=f"Found forbidden claims: {found}" if found else "No forbidden claims found",
    )


def check_do_not_say(content: str, do_not_say: list[str]) -> QACheck:
    """Check that none of the 'do not say' phrases appear in content."""
    found = [phrase for phrase in do_not_say if phrase.lower() in content.lower()]
    return QACheck(
        name="do_not_say",
        passed=len(found) == 0,
        message=f"Found 'do not say' phrases: {found}" if found else "No forbidden phrases found",
    )


def check_title_length(title: str, max_len: int = 60) -> QACheck:
    """Check that the title is within recommended SEO length."""
    length = len(title)
    passed = 10 <= length <= max_len
    return QACheck(
        name="title_length",
        passed=passed,
        message=f"Title length: {length} chars (recommended <= {max_len})",
        severity="warning",
    )


# ---------------------------------------------------------------------------
# SEO/AEO scoring
# ---------------------------------------------------------------------------


def score_seo_aeo(
    content: str,
    meta_description: str,
    primary_keyword: str,
    slug: str,
) -> SEOAEOScore:
    """Compute SEO/AEO quality scores for an article.

    Each dimension is scored 0-10, total 0-100.
    Uses heuristic analysis — not a substitute for real SERP data.
    """
    content = normalize_markdown_headings(content)
    content_lower = content.lower()
    keyword_lower = primary_keyword.lower()
    words = content_lower.split()
    total_words = len(words)

    # --- Title score: keyword in first heading ---
    first_heading = re.search(r"^#\s+(.+)", content, re.MULTILINE)
    title_has_kw = keyword_lower in first_heading.group(1).lower() if first_heading else False
    title_len_ok = first_heading and 10 <= len(first_heading.group(1)) <= 70 if first_heading else False
    title_score = (5.0 if title_has_kw else 2.0) + (3.0 if title_len_ok else 1.0)

    # --- Meta description score ---
    meta_has_kw = keyword_lower in meta_description.lower()
    meta_len_ok = 50 <= len(meta_description) <= 160
    meta_score = (5.0 if meta_has_kw else 2.0) + (5.0 if meta_len_ok else 2.0)

    # --- Keyword density score (target 0.5-2.5%) ---
    kw_score = 0.0
    if total_words > 0:
        kw_count = content_lower.count(keyword_lower)
        kw_word_count = len(keyword_lower.split())
        density = (kw_count * kw_word_count) / total_words * 100 if kw_word_count else 0
        if 0.5 <= density <= 2.5:
            kw_score = 9.0
        elif 0.2 <= density <= 4.0:
            kw_score = 6.0
        else:
            kw_score = 3.0

    # --- Heading structure score ---
    headings = re.findall(r"^(#{1,6})\s+", content, re.MULTILINE)
    h2_count = sum(1 for h in headings if len(h) == 2)
    heading_score = min(10.0, h2_count * 2.0) if h2_count >= 2 else 3.0

    # --- Internal links score ---
    links = re.findall(r"\[.*?\]\((?:/|https?://macroscope\.com).*?\)", content)
    link_score = min(10.0, len(links) * 2.5)

    # --- FAQ presence score ---
    faq_pattern = r"^#{1,3}\s.*(?:FAQ|Frequently\s+Asked|Common\s+Questions)"
    has_faq = bool(re.search(faq_pattern, content, re.MULTILINE | re.IGNORECASE))
    # Count FAQ sub-questions (H3s after the FAQ heading)
    faq_qs = len(re.findall(r"^###\s+.+\?", content, re.MULTILINE))
    faq_score = (5.0 if has_faq else 0.0) + min(5.0, faq_qs * 1.0)

    # --- Direct answer score ---
    first_300 = " ".join(words[:300])
    has_direct = keyword_lower in first_300 and len(re.findall(r"[.!?]\s", first_300)) >= 3
    direct_score = 9.0 if has_direct else 4.0

    # --- Readability score (average sentence length heuristic) ---
    sentences = [s.strip() for s in re.split(r"[.!?]+", content) if len(s.strip()) > 10]
    if sentences:
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if 10 <= avg_len <= 25:
            readability_score = 9.0
        elif 8 <= avg_len <= 30:
            readability_score = 6.0
        else:
            readability_score = 3.0
    else:
        readability_score = 0.0

    # --- Content depth score ---
    if 1200 <= total_words <= 3000:
        depth_score = 9.0
    elif 800 <= total_words <= 4000:
        depth_score = 6.0
    else:
        depth_score = 3.0

    # --- Freshness signals score ---
    current_year = str(datetime.now(timezone.utc).year)
    has_year = current_year in content
    freshness_words = ["latest", "recently", "new", "updated", current_year]
    has_freshness = any(w in content_lower for w in freshness_words)
    freshness_score = 5.0 + (2.5 if has_year else 0.0) + (2.5 if has_freshness else 0.0)

    return SEOAEOScore(
        title_score=round(min(title_score, 10.0), 1),
        meta_description_score=round(min(meta_score, 10.0), 1),
        keyword_density_score=round(min(kw_score, 10.0), 1),
        heading_structure_score=round(min(heading_score, 10.0), 1),
        internal_links_score=round(min(link_score, 10.0), 1),
        faq_presence_score=round(min(faq_score, 10.0), 1),
        direct_answer_score=round(min(direct_score, 10.0), 1),
        readability_score=round(min(readability_score, 10.0), 1),
        content_depth_score=round(min(depth_score, 10.0), 1),
        freshness_signals_score=round(min(freshness_score, 10.0), 1),
    )


# ---------------------------------------------------------------------------
# Full QA suite
# ---------------------------------------------------------------------------


def run_qa(
    content: str,
    slug: str,
    meta_description: str,
    forbidden_claims: list[str] | None = None,
    do_not_say: list[str] | None = None,
    min_word_count: int = 800,
    max_word_count: int = 4000,
    min_internal_links: int = 2,
) -> QAResult:
    """Run the full QA suite on an article."""
    normalized_content = normalize_markdown_headings(content)
    checks = [
        check_word_count(normalized_content, min_word_count, max_word_count),
        check_has_faq_section(normalized_content),
        check_has_direct_answer(normalized_content),
        check_meta_description(meta_description),
        check_heading_structure(normalized_content),
        check_internal_links(normalized_content, min_internal_links),
        check_slug(slug),
        check_forbidden_claims(normalized_content, forbidden_claims or []),
        check_do_not_say(normalized_content, do_not_say or []),
    ]
    return QAResult(checks=checks)
