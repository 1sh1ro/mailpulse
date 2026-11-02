from __future__ import annotations

from ..config import FilteringConfig
from ..models import (
    Action,
    Category,
    Classification,
    Decision,
    DecisionSource,
    Message,
)


def _matches(entries: tuple[str, ...], email: str, domain: str) -> str | None:
    for entry in entries:
        if "@" in entry:
            if entry == email:
                return entry
        else:
            if domain == entry or domain.endswith("." + entry):
                return entry
    return None


class RuleEngine:
    def __init__(self, config: FilteringConfig) -> None:
        self._config = config

    def prefilter(self, message: Message) -> Decision | None:
        if not self._config.enabled:
            return None
        allowlist = self._config.allowlist
        if allowlist:
            hit = _matches(allowlist.entries, message.sender_email, message.sender_domain)
            if hit:
                return Decision(
                    action=Action.NOTIFY,
                    source=DecisionSource.ALLOWLIST,
                    category=Category.NORMAL,
                    summary=None,
                    needs_action=False,
                    deadline=None,
                    rule_name=None,
                    matched_pattern=hit,
                )
        blocklist = self._config.blocklist
        if blocklist:
            hit = _matches(blocklist.entries, message.sender_email, message.sender_domain)
            if hit:
                return Decision(
                    action=Action.MUTE,
                    source=DecisionSource.BLOCKLIST,
                    category=Category.PROMOTIONAL,
                    summary=None,
                    needs_action=False,
                    deadline=None,
                    rule_name=None,
                    matched_pattern=hit,
                )
        subject = message.subject.lower()
        body = message.body_text.lower()
        for rule in self._config.keywords:
            for keyword in rule.keywords:
                if keyword in subject or keyword in body:
                    return Decision(
                        action=Action(rule.action),
                        source=DecisionSource.KEYWORD,
                        category=Category.PROMOTIONAL,
                        summary=None,
                        needs_action=False,
                        deadline=None,
                        rule_name=None,
                        matched_pattern=keyword,
                    )
        return None

    def resolve(self, message: Message, classification: Classification) -> Decision:
        rule_name = classification.rule_name
        if rule_name and rule_name != "none":
            rule = self._config.rule_by_name(rule_name)
            if rule is None:
                raise ValueError(f"分类结果引用了未定义的规则：{rule_name}")
            return Decision(
                action=Action(rule.action),
                source=DecisionSource.AI_RULE,
                category=classification.category,
                summary=classification.summary,
                needs_action=classification.needs_action,
                deadline=classification.deadline,
                rule_name=rule_name,
                matched_pattern=None,
            )
        if classification.category is Category.PROMOTIONAL:
            action = Action(self._config.default_block_action)
            source = DecisionSource.AI_DEFAULT
        elif classification.category is Category.IMPORTANT:
            action = Action.NOTIFY
            source = DecisionSource.AI_DEFAULT
        else:
            action = Action.DIGEST
            source = DecisionSource.AI_DEFAULT
        return Decision(
            action=action,
            source=source,
            category=classification.category,
            summary=classification.summary,
            needs_action=classification.needs_action,
            deadline=classification.deadline,
            rule_name=None,
            matched_pattern=None,
        )

    def unfiltered(self) -> Decision:
        return Decision(
            action=Action.DIGEST,
            source=DecisionSource.CONFIG_DEFAULT,
            category=Category.NORMAL,
            summary=None,
            needs_action=False,
            deadline=None,
            rule_name=None,
            matched_pattern=None,
        )
