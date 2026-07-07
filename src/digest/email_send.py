from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from .config import EmailConfig


class EmailConfigError(RuntimeError):
    pass


def send_email(
    *, email_config: EmailConfig, subject: str, body: str, html_body: str = ""
) -> None:
    if not email_config.enabled:
        raise EmailConfigError("Email sending is disabled in config.")
    if not email_config.recipient:
        raise EmailConfigError("Email recipient is empty.")

    sender = os.environ.get(email_config.sender_env)
    username = os.environ.get(email_config.smtp_username_env)
    password = os.environ.get(email_config.smtp_password_env)

    missing = []
    if not sender:
        missing.append(email_config.sender_env)
    if not username:
        missing.append(email_config.smtp_username_env)
    if not password:
        missing.append(email_config.smtp_password_env)
    if missing:
        raise EmailConfigError(
            "Missing email environment variable(s): " + ", ".join(missing)
        )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = email_config.recipient
    message["Subject"] = subject
    message.set_content(body)
    if html_body:
        # Multipart/alternative: clients render the HTML brief, plain text
        # stays as the fallback.
        message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(email_config.smtp_host, email_config.smtp_port, timeout=30) as smtp:
        if email_config.smtp_use_tls:
            smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)
