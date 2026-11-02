from __future__ import annotations

import os
import time

import httpx

from ..clients import build_client
from ..config import TelegramConfig


class NotifyError(Exception):
    pass


class TelegramNotifier:
    def __init__(self, config: TelegramConfig) -> None:
        self._config = config
        token = os.environ.get(config.token_env, "")
        chat_id = os.environ.get(config.chat_id_env, "")
        if not token:
            raise NotifyError(f"缺少环境变量 {config.token_env}")
        if not chat_id:
            raise NotifyError(f"缺少环境变量 {config.chat_id_env}")
        self._chat_id = chat_id
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._client = build_client(config.timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def send(self, text: str) -> None:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": self._config.disable_notification,
        }
        last_error = ""
        for attempt in range(1, 4):
            try:
                response = self._client.post(self._url, json=payload)
            except httpx.HTTPError as exc:
                last_error = f"网络错误 {exc}"
                if attempt < 3:
                    time.sleep(2**attempt)
                    continue
                raise NotifyError(f"Telegram 发送失败：{last_error}") from exc
            if response.status_code == 429:
                retry_after = int(response.json().get("parameters", {}).get("retry_after", 3))
                last_error = "请求频率受限"
                if attempt < 3:
                    time.sleep(min(retry_after, 30))
                    continue
                raise NotifyError(f"Telegram 发送失败：{last_error}")
            if response.status_code >= 400:
                raise NotifyError(
                    f"Telegram 发送失败：HTTP {response.status_code} {response.text[:300]}"
                )
            return
