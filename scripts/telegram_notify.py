#!/usr/bin/env python3
"""Send output-only Telegram notifications for GitHub workflows."""

import argparse
import os
import requests


def main():
    parser = argparse.ArgumentParser(description="Send a Telegram message.")
    parser.add_argument("--text", required=True, help="Message text to send.")
    parser.add_argument(
        "--parse-mode",
        default="Markdown",
        choices=("Markdown", "HTML", ""),
        help="Telegram parse_mode value.",
    )
    parser.add_argument(
        "--allow-missing-secrets",
        action="store_true",
        help="Exit successfully when TELEGRAM secrets are not configured.",
    )
    args = parser.parse_args()

    token = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        if args.allow_missing_secrets:
            print("Telegram secrets missing; skipping notification.")
            return
        raise RuntimeError("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID.")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": args.text,
        "disable_web_page_preview": True,
    }
    if args.parse_mode:
        payload["parse_mode"] = args.parse_mode
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()


if __name__ == "__main__":
    main()
