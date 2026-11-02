from __future__ import annotations

import re
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from .models import Address

_WHITESPACE = re.compile(r"[ \t\u00a0]+")
_NEWLINES = re.compile(r"\n{3,}")


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError):
        return value.strip()


def _decode_part(part: EmailMessage) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return text


def extract_body_text(message: EmailMessage) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain_parts.append(_decode_part(part))
        elif content_type == "text/html":
            html_parts.append(_decode_part(part))
    raw = "\n".join(plain_parts) if plain_parts else _html_to_text("\n".join(html_parts))
    collapsed = _WHITESPACE.sub(" ", raw)
    collapsed = _NEWLINES.sub("\n\n", collapsed)
    return collapsed.strip()


def parse_sender(message: EmailMessage) -> Address:
    header = message.get("From")
    if not header:
        return Address(name="", email="")
    decoded = decode_mime_header(str(header))
    addresses = getaddresses([decoded])
    if not addresses:
        return Address(name="", email="")
    name, email = addresses[0]
    return Address(name=decode_mime_header(name), email=(email or "").strip())


def parse_recipients(message: EmailMessage) -> list[str]:
    raw = []
    for header in ("To", "Cc"):
        value = message.get(header)
        if value:
            raw.append(str(value))
    addresses = getaddresses([decode_mime_header(item) for item in raw])
    return [email for _, email in addresses if email]


def parse_date(message: EmailMessage) -> datetime:
    header = message.get("Date")
    if header:
        try:
            parsed = parsedate_to_datetime(str(header))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)


def parse_message_bytes(raw: bytes) -> EmailMessage:
    return message_from_bytes(raw, policy=policy.default)


def extract_message_id(message: EmailMessage, fallback: str) -> str:
    header = message.get("Message-Id")
    if header:
        cleaned = str(header).strip()
        if cleaned:
            return cleaned
    return fallback
