from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .ai.classifier import AiError, Classifier
from .config import Config, GmailAccount, ImapAccount
from .connectors.base import Connector, ConnectorError, FetchResult
from .connectors.gmail import GmailConnector
from .connectors.imap import ImapConnector
from .models import (
    AccountRunResult,
    Action,
    Category,
    Decision,
    Message,
    MessageStatus,
)
from .notify.render import render_digest, render_notification
from .notify.telegram import NotifyError, TelegramNotifier
from .progress import advance_imap_cursor, digest_due
from .rules.engine import RuleEngine
from .state.models import (
    DigestItem,
    LedgerEntry,
    PendingItem,
    StateDocument,
    SweepWindow,
    utc_now,
)
from .state.store import StateStore, build_store

logger = logging.getLogger("mailpulse")


class RunError(Exception):
    pass


class Runner:
    def __init__(
        self,
        config: Config,
        store: StateStore | None = None,
        notifier: TelegramNotifier | None = None,
        classifier: Classifier | None = None,
        state: StateDocument | None = None,
    ) -> None:
        self._config = config
        self._store = store or build_store(config.storage)
        self._engine = RuleEngine(config.filtering)
        self._state = state
        self._notifier = notifier
        self._classifier = classifier

    def run(self, mode: str = "run", force: bool = False) -> list[AccountRunResult]:
        state = self._load_state()
        results: list[AccountRunResult] = []
        should_scan = mode == "init" or force or self._interval_elapsed(state)
        if not should_scan:
            logger.info(
                "距离上次检查不足 %d 分钟，本轮跳过邮箱检查",
                self._config.sync.check_interval_minutes,
            )
        accounts = self._config.accounts if should_scan else ()
        for account in accounts:
            result = AccountRunResult(account_id=account.id)
            results.append(result)
            document = state.account(account.id, account.connector)
            try:
                self._run_account(account, state, document, result, mode)
            except ConnectorError as exc:
                result.errors.append(str(exc))
                document.last_error = str(exc)
                logger.error("账户 %s 处理失败：%s", account.id, exc)
        if should_scan:
            self._prune(state)
            state.last_run = None if mode == "init" else utc_now()
            self._store.save(state)
        self._send_digest_if_due(state)
        self._close()
        return results

    def _interval_elapsed(self, state: StateDocument) -> bool:
        if state.last_run is None:
            return True
        interval = self._config.sync.check_interval_minutes * 60
        elapsed = (utc_now() - state.last_run).total_seconds()
        return elapsed >= interval * 0.9

    def _load_state(self) -> StateDocument:
        if self._state is not None:
            return self._state
        return self._store.load()

    def _close(self) -> None:
        if self._classifier is not None:
            self._classifier.close()
        if self._notifier is not None:
            self._notifier.close()

    def _ensure_classifier(self) -> Classifier:
        if self._classifier is None:
            self._classifier = Classifier(self._config.ai, self._config.filtering.ai_rules)
        return self._classifier

    def _ensure_notifier(self) -> TelegramNotifier:
        if self._notifier is None:
            self._notifier = TelegramNotifier(self._config.notifications.telegram)
        return self._notifier

    def _build_connector(self, account) -> Connector:
        mark_as_read = self._config.sync.mark_as_read
        batch = self._config.sync.batch_size
        if isinstance(account, ImapAccount):
            return ImapConnector(
                account,
                _env(account.username_env),
                _env(account.password_env),
                mark_as_read,
                batch,
            )
        if isinstance(account, GmailAccount):
            return GmailConnector(
                account,
                _env(account.client_id_env),
                _env(account.client_secret_env),
                _env(account.refresh_token_env),
                mark_as_read,
                batch,
            )
        raise RunError(f"未知的账户类型：{account!r}")

    def _run_account(self, account, state, document, result, mode: str) -> None:
        connector = self._build_connector(account)
        connector.connect()
        try:
            folder_state = document.folder(connector.folder)
            if folder_state.status == "initializing":
                self._initialize(account, connector, state, document, folder_state)
            else:
                self._verify_uidvalidity(account, connector, state, folder_state)
            if folder_state.sweep is not None:
                self._run_sweep(account, connector, state, folder_state, result)
            if mode == "init":
                document.last_run = utc_now()
                document.last_error = None
                self._store.save(state)
                return
            self._run_new(account, connector, state, folder_state, result)
            document.last_run = utc_now()
            document.last_error = None
            self._store.save(state)
        finally:
            connector.close()

    def _initialize(self, account, connector, state, document, folder_state) -> None:
        if folder_state.init_started_at is None:
            folder_state.init_started_at = utc_now()
            self._store.save(state)
            logger.info("账户 %s 开始初始化，记录时间边界", account.id)
        boundary = connector.read_boundary(connector.folder)
        folder_state.uidvalidity = boundary.uidvalidity
        folder_state.cursor_history_id = boundary.history_id
        folder_state.cursor_uid = boundary.max_uid if connector.kind == "imap" else None
        folder_state.status = "ready"
        folder_state.ready_at = utc_now()
        folder_state.sweep = SweepWindow(
            since=folder_state.init_started_at,
            max_uid=boundary.max_uid,
            max_history_id=boundary.history_id,
        )
        self._store.save(state)
        logger.info(
            "账户 %s 初始化完成，边界 uidvalidity=%s max_uid=%s history_id=%s",
            account.id,
            boundary.uidvalidity,
            boundary.max_uid,
            boundary.history_id,
        )

    def _verify_uidvalidity(self, account, connector, state, folder_state) -> None:
        if connector.kind != "imap":
            return
        boundary = connector.read_boundary(connector.folder)
        if folder_state.uidvalidity is None:
            folder_state.uidvalidity = boundary.uidvalidity
            folder_state.cursor_uid = boundary.max_uid
            self._store.save(state)
            return
        if boundary.uidvalidity == folder_state.uidvalidity:
            return
        logger.warning(
            "账户 %s 的 UIDVALIDITY 从 %s 变为 %s，进入安全恢复",
            account.id,
            folder_state.uidvalidity,
            boundary.uidvalidity,
        )
        folder_state.pending.clear()
        folder_state.uidvalidity = boundary.uidvalidity
        folder_state.cursor_uid = boundary.max_uid
        folder_state.sweep = SweepWindow(
            since=folder_state.last_success_at or (utc_now() - timedelta(days=1)),
            max_uid=boundary.max_uid,
        )
        self._store.save(state)

    def _run_sweep(self, account, connector, state, folder_state, result) -> None:
        window = folder_state.sweep
        if window is None:
            return
        messages = connector.sweep_since(
            connector.folder, window.since, window.max_uid, window.max_history_id
        )
        messages = [item for item in messages if item.received_at >= window.since]
        logger.info("账户 %s 补扫到 %d 封邮件", account.id, len(messages))
        self._process_messages(account, connector, state, folder_state, messages, result)
        folder_state.sweep = None
        self._store.save(state)

    def _run_new(self, account, connector, state, folder_state, result) -> None:
        fetch: FetchResult = connector.fetch_new(
            connector.folder, folder_state.cursor_uid, folder_state.cursor_history_id
        )
        result.fetched = len(fetch.messages)
        if fetch.messages:
            logger.info("账户 %s 拉取到 %d 封新邮件", account.id, len(fetch.messages))
            self._process_messages(account, connector, state, folder_state, fetch.messages, result)
        if connector.kind == "imap":
            if fetch.uidvalidity is not None:
                folder_state.uidvalidity = fetch.uidvalidity
            self._advance_imap_cursor(folder_state, fetch.messages)
        elif not folder_state.pending and fetch.history_id:
            folder_state.cursor_history_id = fetch.history_id
        if not result.errors:
            folder_state.last_success_at = utc_now()

    def _advance_imap_cursor(self, folder_state, messages: list[Message]) -> None:
        uids = [item.uid for item in messages if item.uid is not None]
        folder_state.cursor_uid = advance_imap_cursor(
            folder_state.cursor_uid, uids, set(folder_state.pending)
        )

    def _process_messages(
        self, account, connector, state, folder_state, messages: list[Message], result
    ) -> None:
        undecided: list[Message] = []
        for message in messages:
            if state.already_processed(account.id, message.message_id):
                continue
            decision = self._engine.prefilter(message)
            if decision is not None:
                self._apply(account, connector, state, folder_state, message, decision, result)
                continue
            undecided.append(message)
        if not undecided:
            self._store.save(state)
            return
        if not self._config.filtering.enabled:
            for message in undecided:
                self._apply(
                    account,
                    connector,
                    state,
                    folder_state,
                    message,
                    self._engine.unfiltered(),
                    result,
                )
            self._store.save(state)
            return
        outcomes = self._ensure_classifier().classify_many(undecided)
        for message, outcome in zip(undecided, outcomes):
            if isinstance(outcome, Exception):
                self._record_failure(state, folder_state, message, str(outcome), result)
                continue
            decision = self._engine.resolve(message, outcome)
            self._apply(account, connector, state, folder_state, message, decision, result)
        self._store.save(state)

    def _apply(self, account, connector, state, folder_state, message, decision: Decision, result) -> None:
        try:
            status = self._execute(state, message, decision, result)
        except NotifyError as exc:
            self._record_failure(state, folder_state, message, f"通知失败：{exc}", result)
            return
        entry = LedgerEntry(
            message_id=message.message_id,
            status=status,
            category=decision.category.value,
            action=decision.action.value,
            source=decision.source.value,
            rule=decision.rule_name,
            account=account.id,
            at=utc_now(),
        )
        state.record(message.key, entry)
        folder_state.pending.pop(_pending_key(message), None)
        if self._config.sync.mark_as_read and self._config.sync.mode == "new_only":
            connector.mark_read(connector.folder, message)
        logger.info(
            "邮件 %s 判定为 %s，来源 %s，动作 %s",
            message.key,
            decision.category.value,
            decision.source.value,
            decision.action.value,
        )

    def _execute(self, state, message: Message, decision: Decision, result: AccountRunResult) -> str:
        if decision.action is Action.NOTIFY:
            self._ensure_notifier().send(
                render_notification(message, decision, self._config.digest)
            )
            result.notified += 1
            return MessageStatus.NOTIFIED.value
        if decision.action is Action.DIGEST:
            state.digest_queue.append(
                DigestItem(
                    account=message.account_id,
                    sender=message.sender_email,
                    subject=message.subject,
                    summary=decision.summary or "",
                    category=decision.category.value,
                    received_at=message.received_at,
                    queued_at=utc_now(),
                )
            )
            result.digested += 1
            return MessageStatus.DIGESTED.value
        result.muted += 1
        return MessageStatus.MUTED.value

    def _record_failure(self, state, folder_state, message, error, result) -> None:
        key = _pending_key(message)
        item = folder_state.pending.get(key)
        attempts = (item.attempts if item else 0) + 1
        if attempts >= self._config.sync.max_attempts:
            logger.error("邮件 %s 连续失败 %d 次，标记为失败：%s", message.key, attempts, error)
            state.record(
                message.key,
                LedgerEntry(
                    message_id=message.message_id,
                    status=MessageStatus.FAILED.value,
                    category=Category.NORMAL.value,
                    action="none",
                    source="failure",
                    rule=None,
                    account=message.account_id,
                    at=utc_now(),
                ),
            )
            folder_state.pending.pop(key, None)
            result.failed += 1
        else:
            folder_state.pending[key] = PendingItem(
                message_id=message.message_id,
                attempts=attempts,
                last_error=error[:500],
                first_seen=item.first_seen if item else utc_now(),
            )
            logger.warning("邮件 %s 第 %d 次处理失败：%s", message.key, attempts, error)
        self._store.save(state)

    def _prune(self, state: StateDocument) -> None:
        now = utc_now()
        ledger_cutoff = now - timedelta(days=self._config.storage.ledger_retention_days)
        expired = [key for key, entry in state.ledger.items() if entry.at < ledger_cutoff]
        for key in expired:
            entry = state.ledger.pop(key)
            state.message_index.pop(state.index_key(entry.account, entry.message_id), None)
        queue_cutoff = now - timedelta(days=self._config.storage.digest_queue_retention_days)
        state.digest_queue = [item for item in state.digest_queue if item.queued_at >= queue_cutoff]

    def _send_digest_if_due(self, state: StateDocument) -> None:
        config = self._config.digest
        if not config.enabled:
            return
        zone = ZoneInfo(config.timezone)
        local_now = utc_now().astimezone(zone)
        if not digest_due(local_now, config, state.digest_last_sent_date):
            return
        today = local_now.strftime("%Y-%m-%d")
        failures = [
            f"{entry.account} {entry.message_id}"
            for entry in state.ledger.values()
            if entry.status == MessageStatus.FAILED.value
        ]
        account_errors = [
            f"{name} {document.last_error}"
            for name, document in state.accounts.items()
            if document.last_error
        ]
        if not state.digest_queue:
            state.digest_last_sent_date = today
            self._store.save(state)
            return
        chunks = render_digest(
            list(state.digest_queue), config, failures or None, account_errors or None
        )
        notifier = self._ensure_notifier()
        for chunk in chunks:
            notifier.send(chunk)
        state.digest_queue.clear()
        state.digest_last_sent_date = today
        self._store.save(state)
        logger.info("已发送每日摘要，共 %d 条消息", len(chunks))


def _pending_key(message: Message) -> str:
    if message.uid is not None:
        return str(message.uid)
    return message.gmail_id or message.message_id


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RunError(f"缺少环境变量 {name}")
    return value
