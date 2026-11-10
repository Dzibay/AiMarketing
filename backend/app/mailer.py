"""Письма через SMTP из .env. Без SMTP письмо не уходит — ссылка пишется в лог backend."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger("app.mailer")


def configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send(to_email: str, subject: str, body: str, *, link: str = "") -> bool:
    """True — письмо ушло. Ошибка SMTP не роняет запрос: ссылка остаётся в логе."""
    if not configured():
        log.warning("SMTP не настроен. Письмо «%s» для %s: %s", subject, to_email, link or body)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.set_content(body)
    try:
        if settings.smtp_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                smtp.ehlo()
                if settings.smtp_starttls:
                    smtp.starttls()
                    smtp.ehlo()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
    except Exception:
        log.exception("Не удалось отправить письмо «%s» на %s", subject, to_email)
        if link:
            log.warning("Ссылка из письма для %s: %s", to_email, link)
        return False
    log.info("Письмо «%s» отправлено на %s", subject, to_email)
    return True
