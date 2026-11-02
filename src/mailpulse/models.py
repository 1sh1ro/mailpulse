from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Action(StrEnum):
    NOTIFY = "notify"
    DIGEST = "digest"
    MUTE = "mute"


class Category(StrEnum):
    PROMOTIONAL = "promotional"
    IMPORTANT = "important"
    NORMAL = "normal"


class DecisionSource(StrEnum):
    ALLOWLIST = "allowlist"
    BLOCKLIST = "blocklist"
    KEYWORD = "keyword"
    AI_RULE = "ai_rule"
    AI_DEFAULT = "ai_default"
    CONFIG_DEFAULT = "config_default"


class MessageStatus(StrEnum):
    NOTIFIED = "notified"
    MUTED = "muted"
    DIGESTED = "digested"
    FAILED = "failed"


class AccountState(StrEnum):
    INITIALIZING = "initializing"
    READY = "ready"


@dataclass(slots=True)
class Address:
    name: str
    email: str


@dataclass(slots=True)
class Message:
    account_id: str
    folder: str
    uid: int | None
    message_id: str
    sender: Address
    subject: str
    received_at: datetime
    body_text: str
    to_addresses: list[str] = field(default_factory=list)
    gmail_id: str | None = None

    @property
    def key(self) -> str:
        if self.gmail_id:
            return f"{self.account_id}:gmail:{self.gmail_id}"
        return f"{self.account_id}:{self.folder}:{self.uid}"

    @property
    def sender_email(self) -> str:
        return self.sender.email.strip().lower()

    @property
    def sender_domain(self) -> str:
        _, _, domain = self.sender_email.partition("@")
        return domain


@dataclass(slots=True)
class Classification:
    rule_name: str
    category: Category
    confidence: float
    summary: str
    needs_action: bool
    deadline: str | None


@dataclass(slots=True)
class Decision:
    action: Action
    source: DecisionSource
    category: Category
    summary: str | None
    needs_action: bool
    deadline: str | None
    rule_name: str | None
    matched_pattern: str | None


@dataclass(slots=True)
class Boundary:
    uidvalidity: int
    max_uid: int


@dataclass(slots=True)
class AccountRunResult:
    account_id: str
    fetched: int = 0
    notified: int = 0
    muted: int = 0
    digested: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
