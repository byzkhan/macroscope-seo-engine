"""Topic scoring engine with weighted criteria.

Scores topic candidates on 7 dimensions totaling 100 points.
Implements archive deduplication based on slugs, title word overlap,
and keyword overlap.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .schemas import ScoredTopic, TopicCandidate, TopicScore

# Maximum score per criterion — these are the upper bounds, not weights.
SCORING_WEIGHTS: dict[str, float] = {
    "business_relevance": 25.0,
    "search_opportunity": 20.0,
    "aeo_fit": 15.0,
    "freshness": 10.0,
    "authority_to_win": 15.0,
    "uniqueness_vs_archive": 10.0,
    "production_ease": 5.0,
}

MIN_TOTAL_SCORE = 45.0

GENERIC_INDICATORS = [
    "introduction to",
    "beginner's guide",
    "what is",
    "getting started",
    "101",
    "basics of",
    "overview of",
    "a guide to",
    "understanding",
]


def _load_archive(archive_path: Path) -> list[dict]:
    """Load topic history from JSON file."""
    if not archive_path.exists():
        return []
    try:
        data = json.loads(archive_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _word_set(text: str) -> set[str]:
    """Extract meaningful words (>2 chars) from text as a lowercase set."""
    stopwords = {"the", "and", "for", "with", "how", "that", "this", "your", "from", "are", "was"}
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {token for token in tokens if len(token) > 2 and token not in stopwords}


def check_archive_similarity(
    candidate: TopicCandidate,
    archive: list[dict],
) -> tuple[float, list[str]]:
    """Check how similar a topic is to archived content.

    Returns:
        Tuple of (uniqueness_score 0-10, list of rejection reasons).
    """
    reasons: list[str] = []
    if not archive:
        return 10.0, reasons

    past_slugs = {entry.get("slug", "") for entry in archive}
    past_titles_raw = [entry.get("title", "") for entry in archive]
    past_keywords: set[str] = set()
    for entry in archive:
        past_keywords.update(kw.lower() for kw in entry.get("keywords", []))

    # --- Exact slug match: hard reject ---
    if candidate.slug in past_slugs:
        reasons.append(f"Slug '{candidate.slug}' already exists in archive")
        return 0.0, reasons

    # --- Title word overlap ---
    candidate_words = _word_set(candidate.title)
    if candidate_words:
        for past_title in past_titles_raw:
            past_words = _word_set(past_title)
            if past_words:
                overlap = len(candidate_words & past_words) / len(candidate_words)
                if overlap > 0.7:
                    reasons.append(f"Title too similar to archived: '{past_title}'")
                    return 2.0, reasons
                if overlap > 0.5:
                    reasons.append(f"Moderate title overlap with: '{past_title}'")
                    return 5.0, reasons

    # --- Keyword overlap ---
    candidate_kws = {kw.lower() for kw in candidate.target_keywords}
    kw_overlap = candidate_kws & past_keywords
    if candidate_kws:
        overlap_ratio = len(kw_overlap) / len(candidate_kws)
        if overlap_ratio > 0.8:
            reasons.append(f"High keyword overlap with archive: {kw_overlap}")
            return 3.0, reasons
        if kw_overlap:
            return min(10.0 * (1.0 - overlap_ratio * 0.5), 10.0), reasons

    return 10.0, reasons


def check_generic(candidate: TopicCandidate) -> list[str]:
    """Detect generic topics that should be rejected."""
    reasons: list[str] = []
    title_lower = candidate.title.lower()
    for indicator in GENERIC_INDICATORS:
        if indicator in title_lower:
            reasons.append(f"Generic indicator found: '{indicator}'")
    if len(candidate.target_keywords) < 2:
        reasons.append("Too few target keywords (minimum 2 required)")
    return reasons


def score_topic(
    candidate: TopicCandidate,
    raw_scores: dict[str, float],
    archive_path: Path,
) -> ScoredTopic:
    """Score a single topic candidate against all criteria.

    Args:
        candidate: The topic to score.
        raw_scores: Dict of criterion_name -> raw score value.
            uniqueness_vs_archive is computed from archive comparison.
        archive_path: Path to topic_history.json.

    Returns:
        A ScoredTopic with computed scores and rejection status.
    """
    rejection_reasons: list[str] = []

    generic_reasons = check_generic(candidate)
    rejection_reasons.extend(generic_reasons)

    archive = _load_archive(archive_path)
    uniqueness_score, uniqueness_reasons = check_archive_similarity(candidate, archive)
    rejection_reasons.extend(uniqueness_reasons)

    # Clamp raw scores to their max bounds
    clamped: dict[str, float] = {}
    for key, max_val in SCORING_WEIGHTS.items():
        if key == "uniqueness_vs_archive":
            clamped[key] = min(uniqueness_score, max_val)
        elif key in raw_scores:
            clamped[key] = round(min(max(raw_scores[key], 0.0), max_val), 2)
        else:
            clamped[key] = 0.0

    topic_score = TopicScore(**clamped)

    if topic_score.total < MIN_TOTAL_SCORE:
        rejection_reasons.append(
            f"Total score {topic_score.total:.1f} below minimum {MIN_TOTAL_SCORE}"
        )

    selected = len(rejection_reasons) == 0
    return ScoredTopic(
        candidate=candidate,
        score=topic_score,
        rejection_reasons=rejection_reasons,
        selected=selected,
    )


def rank_topics(scored: list[ScoredTopic]) -> list[ScoredTopic]:
    """Rank scored topics by total score, descending."""
    return sorted(
        scored,
        key=lambda s: (
            s.consensus_score if s.consensus_score is not None else (s.total_score / 10.0),
            -len(s.rejection_reasons),
            s.total_score,
        ),
        reverse=True,
    )


def select_best(scored: list[ScoredTopic]) -> Optional[ScoredTopic]:
    """Select the highest-scoring non-rejected topic.

    Returns None if all topics were rejected.
    """
    ranked = rank_topics(scored)
    for topic in ranked:
        if topic.selected:
            return topic
    return None
