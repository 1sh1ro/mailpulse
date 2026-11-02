from __future__ import annotations

from datetime import datetime, timezone

from mailpulse.config import DigestConfig
from mailpulse.progress import advance_imap_cursor, digest_due


def test_cursor_advances_through_successes():
    assert advance_imap_cursor(10, [11, 12, 13], set()) == 13


def test_cursor_stops_before_pending():
    assert advance_imap_cursor(10, [11, 12, 13], {"12"}) == 11


def test_cursor_stops_when_first_pending():
    assert advance_imap_cursor(10, [11, 12], {"11"}) == 10


def test_cursor_ignores_lower_uids():
    assert advance_imap_cursor(20, [15, 21], set()) == 21


def test_cursor_handles_empty_batch():
    assert advance_imap_cursor(20, [], set()) == 20


def test_digest_not_due_before_time():
    config = DigestConfig(enabled=True, timezone="Asia/Shanghai", time="09:00")
    now = datetime(2026, 11, 2, 0, 59, tzinfo=timezone.utc)
    zone_now = now.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
    assert digest_due(zone_now, config, None) is False


def test_digest_due_at_time():
    config = DigestConfig(enabled=True, timezone="Asia/Shanghai", time="09:00")
    from zoneinfo import ZoneInfo

    now = datetime(2026, 11, 2, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert digest_due(now, config, None) is True


def test_digest_not_resent_same_day():
    config = DigestConfig(enabled=True, timezone="Asia/Shanghai", time="09:00")
    from zoneinfo import ZoneInfo

    now = datetime(2026, 11, 2, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert digest_due(now, config, "2026-11-02") is False
