from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PendingItem(_Model):
    message_id: str
    attempts: int = 0
    last_error: str = ""
    first_seen: datetime

    def to_json(self) -> dict:
        return {
            "message_id": self.message_id,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "first_seen": to_iso(self.first_seen),
        }

    @classmethod
    def from_json(cls, raw: dict) -> PendingItem:
        return cls(
            message_id=raw["message_id"],
            attempts=int(raw.get("attempts", 0)),
            last_error=raw.get("last_error", ""),
            first_seen=from_iso(raw.get("first_seen")) or utc_now(),
        )


class SweepWindow(_Model):
    since: datetime
    max_uid: int | None = None
    max_history_id: str | None = None

    def to_json(self) -> dict:
        return {
            "since": to_iso(self.since),
            "max_uid": self.max_uid,
            "max_history_id": self.max_history_id,
        }

    @classmethod
    def from_json(cls, raw: dict) -> SweepWindow:
        return cls(
            since=from_iso(raw["since"]) or utc_now(),
            max_uid=raw.get("max_uid"),
            max_history_id=raw.get("max_history_id"),
        )


class FolderState(_Model):
    status: Literal["initializing", "ready"] = "initializing"
    init_started_at: datetime | None = None
    ready_at: datetime | None = None
    uidvalidity: int | None = None
    cursor_uid: int | None = None
    cursor_history_id: str | None = None
    sweep: SweepWindow | None = None
    pending: dict[str, PendingItem] = Field(default_factory=dict)
    last_success_at: datetime | None = None

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "init_started_at": to_iso(self.init_started_at),
            "ready_at": to_iso(self.ready_at),
            "uidvalidity": self.uidvalidity,
            "cursor_uid": self.cursor_uid,
            "cursor_history_id": self.cursor_history_id,
            "sweep": self.sweep.to_json() if self.sweep else None,
            "pending": {key: item.to_json() for key, item in self.pending.items()},
            "last_success_at": to_iso(self.last_success_at),
        }

    @classmethod
    def from_json(cls, raw: dict) -> FolderState:
        sweep = raw.get("sweep")
        return cls(
            status=raw.get("status", "initializing"),
            init_started_at=from_iso(raw.get("init_started_at")),
            ready_at=from_iso(raw.get("ready_at")),
            uidvalidity=raw.get("uidvalidity"),
            cursor_uid=raw.get("cursor_uid"),
            cursor_history_id=raw.get("cursor_history_id"),
            sweep=SweepWindow.from_json(sweep) if sweep else None,
            pending={
                key: PendingItem.from_json(item)
                for key, item in (raw.get("pending") or {}).items()
            },
            last_success_at=from_iso(raw.get("last_success_at")),
        )


class AccountStateDocument(_Model):
    connector: str
    folders: dict[str, FolderState] = Field(default_factory=dict)
    last_run: datetime | None = None
    last_error: str | None = None

    def folder(self, name: str) -> FolderState:
        if name not in self.folders:
            self.folders[name] = FolderState()
        return self.folders[name]

    def to_json(self) -> dict:
        return {
            "connector": self.connector,
            "folders": {name: state.to_json() for name, state in self.folders.items()},
            "last_run": to_iso(self.last_run),
            "last_error": self.last_error,
        }

    @classmethod
    def from_json(cls, raw: dict) -> AccountStateDocument:
        return cls(
            connector=raw["connector"],
            folders={
                name: FolderState.from_json(item)
                for name, item in (raw.get("folders") or {}).items()
            },
            last_run=from_iso(raw.get("last_run")),
            last_error=raw.get("last_error"),
        )


class LedgerEntry(_Model):
    message_id: str
    status: Literal["notified", "muted", "digested", "failed"]
    category: str
    action: str
    source: str
    rule: str | None = None
    account: str
    at: datetime

    def to_json(self) -> dict:
        return {
            "message_id": self.message_id,
            "status": self.status,
            "category": self.category,
            "action": self.action,
            "source": self.source,
            "rule": self.rule,
            "account": self.account,
            "at": to_iso(self.at),
        }

    @classmethod
    def from_json(cls, raw: dict) -> LedgerEntry:
        return cls(
            message_id=raw["message_id"],
            status=raw["status"],
            category=raw["category"],
            action=raw["action"],
            source=raw["source"],
            rule=raw.get("rule"),
            account=raw["account"],
            at=from_iso(raw["at"]) or utc_now(),
        )


class DigestItem(_Model):
    account: str
    sender: str
    subject: str
    summary: str
    category: str
    received_at: datetime
    queued_at: datetime

    def to_json(self) -> dict:
        return {
            "account": self.account,
            "sender": self.sender,
            "subject": self.subject,
            "summary": self.summary,
            "category": self.category,
            "received_at": to_iso(self.received_at),
            "queued_at": to_iso(self.queued_at),
        }

    @classmethod
    def from_json(cls, raw: dict) -> DigestItem:
        return cls(
            account=raw["account"],
            sender=raw["sender"],
            subject=raw["subject"],
            summary=raw["summary"],
            category=raw["category"],
            received_at=from_iso(raw["received_at"]) or utc_now(),
            queued_at=from_iso(raw["queued_at"]) or utc_now(),
        )


class StateDocument(_Model):
    version: Literal[1] = 1
    accounts: dict[str, AccountStateDocument] = Field(default_factory=dict)
    ledger: dict[str, LedgerEntry] = Field(default_factory=dict)
    message_index: dict[str, str] = Field(default_factory=dict)
    digest_queue: list[DigestItem] = Field(default_factory=list)
    digest_last_sent_date: str | None = None
    last_run: datetime | None = None
    revision: int = 0

    def account(self, account_id: str, connector: str) -> AccountStateDocument:
        if account_id not in self.accounts:
            self.accounts[account_id] = AccountStateDocument(connector=connector)
        return self.accounts[account_id]

    def index_key(self, account_id: str, message_id: str) -> str:
        return f"{account_id}|{message_id}"

    def already_processed(self, account_id: str, message_id: str) -> bool:
        return self.index_key(account_id, message_id) in self.message_index

    def record(self, key: str, entry: LedgerEntry) -> None:
        self.ledger[key] = entry
        self.message_index[self.index_key(entry.account, entry.message_id)] = key

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "accounts": {name: value.to_json() for name, value in self.accounts.items()},
            "ledger": {key: value.to_json() for key, value in self.ledger.items()},
            "message_index": dict(self.message_index),
            "digest_queue": [item.to_json() for item in self.digest_queue],
            "digest_last_sent_date": self.digest_last_sent_date,
            "last_run": to_iso(self.last_run),
            "revision": self.revision,
        }

    @classmethod
    def from_json(cls, raw: dict) -> StateDocument:
        return cls(
            version=raw.get("version", 1),
            accounts={
                name: AccountStateDocument.from_json(item)
                for name, item in (raw.get("accounts") or {}).items()
            },
            ledger={
                key: LedgerEntry.from_json(item)
                for key, item in (raw.get("ledger") or {}).items()
            },
            message_index=dict(raw.get("message_index") or {}),
            digest_queue=[
                DigestItem.from_json(item) for item in (raw.get("digest_queue") or [])
            ],
            digest_last_sent_date=raw.get("digest_last_sent_date"),
            last_run=from_iso(raw.get("last_run")),
            revision=int(raw.get("revision", 0)),
        )
