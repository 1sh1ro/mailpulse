from __future__ import annotations

import os

import httpx
import pytest

from mailpulse.clients import build_client
from mailpulse.config import StorageConfig
from mailpulse.state.models import LedgerEntry, StateDocument, utc_now
from mailpulse.state.store import GitHubContentsStore, StateConflict

pytestmark = pytest.mark.integration

TOKEN = os.environ.get("STATE_IT_TOKEN", "")
REPO = os.environ.get("STATE_IT_REPO", "")
PATH = "mailpulse-test/state.json"


def _require() -> None:
    if not TOKEN or not REPO:
        pytest.skip("缺少 STATE_IT_TOKEN 或 STATE_IT_REPO 集成测试环境变量")


def _config() -> StorageConfig:
    return StorageConfig(
        backend="github_private_state",
        repo=REPO,
        path=PATH,
        token_env="STATE_IT_TOKEN",
        branch="main",
    )


def _delete_state_file() -> None:
    client = build_client(30.0)
    url = f"https://api.github.com/repos/{REPO}/contents/{PATH}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = client.get(url, headers=headers, params={"ref": "main"})
    if response.status_code == 200:
        sha = response.json()["sha"]
        client.request(
            "DELETE",
            url,
            headers=headers,
            json={"message": "cleanup test state", "sha": sha, "branch": "main"},
        )
    client.close()


def test_github_state_roundtrip_and_conflict():
    _require()
    _delete_state_file()

    store = GitHubContentsStore(_config(), TOKEN, build_client(30.0))
    state = store.load()
    assert state.accounts == {}

    state.account("it", "imap").folder("INBOX").cursor_uid = 7
    state.record(
        "it:INBOX:7",
        LedgerEntry(
            message_id="<gh@test>",
            status="muted",
            category="promotional",
            action="mute",
            source="blocklist",
            rule=None,
            account="it",
            at=utc_now(),
        ),
    )
    store.save(state)

    reloaded = GitHubContentsStore(_config(), TOKEN, build_client(30.0))
    loaded = reloaded.load()
    assert loaded.accounts["it"].folders["INBOX"].cursor_uid == 7
    assert loaded.ledger["it:INBOX:7"].status == "muted"

    stale = GitHubContentsStore(_config(), TOKEN, build_client(30.0))
    stale.load()
    reloaded.save(loaded)
    with pytest.raises(StateConflict):
        stale.save(loaded)

    _delete_state_file()
