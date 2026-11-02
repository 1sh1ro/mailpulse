from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from mailpulse.ai.classifier import Classifier
from mailpulse.config import AiConfig, AiRule
from mailpulse.models import Address, Category, Message

pytestmark = pytest.mark.integration


def _require() -> None:
    for name in ("AI_BASE_URL", "AI_API_KEY"):
        if not os.environ.get(name):
            pytest.skip(f"缺少环境变量 {name}")


def _config() -> AiConfig:
    return AiConfig(
        provider="openai_compatible",
        base_url_env="AI_BASE_URL",
        api_key_env="AI_API_KEY",
        model=os.environ.get("MAILPULSE_IT_AI_MODEL", "gpt-4o-mini"),
        concurrency=2,
    )


def _message(subject: str, body: str, sender: str) -> Message:
    return Message(
        account_id="it",
        folder="INBOX",
        uid=1,
        message_id="<probe@test>",
        sender=Address(name=sender.split("@")[0], email=sender),
        subject=subject,
        received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        body_text=body,
    )


def test_classifier_returns_schema_on_real_model():
    _require()
    rules = (
        AiRule(
            name="promotional-email",
            instruction="Identify promotional, marketing, coupon and unsolicited sales emails.",
            action="mute",
        ),
        AiRule(
            name="important-email",
            instruction="Identify emails requiring a reply, decision, code review, or action.",
            action="notify",
        ),
    )
    classifier = Classifier(_config(), rules)
    try:
        outcomes = classifier.classify_many(
            [
                _message("会员订阅限时五折", "开通会员立减 50%，点击立即抢购。", "promo@shop.example"),
                _message("请评审 PR #42", "请在周五之前完成代码评审并给出结论。", "dev@team.example"),
                _message("系统故障通知", "生产数据库连接中断，现已恢复，请确认一致性。", "ops@team.example"),
            ]
        )
    finally:
        classifier.close()
    assert len(outcomes) == 3
    for outcome in outcomes:
        assert not isinstance(outcome, Exception), outcome
        assert outcome.category in {Category.PROMOTIONAL, Category.IMPORTANT, Category.NORMAL}
        assert 0.0 <= outcome.confidence <= 1.0
        assert isinstance(outcome.summary, str)


def test_classifier_rejects_undefined_rule_name():
    _require()
    classifier = Classifier(_config(), ())
    try:
        with pytest.raises(Exception):
            classifier._parse('{"rule": "ghost-rule", "category": "normal", "confidence": 0.5, "summary": "x", "needs_action": false, "deadline": null}')
    finally:
        classifier.close()
