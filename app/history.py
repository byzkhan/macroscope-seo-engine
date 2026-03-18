"""Topic cooldown and shortlist history helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .schemas import TopicCooldownRecord, TopicReuseAssessment, TopicShortlistRecord

COOLDOWNS_FILENAME = "topic_cooldowns.json"
SHORTLIST_FILENAME = "topic_shortlist_history.json"


def _read_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_json_list(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    tmp_path.rename(path)


def load_topic_cooldowns(data_dir: Path) -> list[TopicCooldownRecord]:
    """Load active and expired cooldown records."""
    path = data_dir / COOLDOWNS_FILENAME
    return [TopicCooldownRecord.model_validate(row) for row in _read_json_list(path)]


def load_topic_shortlist_history(data_dir: Path) -> list[TopicShortlistRecord]:
    """Load previously shortlisted topics."""
    path = data_dir / SHORTLIST_FILENAME
    return [TopicShortlistRecord.model_validate(row) for row in _read_json_list(path)]


def assess_topic_reuse(
    *,
    slug: str,
    title: str,
    cluster: str,
    keywords: list[str],
    cooldowns: list[TopicCooldownRecord],
    shortlist_records: list[TopicShortlistRecord],
    now: datetime | None = None,
) -> TopicReuseAssessment:
    """Determine whether a topic is eligible and how much penalty it should carry."""
    now = now or datetime.now(timezone.utc)
    penalty = 0.0
    reasons: list[str] = []
    cooldown_until: str | None = None

    for record in cooldowns:
        if record.slug != slug:
            continue
        record_until = datetime.fromisoformat(record.cooldown_until).astimezone(timezone.utc)
        cooldown_until = record.cooldown_until
        if now < record_until:
            return TopicReuseAssessment(
                slug=slug,
                eligible=False,
                penalty=10.0,
                reasons=[f"Topic is in cooldown until {record.cooldown_until}"],
                cooldown_until=record.cooldown_until,
            )
        penalty = max(penalty, 2.0)
        reasons.append("Topic was published previously, but cooldown has expired")

    keywords_lower = {keyword.lower().strip() for keyword in keywords}
    title_tokens = {token for token in title.lower().split() if len(token) > 2}
    recent_shortlists = shortlist_records[-50:]
    for record in recent_shortlists:
        if record.cluster != cluster:
            continue
        shortlist_keywords = {keyword.lower().strip() for keyword in record.keywords}
        overlap = len(keywords_lower & shortlist_keywords)
        title_overlap = len(title_tokens & {token for token in record.title.lower().split() if len(token) > 2})
        if record.slug == slug:
            penalty = max(penalty, 1.5)
            reasons.append("Topic was previously shortlisted but not published")
            continue
        if overlap >= max(1, min(len(keywords_lower), 2)) or title_overlap >= 4:
            penalty = max(penalty, 1.0)
            reasons.append(f"Similar angle previously shortlisted: {record.title}")

    return TopicReuseAssessment(
        slug=slug,
        eligible=True,
        penalty=round(min(penalty, 10.0), 2),
        reasons=reasons,
        cooldown_until=cooldown_until,
    )


def record_topic_shortlist(
    data_dir: Path,
    *,
    run_id: str,
    shortlisted_topics: list[dict],
) -> None:
    """Persist shortlisted but unpublished topics for future reuse."""
    path = data_dir / SHORTLIST_FILENAME
    rows = _read_json_list(path)

    for topic in shortlisted_topics:
        rows.append(
            TopicShortlistRecord(
                slug=topic.get("slug", ""),
                title=topic.get("title", ""),
                cluster=topic.get("cluster", "unknown"),
                recorded_at=datetime.now(timezone.utc).date().isoformat(),
                keywords=topic.get("keywords", []),
                run_id=run_id,
            ).model_dump()
        )

    # Keep the shortlist history bounded.
    rows = rows[-300:]
    _write_json_list(path, rows)


def upsert_topic_cooldown(
    data_dir: Path,
    *,
    slug: str,
    title: str,
    cluster: str,
    keywords: list[str],
    cooldown_days: int,
) -> None:
    """Add or refresh the cooldown record for a published topic."""
    path = data_dir / COOLDOWNS_FILENAME
    now = datetime.now(timezone.utc)
    cooldown_until = (now + timedelta(days=cooldown_days)).isoformat()
    rows = [row for row in _read_json_list(path) if row.get("slug") != slug]
    rows.append(
        TopicCooldownRecord(
            slug=slug,
            title=title,
            cluster=cluster,
            published_at=now.date().isoformat(),
            cooldown_until=cooldown_until,
            keywords=keywords,
        ).model_dump()
    )
    _write_json_list(path, rows)
