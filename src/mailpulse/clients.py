from __future__ import annotations

import os

import httpx


def build_client(timeout: float) -> httpx.Client:
    _sanitize_no_proxy()
    return httpx.Client(timeout=timeout)


def _sanitize_no_proxy() -> None:
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(name)
        if not raw:
            continue
        items = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if item.startswith("[") and item.endswith("]"):
                item = item[1:-1]
            items.append(item)
        os.environ[name] = ",".join(items)
