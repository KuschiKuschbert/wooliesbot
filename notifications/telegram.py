"""Telegram send + Markdown escape (reads secrets from env only)."""

import logging
import os

import requests

TELEGRAM_TOKEN = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
TELEGRAM_MAX_LEN = 4000


def _escape_md(text):
    """Escape Telegram Markdown V1 special characters in dynamic text."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def send_telegram(message):
    """Send message(s) to Telegram. Splits at newlines if over limit.
    Falls back to plain text if Markdown parse fails."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.debug("Telegram not configured; message skipped.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    if len(message) <= TELEGRAM_MAX_LEN:
        parts = [message]
    else:
        parts, curr = [], []
        for line in message.split("\n"):
            if sum(len(l) + 1 for l in curr) + len(line) + 1 > TELEGRAM_MAX_LEN and curr:
                parts.append("\n".join(curr))
                curr = []
            curr.append(line)
        if curr:
            parts.append("\n".join(curr))
    for part in parts:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": part, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code == 400 and "can't parse" in response.text.lower():
                logging.warning("Markdown parse failed, retrying as plain text.")
                payload["parse_mode"] = ""
                response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            logging.info("Telegram message sent successfully.")
        except Exception as e:
            logging.error(f"Error sending Telegram: {e}")
