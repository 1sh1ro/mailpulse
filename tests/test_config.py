from __future__ import annotations

import pytest

from mailpulse.config import ConfigError, load_config

BASE = {
    "version": 1,
    "accounts": [
        {
            "id": "personal",
            "connector": "imap",
            "host": "imap.example.com",
            "port": 993,
            "username_env": "MAIL_USER",
            "password_env": "MAIL_PASSWORD",
        }
    ],
    "ai": {
        "provider": "openai_compatible",
        "base_url_env": "AI_BASE_URL",
        "api_key_env": "AI_API_KEY",
        "model": "test-model",
    },
    "filtering": {"enabled": True},
    "notifications": {"telegram": {"enabled": True}},
    "digest": {"enabled": False},
    "sync": {"mode": "new_only"},
    "storage": {"backend": "local_file", "local_path": "state.json"},
}


def _write(tmp_path, payload):
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return str(path)


def _deep_merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def test_minimal_config_loads(tmp_path):
    config = load_config(_write(tmp_path, BASE))
    assert config.accounts[0].id == "personal"
    assert config.sync.check_interval_minutes == 30


def test_unknown_field_rejected(tmp_path):
    payload = _deep_merge(BASE, {"filtering": {"unknown_option": 1}})
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_duplicate_account_id_rejected(tmp_path):
    payload = _deep_merge(BASE, {})
    payload["accounts"] = payload["accounts"] + [dict(payload["accounts"][0])]
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_invalid_env_name_rejected(tmp_path):
    payload = _deep_merge(BASE, {"accounts": [{"password_env": "lower_case"}]})
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_default_block_action_only_mute(tmp_path):
    payload = _deep_merge(BASE, {"filtering": {"default_block_action": "move_to_spam"}})
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_ai_rule_action_rejects_digest(tmp_path):
    payload = _deep_merge(
        BASE,
        {
            "filtering": {
                "ai_rules": [
                    {"name": "x", "instruction": "do it", "action": "digest"}
                ]
            }
        },
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_allowlist_blocklist_overlap_rejected(tmp_path):
    payload = _deep_merge(
        BASE,
        {
            "filtering": {
                "allowlist": {"senders": ["a@b.com"]},
                "blocklist": {"senders": ["a@b.com"]},
            }
        },
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_no_output_channel_rejected(tmp_path):
    payload = _deep_merge(
        BASE,
        {"notifications": {"telegram": {"enabled": False}}, "digest": {"enabled": False}},
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_digest_requires_telegram(tmp_path):
    payload = _deep_merge(
        BASE,
        {"notifications": {"telegram": {"enabled": False}}, "digest": {"enabled": True}},
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_github_backend_requires_repo(tmp_path):
    payload = _deep_merge(BASE, {"storage": {"backend": "github_private_state"}})
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_persist_email_body_must_be_false(tmp_path):
    payload = _deep_merge(BASE, {"storage": {"persist_email_body": True}})
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_invalid_timezone_rejected(tmp_path):
    payload = _deep_merge(
        BASE,
        {
            "notifications": {"telegram": {"enabled": True}},
            "digest": {"enabled": True, "timezone": "Mars/Olympus"},
        },
    )
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))


def test_keyword_normalized(tmp_path):
    payload = _deep_merge(
        BASE, {"filtering": {"keywords": [{"keywords": ["  Promo  "], "action": "mute"}]}}
    )
    config = load_config(_write(tmp_path, payload))
    assert config.filtering.keywords[0].keywords == ("promo",)


def test_sync_mode_only_new_only(tmp_path):
    payload = _deep_merge(BASE, {"sync": {"mode": "full_scan"}})
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, payload))
