from __future__ import annotations

import re
from datetime import time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .models import Action


class ConfigError(Exception):
    pass


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_SENDER_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_id(value: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(f"标识 {value!r} 不合法，只能使用字母、数字、连字符和下划线")
    return value


def _check_env(value: str) -> str:
    if not _ENV_RE.match(value):
        raise ValueError(f"环境变量名 {value!r} 不合法，必须由大写字母、数字和下划线组成")
    return value


def _normalize_source(value: str) -> str:
    item = value.strip().lower()
    for prefix in ("*@", "@", "*."):
        if item.startswith(prefix):
            item = item[len(prefix) :]
            break
    if not item:
        raise ValueError("黑白名单条目不能为空")
    if "@" in item:
        if not _SENDER_RE.match(item):
            raise ValueError(f"发件人地址 {value!r} 不合法")
        return item
    if not _DOMAIN_RE.match(item):
        raise ValueError(f"域名 {value!r} 不合法")
    return item


def _normalize_keyword(value: str) -> str:
    item = value.strip().lower()
    if not item:
        raise ValueError("关键词不能为空")
    return item


def _check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"时区 {value!r} 不是合法的 IANA 名称") from exc
    return value


class ImapAccount(_Strict):
    connector: Literal["imap"]
    id: str
    host: str
    port: int = Field(default=993, ge=1, le=65535)
    username_env: str
    password_env: str
    folder: str = "INBOX"
    use_ssl: bool = True
    tls_ca_file: str | None = None
    timeout_seconds: int = Field(default=60, ge=5, le=300)

    @field_validator("id")
    @classmethod
    def _id_valid(cls, value: str) -> str:
        return _check_id(value)

    @field_validator("username_env", "password_env")
    @classmethod
    def _env_valid(cls, value: str) -> str:
        return _check_env(value)

    @field_validator("host", "folder")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段不能为空")
        return value.strip()


class GmailAccount(_Strict):
    connector: Literal["gmail"]
    id: str
    client_id_env: str
    client_secret_env: str
    refresh_token_env: str
    query: str = "in:inbox"
    timeout_seconds: int = Field(default=60, ge=5, le=300)

    @field_validator("id")
    @classmethod
    def _id_valid(cls, value: str) -> str:
        return _check_id(value)

    @field_validator("client_id_env", "client_secret_env", "refresh_token_env")
    @classmethod
    def _env_valid(cls, value: str) -> str:
        return _check_env(value)


class AiConfig(_Strict):
    provider: Literal["openai_compatible"]
    base_url_env: str
    api_key_env: str
    model: str
    language: str = "zh-CN"
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    max_body_chars: int = Field(default=6000, ge=500, le=20000)
    concurrency: int = Field(default=4, ge=1, le=16)
    max_attempts: int = Field(default=3, ge=1, le=10)

    @field_validator("base_url_env", "api_key_env")
    @classmethod
    def _env_valid(cls, value: str) -> str:
        return _check_env(value)

    @field_validator("model")
    @classmethod
    def _model_valid(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ai.model 不能为空")
        return value.strip()


class SourceList(_Strict):
    senders: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()

    @field_validator("senders", "domains")
    @classmethod
    def _normalize(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_normalize_source(item) for item in value)
        duplicates = {item for item in normalized if normalized.count(item) > 1}
        if duplicates:
            raise ValueError(f"重复条目：{', '.join(sorted(duplicates))}")
        return normalized

    @model_validator(mode="after")
    def _not_empty(self) -> SourceList:
        if not self.senders and not self.domains:
            raise ValueError("黑白名单至少要有一个发件人或域名")
        return self

    @property
    def entries(self) -> tuple[str, ...]:
        return self.senders + self.domains


class KeywordRule(_Strict):
    keywords: tuple[str, ...]
    action: Literal["mute", "digest"] = "mute"

    @field_validator("keywords")
    @classmethod
    def _normalize(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_normalize_keyword(item) for item in value)
        if not normalized:
            raise ValueError("关键词规则至少要有一个关键词")
        duplicates = {item for item in normalized if normalized.count(item) > 1}
        if duplicates:
            raise ValueError(f"重复关键词：{', '.join(sorted(duplicates))}")
        return normalized


class AiRule(_Strict):
    name: str
    instruction: str
    action: Literal["notify", "mute"]

    @field_validator("name")
    @classmethod
    def _name_valid(cls, value: str) -> str:
        return _check_id(value)

    @field_validator("instruction")
    @classmethod
    def _instruction_valid(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ai_rules.instruction 不能为空")
        return " ".join(value.split())


class FilteringConfig(_Strict):
    enabled: bool = True
    default_block_action: Literal["mute"] = "mute"
    allowlist: SourceList | None = None
    blocklist: SourceList | None = None
    keywords: tuple[KeywordRule, ...] = ()
    ai_rules: tuple[AiRule, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> FilteringConfig:
        names = [rule.name for rule in self.ai_rules]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"AI 规则名称重复：{', '.join(sorted(duplicates))}")
        if self.allowlist and self.blocklist:
            overlap = set(self.allowlist.entries) & set(self.blocklist.entries)
            if overlap:
                raise ValueError(f"白名单与黑名单存在相同条目：{', '.join(sorted(overlap))}")
        return self

    def rule_by_name(self, name: str) -> AiRule | None:
        for rule in self.ai_rules:
            if rule.name == name:
                return rule
        return None


class TelegramConfig(_Strict):
    enabled: bool = False
    token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    disable_notification: bool = False

    @field_validator("token_env", "chat_id_env")
    @classmethod
    def _env_valid(cls, value: str) -> str:
        return _check_env(value)


class NotificationsConfig(_Strict):
    telegram: TelegramConfig = TelegramConfig()


class DigestConfig(_Strict):
    enabled: bool = False
    timezone: str = "UTC"
    time: str = "09:00"
    max_message_chars: int = Field(default=3800, ge=500, le=4096)

    @field_validator("timezone")
    @classmethod
    def _timezone_valid(cls, value: str) -> str:
        return _check_timezone(value)

    @field_validator("time")
    @classmethod
    def _time_valid(cls, value: str) -> str:
        if not _TIME_RE.match(value):
            raise ValueError(f"摘要时刻 {value!r} 必须是 HH:MM 格式")
        return value

    @property
    def at(self) -> time:
        hour, minute = self.time.split(":")
        return time(int(hour), int(minute))


class SyncConfig(_Strict):
    mode: Literal["new_only"] = "new_only"
    check_interval_minutes: int = Field(default=30, ge=5, le=1440)
    mark_as_read: bool = False
    batch_size: int = Field(default=50, ge=1, le=500)
    max_attempts: int = Field(default=5, ge=1, le=20)
    retry_scan_days: int = Field(default=2, ge=1, le=30)


class StorageConfig(_Strict):
    backend: Literal["github_private_state", "local_file"] = "github_private_state"
    repo: str | None = None
    path: str = "mailpulse-state/state.json"
    token_env: str = "STATE_GITHUB_TOKEN"
    branch: str = "main"
    local_path: str = "state.json"
    persist_email_body: Literal[False] = False
    ledger_retention_days: int = Field(default=30, ge=1, le=365)
    digest_queue_retention_days: int = Field(default=7, ge=1, le=90)

    @field_validator("token_env")
    @classmethod
    def _env_valid(cls, value: str) -> str:
        return _check_env(value)

    @field_validator("path")
    @classmethod
    def _path_valid(cls, value: str) -> str:
        if not value.strip() or value.startswith("/") or ".." in value.split("/"):
            raise ValueError("storage.path 必须是仓库内的相对路径")
        return value.strip()

    @model_validator(mode="after")
    def _repo_required(self) -> StorageConfig:
        if self.backend == "github_private_state":
            if not self.repo:
                raise ValueError("storage.repo 在 github_private_state 后端下必填")
            if not re.match(r"^[^/\s]+/[^/\s]+$", self.repo):
                raise ValueError("storage.repo 必须是 owner/name 形式")
        return self


class Config(_Strict):
    version: Literal[1]
    accounts: tuple[
        Annotated[ImapAccount | GmailAccount, Field(discriminator="connector")], ...
    ]
    ai: AiConfig
    filtering: FilteringConfig
    notifications: NotificationsConfig = NotificationsConfig()
    digest: DigestConfig = DigestConfig()
    sync: SyncConfig = SyncConfig()
    storage: StorageConfig

    @model_validator(mode="after")
    def _ids_unique(self) -> Config:
        if not self.accounts:
            raise ValueError("accounts 至少需要一个账户")
        ids = [account.id for account in self.accounts]
        duplicates = {item for item in ids if ids.count(item) > 1}
        if duplicates:
            raise ValueError(f"账户标识重复：{', '.join(sorted(duplicates))}")
        return self

    @model_validator(mode="after")
    def _output_channel(self) -> Config:
        if not self.notifications.telegram.enabled and not self.digest.enabled:
            raise ValueError("Telegram 推送与每日摘要都关闭时系统没有任何输出渠道")
        if self.digest.enabled and not self.notifications.telegram.enabled:
            raise ValueError("每日摘要通过 Telegram 发送，需要开启 notifications.telegram")
        return self

    def default_action(self) -> Action:
        return Action.MUTE


def load_config(path: str) -> Config:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在：{path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件不是合法的 YAML：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是映射结构")
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"配置校验失败：\n{exc}") from exc
