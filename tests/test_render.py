from __future__ import annotations

from datetime import datetime, timezone

from mailpulse.config import DigestConfig
from mailpulse.models import Action, Address, Category, Decision, DecisionSource, Message
from mailpulse.notify.render import render_digest, render_notification, split_message
from mailpulse.state.models import DigestItem


def make_message() -> Message:
    return Message(
        account_id="personal",
        folder="INBOX",
        uid=3,
        message_id="<m@x>",
        sender=Address(name="广告部", email="promo@shop.example"),
        subject="会员订阅限时五折",
        received_at=datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc),
        body_text="开通会员立减 50%",
    )


def test_notification_escapes_html():
    decision = Decision(
        action=Action.NOTIFY,
        source=DecisionSource.AI_RULE,
        category=Category.IMPORTANT,
        summary="需要审批 <b>合同</b>",
        needs_action=True,
        deadline=None,
        rule_name="important-email",
        matched_pattern=None,
    )
    message = make_message()
    message.subject = "<script>alert(1)</script>"
    text = render_notification(message, decision)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "promo@shop.example" in text
    assert "blocklist.senders" in text


def test_split_message_respects_limit():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(60))
    chunks = split_message(text, 200)
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_digest_groups_by_account():
    config = DigestConfig(enabled=True, timezone="Asia/Shanghai", time="09:00")
    items = [
        DigestItem(
            account="personal",
            sender="a@b.com",
            subject="第一封",
            summary="摘要一",
            category="normal",
            received_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            queued_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
    ]
    chunks = render_digest(items, config, ["personal 失败一封"], ["work 授权失效"])
    text = "\n".join(chunks)
    assert "每日摘要" in text
    assert "第一封" in text
    assert "处理失败" in text
    assert "账户异常" in text
