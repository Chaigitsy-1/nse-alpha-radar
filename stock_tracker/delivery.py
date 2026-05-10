from __future__ import annotations

import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path


def send_email(subject: str, body: str, attachment: Path | None = None) -> tuple[bool, str]:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587") or "587")
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    recipient = os.getenv("EMAIL_TO", "")

    if not all([host, username, password, recipient]):
        return False, "Email skipped: SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, or EMAIL_TO missing."

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipient
    message.set_content(body)

    if attachment and attachment.exists():
        message.add_attachment(
            attachment.read_bytes(),
            maintype="text",
            subtype="markdown",
            filename=attachment.name,
        )

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(message)
    return True, f"Email sent to {recipient}."


def send_telegram(message: str) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False, "Telegram skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message[:3900],
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()
    return True, "Telegram message sent."
