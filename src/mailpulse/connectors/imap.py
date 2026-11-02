from __future__ import annotations

import imaplib
import re
import ssl
from datetime import datetime, timedelta, timezone

from ..config import ImapAccount
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

_UID_RE = re.compile(r"\bUID (\d+)")
_INTERNALDATE_RE = re.compile(r'INTERNALDATE "([^"]+)"')
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _imap_date(value: datetime) -> str:
    return f"{value.day}-{_MONTHS[value.month - 1]}-{value.year}"


class ImapConnector:
    kind = "imap"

    def __init__(
        self,
        account: ImapAccount,
        username: str,
        password: str,
        mark_as_read: bool,
        batch_size: int = 50,
    ) -> None:
        self.account_id = account.id
        self.folder = account.folder
        self._account = account
        self._username = username
        self._password = password
        self._mark_as_read = mark_as_read
        self._batch_size = batch_size
        self._imap: imaplib.IMAP4 | None = None
        self._selected: str | None = None
        self._readonly: bool | None = None

    def connect(self) -> None:
        timeout = self._account.timeout_seconds
        context = ssl.create_default_context(cafile=self._account.tls_ca_file)
        try:
            if self._account.use_ssl:
                connection = imaplib.IMAP4_SSL(
                    self._account.host,
                    self._account.port,
                    ssl_context=context,
                    timeout=timeout,
                )
            else:
                plain = imaplib.IMAP4(
                    self._account.host, self._account.port, timeout=timeout
                )
                plain.starttls(ssl_context=context)
                connection = plain
        except OSError as exc:
            raise ConnectorError(
                f"无法连接 IMAP 服务器 {self._account.host}:{self._account.port}：{exc}"
            ) from exc
        try:
            connection.login(self._username, self._password)
        except imaplib.IMAP4.error as exc:
            connection.logout()
            raise ConnectorError(f"IMAP 登录失败：{exc}") from exc
        self._imap = connection
        self._selected = None
        self._readonly = None

    def close(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except imaplib.IMAP4.error:
                pass
            finally:
                self._imap = None
                self._selected = None
                self._readonly = None

    def _connection(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise ConnectorError("IMAP 连接尚未建立")
        return self._imap

    def _select(self, folder: str, readonly: bool) -> None:
        connection = self._connection()
        if self._selected == folder and self._readonly == readonly:
            return
        typ, data = connection.select(f'"{folder}"', readonly=readonly)
        if typ != "OK":
            raise ConnectorError(f"选择文件夹 {folder} 失败：{data}")
        self._selected = folder
        self._readonly = readonly

    def read_boundary(self, folder: str) -> Boundary:
        connection = self._connection()
        self._select(folder, readonly=True)
        typ, data = connection.status(f'"{folder}"', "(UIDVALIDITY UIDNEXT)")
        if typ != "OK":
            raise ConnectorError(f"读取文件夹 {folder} 状态失败：{data}")
        text = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        uidvalidity_match = re.search(r"UIDVALIDITY (\d+)", text)
        uidnext_match = re.search(r"UIDNEXT (\d+)", text)
        if not uidvalidity_match or not uidnext_match:
            raise ConnectorError(f"无法从服务器响应中读取 UIDVALIDITY 与 UIDNEXT：{text}")
        uidvalidity = int(uidvalidity_match.group(1))
        uidnext = int(uidnext_match.group(1))
        return Boundary(uidvalidity=uidvalidity, max_uid=max(uidnext - 1, 0))

    def fetch_new(
        self, folder: str, cursor_uid: int | None, cursor_history_id: str | None
    ) -> FetchResult:
        connection = self._connection()
        boundary = self.read_boundary(folder)
        low = (cursor_uid or 0) + 1
        if boundary.max_uid is not None and boundary.max_uid < low:
            return FetchResult(
                messages=[],
                uidvalidity=boundary.uidvalidity,
                max_uid=boundary.max_uid,
            )
        self._select(folder, readonly=not self._mark_as_read)
        typ, data = connection.uid(
            "SEARCH", None, "UID", f"{low}:{boundary.max_uid if boundary.max_uid else '*'}"
        )
        if typ != "OK":
            raise ConnectorError(f"搜索文件夹 {folder} 失败：{data}")
        uids = sorted(
            uid
            for uid in (int(item) for item in (data[0] or b"").split() if item.isdigit())
            if uid >= low
        )
        messages = self._fetch_uids(folder, uids[: self._batch_size])
        return FetchResult(
            messages=messages,
            uidvalidity=boundary.uidvalidity,
            max_uid=boundary.max_uid,
        )

    def sweep_since(
        self, folder: str, since: datetime, max_uid: int | None, max_history_id: str | None
    ) -> list[Message]:
        connection = self._connection()
        if max_uid is None:
            return []
        self._select(folder, readonly=not self._mark_as_read)
        search_since = (since - timedelta(days=1)).astimezone(timezone.utc)
        typ, data = connection.uid(
            "SEARCH", None, "SINCE", _imap_date(search_since), "UID", f"1:{max_uid}"
        )
        if typ != "OK":
            raise ConnectorError(f"搜索文件夹 {folder} 失败：{data}")
        candidates = sorted(
            uid
            for uid in (int(item) for item in (data[0] or b"").split() if item.isdigit())
            if uid <= max_uid
        )
        if not candidates:
            return []
        internal_dates = self._fetch_internal_dates(candidates)
        kept = [
            uid
            for uid in candidates
            if uid in internal_dates and internal_dates[uid] >= since
        ]
        return self._fetch_uids(folder, kept)

    def _fetch_internal_dates(self, uids: list[int]) -> dict[int, datetime]:
        connection = self._connection()
        result: dict[int, datetime] = {}
        for start in range(0, len(uids), self._batch_size):
            chunk = uids[start : start + self._batch_size]
            typ, data = connection.uid(
                "FETCH", ",".join(str(uid) for uid in chunk), "(UID INTERNALDATE)"
            )
            if typ != "OK":
                raise ConnectorError(f"读取邮件接收时间失败：{data}")
            for item in data:
                if isinstance(item, tuple):
                    item = item[0]
                if not isinstance(item, bytes):
                    continue
                text = item.decode(errors="replace")
                uid_match = _UID_RE.search(text)
                date_match = _INTERNALDATE_RE.search(text)
                if not uid_match or not date_match:
                    continue
                try:
                    result[int(uid_match.group(1))] = datetime.strptime(
                        date_match.group(1), "%d-%b-%Y %H:%M:%S %z"
                    ).astimezone(timezone.utc)
                except ValueError:
                    continue
        return result

    def _fetch_uids(self, folder: str, uids: list[int]) -> list[Message]:
        connection = self._connection()
        messages: list[Message] = []
        for start in range(0, len(uids), self._batch_size):
            chunk = uids[start : start + self._batch_size]
            typ, data = connection.uid(
                "FETCH", ",".join(str(uid) for uid in chunk), "(UID INTERNALDATE BODY.PEEK[])"
            )
            if typ != "OK":
                raise ConnectorError(f"获取邮件内容失败：{data}")
            messages.extend(self._parse_fetch(folder, data))
        messages.sort(key=lambda item: item.uid)
        return messages

    def _parse_fetch(self, folder: str, data: list) -> list[Message]:
        messages: list[Message] = []
        for part in data:
            if not isinstance(part, tuple):
                continue
            meta_raw, payload = part
            meta = meta_raw.decode(errors="replace") if isinstance(meta_raw, bytes) else str(meta_raw)
            uid_match = _UID_RE.search(meta)
            if not uid_match:
                continue
            uid = int(uid_match.group(1))
            parsed = parse_message_bytes(payload)
            internal_match = _INTERNALDATE_RE.search(meta)
            if internal_match:
                try:
                    received = datetime.strptime(
                        internal_match.group(1), "%d-%b-%Y %H:%M:%S %z"
                    ).astimezone(timezone.utc)
                except ValueError:
                    received = parse_date(parsed)
            else:
                received = parse_date(parsed)
            messages.append(
                Message(
                    account_id=self.account_id,
                    folder=folder,
                    uid=uid,
                    message_id=extract_message_id(parsed, f"<imap-{uid}@local>"),
                    sender=parse_sender(parsed),
                    subject=(parsed.get("Subject", "") or "").strip(),
                    received_at=received,
                    body_text=extract_body_text(parsed),
                    to_addresses=parse_recipients(parsed),
                )
            )
        return messages

    def mark_read(self, folder: str, message: Message) -> None:
        if message.uid is None:
            raise ConnectorError("缺少 IMAP UID，无法标记已读")
        self._select(folder, readonly=False)
        typ, data = self._connection().uid(
            "STORE", str(message.uid), "+FLAGS", "(\\Seen)"
        )
        if typ != "OK":
            raise ConnectorError(f"标记已读失败：{data}")
