from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ..models import Message


class ConnectorError(Exception):
    pass


@dataclass(slots=True)
class Boundary:
    uidvalidity: int | None = None
    max_uid: int | None = None
    history_id: str | None = None


@dataclass(slots=True)
class FetchResult:
    messages: list[Message] = field(default_factory=list)
    uidvalidity: int | None = None
    max_uid: int | None = None
    history_id: str | None = None


class Connector(Protocol):
    kind: str
    account_id: str
    folder: str

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def read_boundary(self, folder: str) -> Boundary: ...

    def fetch_new(
        self, folder: str, cursor_uid: int | None, cursor_history_id: str | None
    ) -> FetchResult: ...

    def sweep_since(
        self, folder: str, since: datetime, max_uid: int | None, max_history_id: str | None
    ) -> list[Message]: ...

    def mark_read(self, folder: str, uid: int) -> None: ...
