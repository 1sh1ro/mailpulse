from __future__ import annotations

import argparse
import logging
import os
import sys

from .ai.classifier import AiError
from .config import ConfigError, load_config
from .notify.telegram import NotifyError
from .pipeline import RunError, Runner
from .state.store import StateConflict, StateError


def _redact(text: str) -> str:
    for name, value in os.environ.items():
        if not value or len(value) < 8:
            continue
        if name.lower().endswith(("token", "key", "password", "secret")):
            text = text.replace(value, "***")
    return text


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(str(record.msg))
        return True


def _setup_logging(verbose: bool) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    )
    handler.addFilter(_RedactFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mailpulse", description="AI 邮件过滤与通知")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="建立新邮件边界并补扫初始化窗口")
    run_parser = sub.add_parser("run", help="检查新邮件并发送通知与摘要")
    run_parser.add_argument("--force", action="store_true", help="忽略检查间隔立即执行")
    sub.add_parser("validate", help="只校验配置与密钥是否齐备")
    return parser


def _report(results) -> int:
    failures = 0
    for result in results:
        logging.getLogger("mailpulse").info(
            "账户 %s：拉取 %d，通知 %d，摘要 %d，静默 %d，失败 %d",
            result.account_id,
            result.fetched,
            result.notified,
            result.digested,
            result.muted,
            result.failed,
        )
        for error in result.errors:
            logging.getLogger("mailpulse").error("账户 %s 异常：%s", result.account_id, error)
            failures += 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.command == "validate":
        print("配置校验通过")
        return 0
    try:
        runner = Runner(config)
        results = runner.run(mode=args.command, force=args.force)
    except (StateError, StateConflict, RunError, AiError, NotifyError) as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1
    return _report(results)


if __name__ == "__main__":
    raise SystemExit(main())
