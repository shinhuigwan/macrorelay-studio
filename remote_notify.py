"""Send a MacroRelay Remote notification from a generated macro."""

from __future__ import annotations

import argparse
from pathlib import Path

from remote_common import load_config, post_agent_event


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="MacroRelay")
    parser.add_argument("--message", default="동작이 완료되었습니다.")
    parser.add_argument("--message-file", type=Path)
    parser.add_argument("--level", default="info", choices=("info", "success", "warning", "error"))
    parser.add_argument("--macro", default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    config = load_config(root, create=False)
    if not config.get("enabled"):
        return 2
    message = args.message.strip()
    if args.message_file and args.message_file.is_file():
        message = args.message_file.read_text(encoding="utf-8-sig", errors="replace").strip()
    message = message or args.title
    result = post_agent_event(
        config,
        "notification",
        message,
        {"title": args.title, "level": args.level, "macro": args.macro},
    )
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
