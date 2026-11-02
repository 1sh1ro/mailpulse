from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mailpulse.config import (
    AiRule,
    FilteringConfig,
    SourceList,
)
from mailpulse.models import Action, Address, Category, Classification, Message
from mailpulse.rules.engine import RuleEngine


def make_message(sender: str, subject: str = "hello", body: str = "body") -> Message:
    local, _, domain = sender.partition("@")
    return Message(
        account_id="personal",
        folder="INBOX",
        uid=1,
        message_id="<m1@example>",
        sender=Address(name=local, email=sender),
        subject=subject,
        received_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        body_text=body,
    )


def filtering(**kwargs) -> FilteringConfig:
    return FilteringConfig(**kwargs)


def test_allowlist_beats_keyword():
    engine = RuleEngine(
        filtering(
            allowlist=SourceList(senders=["important@example.com"]),
            keywords=[{"keywords": ["优惠"], "action": "mute"}],
        )
    )
    decision = engine.prefilter(make_message("important@example.com", subject="限时优惠"))
    assert decision is not None
    assert decision.action is Action.NOTIFY


def test_domain_matches_subdomain():
    engine = RuleEngine(filtering(blocklist=SourceList(domains=["example.org"])))
    decision = engine.prefilter(make_message("promo@news.example.org"))
    assert decision is not None
    assert decision.action is Action.MUTE


def test_domain_does_not_match_suffix_trick():
    engine = RuleEngine(filtering(blocklist=SourceList(domains=["example.org"])))
    assert engine.prefilter(make_message("promo@notexample.org")) is None


def test_keyword_body_match():
    engine = RuleEngine(filtering(keywords=[{"keywords": ["退订"], "action": "mute"}]))
    decision = engine.prefilter(make_message("a@b.com", subject="通知", body="点击此处退订"))
    assert decision is not None
    assert decision.source.value == "keyword"


def test_no_match_returns_none():
    engine = RuleEngine(filtering(blocklist=SourceList(senders=["a@b.com"])))
    assert engine.prefilter(make_message("c@d.com")) is None


def test_resolve_rule_action():
    engine = RuleEngine(
        filtering(
            ai_rules=[AiRule(name="important-email", instruction="find urgent", action="notify")]
        )
    )
    classification = Classification(
        rule_name="important-email",
        category=Category.IMPORTANT,
        confidence=0.9,
        summary="需要回复",
        needs_action=True,
        deadline=None,
    )
    decision = engine.resolve(make_message("a@b.com"), classification)
    assert decision.action is Action.NOTIFY
    assert decision.source.value == "ai_rule"


def test_resolve_promotional_default_mute():
    engine = RuleEngine(filtering())
    classification = Classification(
        rule_name="none",
        category=Category.PROMOTIONAL,
        confidence=0.8,
        summary="促销",
        needs_action=False,
        deadline=None,
    )
    decision = engine.resolve(make_message("a@b.com"), classification)
    assert decision.action is Action.MUTE


def test_resolve_normal_goes_to_digest():
    engine = RuleEngine(filtering())
    classification = Classification(
        rule_name="none",
        category=Category.NORMAL,
        confidence=0.5,
        summary="普通",
        needs_action=False,
        deadline=None,
    )
    decision = engine.resolve(make_message("a@b.com"), classification)
    assert decision.action is Action.DIGEST


def test_resolve_unknown_rule_raises():
    engine = RuleEngine(filtering())
    classification = Classification(
        rule_name="ghost",
        category=Category.NORMAL,
        confidence=0.5,
        summary="x",
        needs_action=False,
        deadline=None,
    )
    with pytest.raises(ValueError):
        engine.resolve(make_message("a@b.com"), classification)


def test_filtering_disabled_returns_unfiltered():
    engine = RuleEngine(filtering(enabled=False, blocklist=SourceList(senders=["a@b.com"])))
    assert engine.prefilter(make_message("a@b.com")) is None
    assert engine.unfiltered().action is Action.DIGEST
