from __future__ import annotations

from email.message import EmailMessage
from email import policy

from mailpulse.parsing import (
    extract_body_text,
    extract_message_id,
    parse_date,
    parse_message_bytes,
    parse_recipients,
    parse_sender,
)


def build_message(bodies) -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "Alice Example <alice@example.com>"
    message["To"] = "bob@example.com"
    message["Cc"] = "carol@example.com"
    message["Subject"] = "周会纪要"
    message["Date"] = "Mon, 02 Nov 2026 09:30:00 +0800"
    message["Message-Id"] = "<abc123@example.com>"
    for content_type, content in bodies:
        message.set_content(content) if content_type == "text/plain" else message.add_alternative(content, subtype="html")
    return message.as_bytes()


def test_parse_sender_and_recipients():
    parsed = parse_message_bytes(build_message([("text/plain", "hello")]))
    sender = parse_sender(parsed)
    assert sender.name == "Alice Example"
    assert sender.email == "alice@example.com"
    assert parse_recipients(parsed) == ["bob@example.com", "carol@example.com"]


def test_message_id_and_date():
    parsed = parse_message_bytes(build_message([("text/plain", "hello")]))
    assert extract_message_id(parsed, "<fallback>") == "<abc123@example.com>"
    assert parse_date(parsed).isoformat() == "2026-11-02T01:30:00+00:00"


def test_missing_message_id_uses_fallback():
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "a@b.com"
    message["Subject"] = "x"
    parsed = parse_message_bytes(message.as_bytes())
    assert extract_message_id(parsed, "<fallback>") == "<fallback>"


def test_html_only_body_is_flattened():
    html = "<html><body><p>你好</p><p>这是正文</p><script>bad()</script></body></html>"
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "a@b.com"
    message["Subject"] = "x"
    message.set_content(html, subtype="html")
    parsed = parse_message_bytes(message.as_bytes())
    body = extract_body_text(parsed)
    assert "你好" in body
    assert "这是正文" in body
    assert "bad()" not in body


def test_multipart_prefers_plain():
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "a@b.com"
    message["Subject"] = "x"
    message.set_content("纯文本内容")
    message.add_alternative("<p>HTML 内容</p>", subtype="html")
    parsed = parse_message_bytes(message.as_bytes())
    body = extract_body_text(parsed)
    assert body == "纯文本内容"


def test_attachment_ignored():
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = "a@b.com"
    message["Subject"] = "x"
    message.set_content("正文")
    message.add_attachment(b"binary-data", maintype="application", subtype="pdf", filename="a.pdf")
    parsed = parse_message_bytes(message.as_bytes())
    assert extract_body_text(parsed) == "正文"
