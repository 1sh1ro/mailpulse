from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from mailpulse.state.models import (
    DigestItem,
    LedgerEntry,
    PendingItem,
    StateDocument,
    utc_now,
)
from mailpulse.state.store import LocalFileStore


def test_state_roundtrip(tmp_path):
    state = StateDocument()
    account = state.account("personal", "imap")
    folder = account.folder("INBOX")
    folder.status = "ready"
    folder.uidvalidity = 123
    folder.cursor_uid = 10
    folder.pending["11"] = PendingItem(
        message_id="<a@b>", attempts=1, last_error="boom", first_seen=utc_now()
    )
    state.record(
        "personal:INBOX:9",
        LedgerEntry(
            message_id="<x@y>",
            status="notified",
            category="important",
            action="notify",
            source="ai_rule",
            rule="important-email",
            account="personal",
            at=utc_now(),
        ),
    )
    state.digest_queue.append(
        DigestItem(
            account="personal",
            sender="a@b.com",
            subject="hello",
            summary="s",
            category="normal",
            received_at=utc_now(),
            queued_at=utc_now(),
        )
    )
    store = LocalFileStore(str(tmp_path / "state.json"))
    store.save(state)
    loaded = store.load()
    assert loaded.accounts["personal"].folders["INBOX"].cursor_uid == 10
    assert loaded.accounts["personal"].folders["INBOX"].pending["11"].attempts == 1
    assert loaded.ledger["personal:INBOX:9"].rule == "important-email"
    assert len(loaded.digest_queue) == 1
    assert loaded.already_processed("personal", "<x@y>")


def test_missing_state_file_returns_empty(tmp_path):
    store = LocalFileStore(str(tmp_path / "none.json"))
    assert store.load().accounts == {}


def test_corrupt_state_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    store = LocalFileStore(str(path))
    try:
        store.load()
    except Exception as exc:
        assert "JSON" in str(exc)
    else:
        raise AssertionError("expected failure")


def test_json_shape_stable():
    state = StateDocument()
    payload = json.loads(json.dumps(state.to_json()))
    assert payload["version"] == 1
    assert set(payload) == {
        "version",
        "accounts",
        "ledger",
        "message_index",
        "digest_queue",
        "digest_last_sent_date",
        "last_run",
        "revision",
    }
