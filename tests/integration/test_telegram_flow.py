from __future__ import annotations

import os

import pytest

from mailpulse.config import TelegramConfig
from mailpulse.notify.telegram import NotifyError, TelegramNotifier

pytestmark = pytest.mark.integration


def test_invalid_token_rejected_by_real_api():
    os.environ["MAILPULSE_IT_TG_TOKEN"] = "123456:invalid-token-for-testing"
    os.environ["MAILPULSE_IT_TG_CHAT"] = "1"
    config = TelegramConfig(
        enabled=True,
        token_env="MAILPULSE_IT_TG_TOKEN",
        chat_id_env="MAILPULSE_IT_TG_CHAT",
    )
    notifier = TelegramNotifier(config)
    try:
        with pytest.raises(NotifyError) as error:
            notifier.send("<b>test</b>")
        assert "401" in str(error.value) or "404" in str(error.value)
    finally:
        notifier.close()
