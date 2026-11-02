from __future__ import annotations

from datetime import datetime, time

from .config import DigestConfig


def advance_imap_cursor(cursor: int | None, uids: list[int], pending_keys: set[str]) -> int:
    current = cursor or 0
    for uid in sorted(uids):
        if str(uid) in pending_keys:
            break
        if uid > current:
            current = uid
    return current


def digest_due(now_local: datetime, config: DigestConfig, last_sent_date: str | None) -> bool:
    today = now_local.strftime("%Y-%m-%d")
    if last_sent_date == today:
        return False
    scheduled: time = config.at
    return now_local.time() >= scheduled
