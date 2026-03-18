"""Tests for ensemble helper behavior."""

from app.ensemble import merge_market_signal_reports


def test_merge_market_signal_reports_handles_empty_input():
    report = merge_market_signal_reports([])

    assert report.signals == []
    assert report.trending_themes == []
    assert report.recommended_angles == []
    assert report.collected_at is not None
