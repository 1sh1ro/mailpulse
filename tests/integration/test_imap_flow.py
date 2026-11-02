from __future__ import annotations

import imaplib
import os
import re
import ssl
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

import pytest
import yaml

from mailpulse.config import load_config
from mailpulse.pipeline import Runner
from mailpulse.state.models import SweepWindow
from mailpulse.state.store import LocalFileStore

pytestmark = pytest.mark.integration

HOST = os.environ.get("MAILPULSE_IT_IMAP_HOST", "")
PORT = os.environ.get("MAILPULSE_IT_IMAP_PORT", "")
CA = os.environ.get("MAILPULSE_IT_IMAP_CA", "")
USER = os.environ.get("MAILPULSE_IT_IMAP_USER", "")
PASSWORD = os.environ.get("MAILPULSE_IT_IMAP_PASSWORD", "")


def _require() -> None:
    if not all([HOST, PORT, CA, USER, PASSWORD]):
        pytest.skip("缺少 MAILPULSE_IT_IMAP_* 集成测试环境变量")


def _connect() -> imaplib.IMAP4_SSL:
    context = ssl.create_default_context(cafile=CA)
    connection = imaplib.IMAP4_SSL(HOST, int(PORT), ssl_context=context, timeout=20)
    connection.login(USER, PASSWORD)
    return connection


def mailbox_state() -> tuple[int, int]:
    connection = _connect()
    typ, data = connection.status("INBOX", "(UIDVALIDITY UIDNEXT)")
    assert typ == "OK", data
    text = data[0].decode()
    uidvalidity = int(re.search(r"UIDVALIDITY (\d+)", text).group(1))
    uidnext = int(re.search(r"UIDNEXT (\d+)", text).group(1))
    connection.logout()
    return uidvalidity, uidnext - 1


def reset_mailbox() -> None:
    connection = _connect()
    typ, data = connection.select("INBOX", readonly=False)
    assert typ == "OK", data
    typ, data = connection.search(None, "ALL")
    if data and data[0]:
        for sequence in data[0].split():
            connection.store(sequence, "+FLAGS", "(\\Deleted)")
        connection.expunge()
    connection.close()
    connection.logout()


def seed(subject: str, sender: str, body: str, minutes_ago: int = 0) -> None:
    connection = _connect()
    connection.select("INBOX", readonly=False)
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender
    message["To"] = "tester@test.local"
    message["Subject"] = subject
    message["Date"] = formatdate(time.time() - minutes_ago * 60, localtime=False, usegmt=True)
    message["Message-Id"] = f"<it-{int(time.time() * 1000000)}@test>"
    message.set_content(body, charset="utf-8")
    stamp = imaplib.Time2Internaldate(time.time() - minutes_ago * 60)
    typ, data = connection.append("INBOX", None, stamp, message.as_bytes())
    assert typ == "OK", data
    connection.close()
    connection.logout()


def build_config(tmp_path: Path, extra_overrides: dict | None = None):
    payload = {
        "version": 1,
        "accounts": [
            {
                "id": "it",
                "connector": "imap",
                "host": HOST,
                "port": int(PORT),
                "username_env": "MAILPULSE_IT_IMAP_USER",
                "password_env": "MAILPULSE_IT_IMAP_PASSWORD",
                "folder": "INBOX",
                "use_ssl": True,
                "tls_ca_file": CA,
            }
        ],
        "ai": {
            "provider": "openai_compatible",
            "base_url_env": "AI_BASE_URL",
            "api_key_env": "AI_API_KEY",
            "model": os.environ.get("MAILPULSE_IT_AI_MODEL", "gpt-4o-mini"),
            "concurrency": 2,
        },
        "filtering": {
            "enabled": True,
            "default_block_action": "mute",
            "blocklist": {"domains": ["example.com", "blocked.example", "test.local"]},
        },
        "notifications": {"telegram": {"enabled": True}},
        "digest": {"enabled": False},
        "sync": {"mode": "new_only", "mark_as_read": False, "batch_size": 20},
        "storage": {"backend": "local_file", "local_path": str(tmp_path / "state.json")},
    }
    if extra_overrides:
        payload = _merge(payload, extra_overrides)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return load_config(str(path))


def _merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def test_initialization_excludes_history(tmp_path):
    _require()
    reset_mailbox()
    seed("历史邮件一", "old@example.com", "初始化之前的历史邮件", 120)
    seed("历史邮件二", "old@example.com", "初始化之前的另一封", 110)
    _, expected_boundary = mailbox_state()

    config = build_config(tmp_path)
    store = LocalFileStore(str(tmp_path / "state.json"))
    Runner(config, store=store).run(mode="init")

    state = store.load()
    folder = state.accounts["it"].folders["INBOX"]
    assert folder.status == "ready"
    assert folder.cursor_uid == expected_boundary
    assert folder.uidvalidity is not None
    assert state.ledger == {}


def test_new_mail_after_init_is_processed_once(tmp_path):
    _require()
    reset_mailbox()
    seed("历史邮件", "old@example.com", "初始化之前", 100)

    config = build_config(tmp_path)
    store = LocalFileStore(str(tmp_path / "state.json"))
    Runner(config, store=store).run(mode="init")
    boundary = store.load().accounts["it"].folders["INBOX"].cursor_uid

    seed("广告推广", "promo@blocked.example", "限时五折优惠", 1)
    seed("项目周报", "team@example.com", "本周完成了接口联调", 1)

    results = Runner(config, store=store).run(mode="run", force=True)
    assert results[0].fetched == 2
    state = store.load()
    assert state.accounts["it"].folders["INBOX"].cursor_uid == boundary + 2
    assert len(state.ledger) == 2

    again = Runner(config, store=store).run(mode="run", force=True)
    assert again[0].fetched == 0
    assert len(store.load().ledger) == 2


def test_sweep_catches_message_between_init_and_run(tmp_path):
    _require()
    reset_mailbox()
    seed("历史邮件", "old@example.com", "初始化之前", 100)

    config = build_config(tmp_path)
    store = LocalFileStore(str(tmp_path / "state.json"))
    Runner(config, store=store).run(mode="init")

    state = store.load()
    folder = state.accounts["it"].folders["INBOX"]
    original_boundary = folder.cursor_uid
    folder.sweep = SweepWindow(
        since=datetime.now(timezone.utc) - timedelta(hours=1), max_uid=original_boundary
    )
    store.save(state)

    seed("窗口期邮件", "news@blocked.example", "初始化窗口期到达的邮件", 1)
    seed("之后的邮件", "team@example.com", "边界之后的邮件", 0)

    Runner(config, store=store).run(mode="run", force=True)
    state = store.load()
    assert len(state.ledger) == 2
    assert state.accounts["it"].folders["INBOX"].sweep is None


def test_mark_as_read_when_enabled(tmp_path):
    _require()
    reset_mailbox()
    config = build_config(
        tmp_path, {"sync": {"mode": "new_only", "mark_as_read": True, "batch_size": 20}}
    )
    store = LocalFileStore(str(tmp_path / "state.json"))
    Runner(config, store=store).run(mode="init")
    seed("普通通知", "team@blocked.example", "内容", 0)
    Runner(config, store=store).run(mode="run", force=True)

    connection = _connect()
    connection.select("INBOX", readonly=True)
    typ, data = connection.search(None, "UNSEEN")
    unseen = data[0].split() if data and data[0] else []
    connection.logout()
    assert unseen == []


def test_uidvalidity_change_triggers_safe_recovery(tmp_path):
    _require()
    reset_mailbox()
    seed("历史邮件", "old@example.com", "初始化之前", 100)

    config = build_config(tmp_path)
    store = LocalFileStore(str(tmp_path / "state.json"))
    Runner(config, store=store).run(mode="init")

    state = store.load()
    folder = state.accounts["it"].folders["INBOX"]
    folder.uidvalidity = 1
    folder.cursor_uid = 999
    store.save(state)

    seed("恢复期邮件", "team@blocked.example", "游标失效之后到达", 1)
    Runner(config, store=store).run(mode="run", force=True)

    state = store.load()
    assert state.accounts["it"].folders["INBOX"].uidvalidity != 1
    assert len(state.ledger) >= 1
