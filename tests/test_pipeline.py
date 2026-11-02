from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import yaml

from mailpulse.config import load_config
from mailpulse.pipeline import Runner
from mailpulse.state.models import StateDocument, utc_now
from mailpulse.state.store import LocalFileStore


def _config(tmp_path: Path):
    payload = {
        "version": 1,
        "accounts": [
            {
                "id": "a",
                "connector": "imap",
                "host": "127.0.0.1",
                "port": 1,
                "username_env": "MAIL_USER",
                "password_env": "MAIL_PASSWORD",
            }
        ],
        "ai": {
            "provider": "openai_compatible",
            "base_url_env": "AI_BASE_URL",
            "api_key_env": "AI_API_KEY",
            "model": "m",
        },
        "filtering": {"enabled": True},
        "notifications": {"telegram": {"enabled": True}},
        "digest": {"enabled": False},
        "sync": {"mode": "new_only", "check_interval_minutes": 30},
        "storage": {"backend": "local_file", "local_path": str(tmp_path / "s.json")},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return load_config(str(path))


def test_interval_gate_skips_recent_run(tmp_path):
    config = _config(tmp_path)
    state = StateDocument()
    state.last_run = utc_now() - timedelta(minutes=5)
    store = LocalFileStore(str(tmp_path / "s.json"))
    results = Runner(config, store=store, state=state).run(mode="run")
    assert results == []


def test_interval_gate_allows_after_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIL_USER", "u")
    monkeypatch.setenv("MAIL_PASSWORD", "p")
    config = _config(tmp_path)
    state = StateDocument()
    state.last_run = utc_now() - timedelta(minutes=29)
    store = LocalFileStore(str(tmp_path / "s.json"))
    results = Runner(config, store=store, state=state).run(mode="run")
    assert len(results) == 1
    assert results[0].errors
