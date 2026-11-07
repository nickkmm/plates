"""Stored timestamps are naive UTC; people read them in Colombia time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.timezones import format_bogota, to_bogota, utc_now


def test_naive_value_is_read_as_utc() -> None:
    assert to_bogota(datetime(2026, 11, 8, 1, 30)).strftime("%d/%m/%Y %H:%M") == "07/11/2026 20:30"


def test_aware_value_keeps_its_instant() -> None:
    value = datetime(2026, 11, 8, 1, 30, tzinfo=UTC)
    assert to_bogota(value) == value and to_bogota(value).hour == 20


def test_format_uses_the_given_pattern() -> None:
    assert format_bogota(datetime(2026, 11, 8, 1, 30), "%Y-%m-%d %H:%M") == "2026-11-07 20:30"


def test_format_of_nothing_is_empty() -> None:
    assert format_bogota(None) == ""


def test_utc_now_is_naive_utc() -> None:
    now = utc_now()
    assert now.tzinfo is None
    assert abs(now - datetime.now(UTC).replace(tzinfo=None)) < timedelta(seconds=5)
