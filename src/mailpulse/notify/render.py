from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import DigestConfig
from ..models import Action, Category, Decision, Message
from ..state.models import DigestItem


def _escape(value: str) -> str:
    return html.escape(value, quote=False)


def render_notification(
    message: Message,
    decision: Decision,
    digest_config: DigestConfig | None = None,
) -> str:
    lines = ["<b>重要邮件</b>", ""]
    lines.append(f"<b>账户</b>：{_escape(message.account_id)}")
    sender = _escape(message.sender.name) if message.sender.name else ""
    address = _escape(message.sender_email)
    lines.append(f"<b>发件人</b>：{sender + ' ' if sender else ''}&lt;{address}&gt;")
    lines.append(f"<b>主题</b>：{_escape(message.subject) or '（无主题）'}")
    if decision.summary:
        lines.append(f"<b>摘要</b>：{_escape(decision.summary)}")
    tags = [decision.category.value]
    if decision.needs_action:
        tags.append("需要处理")
    if decision.deadline:
        tags.append(f"截止 {decision.deadline}")
    lines.append(f"<b>分类</b>：{_escape(' / '.join(tags))}")
    lines.append(f"<b>时间</b>：{_format_time(message.received_at, digest_config)}")
    lines.append("")
    lines.append("<i>屏蔽此发件人，把下面这行加入配置的 blocklist.senders：</i>")
    lines.append(f"<code>{address}</code>")
    return "\n".join(lines)


def _format_time(value: datetime, digest_config: DigestConfig | None) -> str:
    if digest_config is None:
        return value.strftime("%Y-%m-%d %H:%M UTC")
    zone = ZoneInfo(digest_config.timezone)
    return value.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def render_digest(
    items: list[DigestItem],
    digest_config: DigestConfig,
    failures: list[str] | None = None,
    account_errors: list[str] | None = None,
) -> list[str]:
    zone = ZoneInfo(digest_config.timezone)
    today = datetime.now(zone).strftime("%Y-%m-%d")
    header = f"<b>每日摘要</b> {_escape(today)}"
    grouped: dict[str, list[DigestItem]] = {}
    for item in items:
        grouped.setdefault(item.account, []).append(item)
    lines = [header]
    for account, entries in grouped.items():
        lines.append("")
        lines.append(f"<b>{_escape(account)}</b>（{len(entries)} 封）")
        for entry in entries:
            summary = f" — {_escape(entry.summary)}" if entry.summary else ""
            lines.append(
                f"• {_escape(entry.sender)}｜{_escape(entry.subject) or '（无主题）'}{summary}"
            )
    if failures:
        lines.append("")
        lines.append("<b>处理失败</b>")
        for failure in failures:
            lines.append(f"• {_escape(failure)}")
    if account_errors:
        lines.append("")
        lines.append("<b>账户异常</b>")
        for error in account_errors:
            lines.append(f"• {_escape(error)}")
    return split_message("\n".join(lines), digest_config.max_message_chars)


def split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        pieces = [line]
        while len(pieces[-1]) > limit:
            head, tail = pieces[-1][:limit], pieces[-1][limit:]
            pieces[-1] = head
            pieces.append(tail)
        for piece in pieces:
            addition = len(piece) + 1
            if current_len + addition > limit and current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(piece)
            current_len += addition
    if current:
        chunks.append("\n".join(current))
    return chunks


def action_label(action: Action) -> str:
    return {
        Action.NOTIFY: "通知",
        Action.DIGEST: "摘要",
        Action.MUTE: "静默",
    }[action]


def category_label(category: Category) -> str:
    return {
        Category.PROMOTIONAL: "推广",
        Category.IMPORTANT: "重要",
        Category.NORMAL: "普通",
    }[category]
