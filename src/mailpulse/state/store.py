from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

import httpx

from ..config import StorageConfig
from ..clients import build_client
from .models import StateDocument


class StateError(Exception):
    pass


class StateConflict(StateError):
    pass


class StateStore(Protocol):
    def load(self) -> StateDocument: ...
    def save(self, state: StateDocument) -> None: ...


class LocalFileStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def load(self) -> StateDocument:
        if not self._path.exists():
            return StateDocument()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(f"状态文件不是合法的 JSON：{self._path}") from exc
        return StateDocument.from_json(raw)

    def save(self, state: StateDocument) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_json(), ensure_ascii=False, indent=2)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self._path.parent, delete=False, suffix=".tmp"
        )
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self._path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise


class GitHubContentsStore:
    def __init__(self, config: StorageConfig, token: str, client: httpx.Client) -> None:
        if not config.repo:
            raise StateError("github_private_state 后端缺少 storage.repo")
        self._repo = config.repo
        self._path = config.path
        self._branch = config.branch
        self._token = token
        self._client = client
        self._sha: str | None = None
        self._base_url = f"https://api.github.com/repos/{self._repo}/contents/"
        self._max_bytes = 1024 * 1024

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def load(self) -> StateDocument:
        response = self._client.get(
            self._base_url + self._path,
            headers=self._headers(),
            params={"ref": self._branch},
            timeout=30.0,
        )
        if response.status_code == 404:
            self._sha = None
            return StateDocument()
        if response.status_code != 200:
            raise StateError(
                f"读取状态仓库失败：HTTP {response.status_code} {response.text[:300]}"
            )
        payload = response.json()
        self._sha = payload["sha"]
        content = base64.b64decode(payload["content"])
        if not content.strip():
            return StateDocument()
        try:
            raw = json.loads(content.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError("状态仓库中的状态文件不是合法的 JSON") from exc
        return StateDocument.from_json(raw)

    def save(self, state: StateDocument) -> None:
        state.revision += 1
        body_bytes = json.dumps(state.to_json(), ensure_ascii=False, indent=2).encode()
        if len(body_bytes) > self._max_bytes:
            raise StateError(
                f"状态文件大小 {len(body_bytes)} 字节超过 1 MB 上限，请检查 ledger 保留天数"
            )
        body = {
            "message": f"mailpulse: state revision {state.revision}",
            "content": base64.b64encode(body_bytes).decode(),
            "branch": self._branch,
        }
        if self._sha:
            body["sha"] = self._sha
        response = self._client.put(
            self._base_url + self._path,
            headers=self._headers(),
            json=body,
            timeout=30.0,
        )
        if response.status_code in (409, 422):
            raise StateConflict(
                "状态仓库写入冲突，可能有另一次运行同时更新状态；本轮结果未提交"
            )
        if response.status_code not in (200, 201):
            raise StateError(
                f"写入状态仓库失败：HTTP {response.status_code} {response.text[:300]}"
            )
        self._sha = response.json()["content"]["sha"]


def build_store(config: StorageConfig, client: httpx.Client | None = None) -> StateStore:
    if config.backend == "local_file":
        return LocalFileStore(config.local_path)
    token = os.environ.get(config.token_env, "")
    if not token:
        raise StateError(f"缺少环境变量 {config.token_env}，无法访问状态仓库")
    return GitHubContentsStore(config, token, client or build_client(30.0))
