from __future__ import annotations

import base64
from datetime import datetime, timezone
from email import message_from_bytes, policy

import httpx

from ..clients import build_client
from ..config import GmailAccount
from ..models import Message
from ..parsing import (
    extract_body_text,
    extract_message_id,
    parse_date,
    parse_message_bytes,
    parse_recipients,
    parse_sender,
)
from .base import Boundary, ConnectorError, FetchResult

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API = "https://gmail.googleapis.com/gmail/v1/users/me"


class HistoryExpired(ConnectorError):
    pass


class GmailConnector:
    kind = "gmail"

    def __init__(
        self,
        account: GmailAccount,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        mark_as_read: bool,
        batch_size: int = 50,
    ) -> None:
        self.account_id = account.id
        self.folder = "INBOX"
        self._account = account
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._mark_as_read = mark_as_read
        self._batch_size = batch_size
        self._client = build_client(account.timeout_seconds)
        self._token: str | None = None

    def connect(self) -> None:
        try:
            response = self._client.post(
                _TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Gmail 令牌刷新网络错误：{exc}") from exc
        if response.status_code != 200:
            raise ConnectorError(
                f"Gmail 令牌刷新失败：HTTP {response.status_code} {response.text[:200]}"
            )
        self._token = response.json()["access_token"]

    def close(self) -> None:
        self._client.close()
        self._token = None

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise ConnectorError("Gmail 尚未完成授权")
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            response = self._client.get(
                f"{_API}{path}", headers=self._headers(), params=params
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Gmail 请求失败：{exc}") from exc
        self._raise_for_status(response)
        return response.json()

    def _post(self, path: str, body: dict) -> dict:
        try:
            response = self._client.post(
                f"{_API}{path}", headers=self._headers(), json=body
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Gmail 请求失败：{exc}") from exc
        self._raise_for_status(response)
        return response.json() if response.content else {}

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 404:
            raise HistoryExpired("Gmail historyId 已过期")
        if response.status_code >= 400:
            raise ConnectorError(
                f"Gmail 接口失败：HTTP {response.status_code} {response.text[:300]}"
            )

    def read_boundary(self, folder: str) -> Boundary:
        profile = self._get("/profile")
        return Boundary(history_id=str(profile["historyId"]))

    def fetch_new(
        self, folder: str, cursor_uid: int | None, cursor_history_id: str | None
    ) -> FetchResult:
        if not cursor_history_id:
            profile = self._get("/profile")
            return FetchResult(messages=[], history_id=str(profile["historyId"]))
        message_ids: list[str] = []
        page_token: str | None = None
        latest_history_id = cursor_history_id
        while True:
            params = {
                "startHistoryId": cursor_history_id,
                "historyTypes": "messageAdded",
                "labelId": "INBOX",
                "maxResults": 500,
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("/history", params)
            latest_history_id = str(payload.get("historyId", latest_history_id))
            for record in payload.get("history", []):
                for added in record.get("messagesAdded", []):
                    identifier = added["message"]["id"]
                    if identifier not in message_ids:
                        message_ids.append(identifier)
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        messages = [self._get_message(identifier) for identifier in message_ids[: self._batch_size]]
        return FetchResult(
            messages=[message for message in messages if message is not None],
            history_id=latest_history_id,
            max_uid=None,
        )

    def sweep_since(
        self, folder: str, since: datetime, max_uid: int | None, max_history_id: str | None
    ) -> list[Message]:
        epoch = int(since.timestamp())
        query = f"{self._account.query} after:{epoch}"
        identifiers: list[str] = []
        page_token: str | None = None
        while True:
            params = {"q": query, "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("/messages", params)
            for item in payload.get("messages", []):
                if item["id"] not in identifiers:
                    identifiers.append(item["id"])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        result = [self._get_message(identifier) for identifier in identifiers]
        return [message for message in result if message is not None]

    def _get_message(self, identifier: str) -> Message | None:
        payload = self._get(f"/messages/{identifier}", {"format": "raw"})
        raw_b64 = payload.get("raw")
        if not raw_b64:
            return None
        raw = base64.urlsafe_b64decode(raw_b64.encode())
        parsed = message_from_bytes(raw, policy=policy.default)
        internal = payload.get("internalDate")
        received = (
            datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc)
            if internal
            else parse_date(parsed)
        )
        return Message(
            account_id=self.account_id,
            folder=self.folder,
            uid=None,
            gmail_id=identifier,
            message_id=extract_message_id(parsed, f"<gmail-{identifier}@local>"),
            sender=parse_sender(parsed),
            subject=(parsed.get("Subject", "") or "").strip(),
            received_at=received,
            body_text=extract_body_text(parsed),
            to_addresses=parse_recipients(parsed),
        )

    def mark_read(self, folder: str, message: Message) -> None:
        if not message.gmail_id:
            raise ConnectorError("缺少 Gmail 邮件标识，无法标记已读")
        self._post(f"/messages/{message.gmail_id}/modify", {"removeLabelIds": ["UNREAD"]})
