"""Telegram-алерты: привязка чата через /start CODE и отправка сообщений."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.db import pool

log = logging.getLogger("app.telegram")

API = "https://api.telegram.org"
_OFFSET = 0
_LINK_TTL_MIN = 30


def telegram_configured() -> bool:
    return bool(settings.telegram_bot_token.strip())


def webhook_secret() -> str:
    return (settings.telegram_webhook_secret or "").strip()


def verify_webhook_secret(header_value: str | None) -> bool:
    """Если secret не задан — webhook открыт (обратная совместимость)."""
    expected = webhook_secret()
    if not expected:
        return True
    return secrets.compare_digest((header_value or "").strip(), expected)


def bot_username() -> str:
    return (settings.telegram_bot_username or "").lstrip("@")


def send_message(chat_id: str | int, text: str) -> bool:
    if not telegram_configured() or not chat_id:
        return False
    url = f"{API}/bot{settings.telegram_bot_token}/sendMessage"
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                url,
                json={
                    "chat_id": str(chat_id),
                    "text": text[:4000],
                    "disable_web_page_preview": True,
                },
            )
        if response.status_code >= 400:
            log.warning("telegram send %s %s", response.status_code, response.text[:300])
            return False
        return True
    except httpx.HTTPError:
        log.exception("telegram send")
        return False


# --- Группа поддержки с темами ----------------------------------------------------------------------------


def support_chat_id() -> str:
    return (settings.telegram_support_chat_id or "").strip()


def support_configured() -> bool:
    return telegram_configured() and bool(support_chat_id())


def _api(method: str, *, payload: dict | None = None, files: dict | None = None) -> dict | None:
    """Вызов Bot API: result или None (не настроено, сеть, ошибка Telegram — в лог)."""
    if not telegram_configured():
        return None
    url = f"{API}/bot{settings.telegram_bot_token}/{method}"
    try:
        with httpx.Client(timeout=30.0) as client:
            if files:
                response = client.post(url, data={k: str(v) for k, v in (payload or {}).items()}, files=files)
            else:
                response = client.post(url, json=payload or {})
    except httpx.HTTPError:
        log.exception("telegram %s", method)
        return None
    if response.status_code >= 400:
        log.warning("telegram %s %s %s", method, response.status_code, response.text[:300])
        return None
    return (response.json() or {}).get("result")


def create_support_topic(name: str) -> int | None:
    """Новая тема в группе поддержки → её message_thread_id."""
    if not support_configured():
        return None
    result = _api("createForumTopic", payload={"chat_id": support_chat_id(), "name": name[:128]})
    return int(result["message_thread_id"]) if result and result.get("message_thread_id") else None


def send_to_support_topic(topic_id: int, text: str, *, image: bytes | None = None, image_type: str = "") -> int | None:
    """Сообщение (или скриншот с подписью) в тему поддержки → message_id."""
    if not support_configured() or not topic_id:
        return None
    payload = {"chat_id": support_chat_id(), "message_thread_id": int(topic_id)}
    if image:
        ext = "png" if image_type == "image/png" else "jpg"
        result = _api(
            "sendPhoto",
            payload={**payload, "caption": text[:1000]},
            files={"photo": (f"screenshot.{ext}", image, image_type or "image/jpeg")},
        )
    else:
        result = _api("sendMessage", payload={**payload, "text": text[:4000], "disable_web_page_preview": True})
    return int(result["message_id"]) if result and result.get("message_id") else None


def download_file(file_id: str, max_bytes: int = 5_000_000) -> bytes | None:
    """Файл из сообщения (фото из ответа поддержки)."""
    info = _api("getFile", payload={"file_id": file_id})
    path = (info or {}).get("file_path")
    if not path or int((info or {}).get("file_size") or 0) > max_bytes:
        return None
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{API}/file/bot{settings.telegram_bot_token}/{path}")
    except httpx.HTTPError:
        log.exception("telegram file")
        return None
    if response.status_code >= 400 or len(response.content) > max_bytes:
        return None
    return response.content


def notify_user(user_id: int, text: str) -> bool:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT telegram_chat_id FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
    chat_id = (row or {}).get("telegram_chat_id") or ""
    if not chat_id:
        return False
    return send_message(chat_id, text)


def create_link_code(user_id: int) -> dict:
    if not telegram_configured():
        return {"configured": False, "code": "", "url": ""}
    code = secrets.token_urlsafe(16)
    expires = datetime.now(timezone.utc) + timedelta(minutes=_LINK_TTL_MIN)
    with pool.connection() as conn:
        conn.execute(
            "DELETE FROM telegram_link_codes WHERE user_id = %s OR expires_at < NOW()",
            (user_id,),
        )
        conn.execute(
            """
            INSERT INTO telegram_link_codes (code, user_id, expires_at)
            VALUES (%s, %s, %s)
            """,
            (code, user_id, expires),
        )
    username = bot_username()
    url = f"https://t.me/{username}?start={code}" if username else ""
    return {
        "configured": True,
        "code": code,
        "url": url,
        "expires_minutes": _LINK_TTL_MIN,
        "bot_username": username,
    }


def unlink_telegram(user_id: int) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE users SET telegram_chat_id = '' WHERE id = %s",
            (user_id,),
        )


def get_telegram_status(user_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT telegram_chat_id FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
    chat = (row or {}).get("telegram_chat_id") or ""
    return {
        "configured": telegram_configured(),
        "bot_username": bot_username(),
        "linked": bool(chat),
        "chat_id": chat if chat else "",
    }


def _bind_code(code: str, chat_id: str) -> bool:
    code = (code or "").strip()
    if not code or not chat_id:
        return False
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT user_id FROM telegram_link_codes
            WHERE code = %s AND expires_at > NOW()
            """,
            (code,),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE users SET telegram_chat_id = %s WHERE id = %s",
            (str(chat_id), row["user_id"]),
        )
        conn.execute("DELETE FROM telegram_link_codes WHERE code = %s", (code,))
    send_message(chat_id, "Толк: Telegram подключён. Буду присылать срабатывания правил.")
    return True


def handle_update(update: dict) -> None:
    message = update.get("message") or update.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    if support_chat_id() and str(chat_id) == support_chat_id():
        # Ответ поддержки в теме группы — в виджет пользователя.
        from app.support import handle_group_message

        handle_group_message(message)
        return
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        if code and _bind_code(code, str(chat_id)):
            return
        send_message(
            chat_id,
            "Откройте ссылку привязки в кабинете Толка (раздел Telegram) и нажмите Start ещё раз.",
        )


def poll_updates_once() -> int:
    """Обрабатывает входящие апдейты бота. Возвращает число обработанных."""
    global _OFFSET
    if not telegram_configured():
        return 0
    url = f"{API}/bot{settings.telegram_bot_token}/getUpdates"
    try:
        with httpx.Client(timeout=35.0) as client:
            response = client.get(
                url,
                params={"offset": _OFFSET, "timeout": 0, "limit": 50},
            )
    except httpx.HTTPError:
        log.exception("telegram getUpdates")
        return 0
    if response.status_code >= 400:
        log.warning("telegram getUpdates %s %s", response.status_code, response.text[:200])
        return 0
    result = (response.json() or {}).get("result") or []
    n = 0
    for update in result:
        try:
            handle_update(update)
            n += 1
        except Exception:
            log.exception("telegram update")
        uid = int(update.get("update_id") or 0)
        if uid >= _OFFSET:
            _OFFSET = uid + 1
    return n


def format_rule_alert(
    *,
    rule_name: str,
    action: str,
    dry_run: bool,
    matched: list[dict],
    applied: int,
    error: str = "",
) -> str:
    mode = "dry-run" if dry_run else "auto"
    lines = [
        f"Толк · правило «{rule_name}»",
        f"Режим: {mode} · действие: {action} · совпало: {len(matched)} · применено: {applied}",
    ]
    if error:
        lines.append(f"⚠ Не всё применилось: {error}")
    for item in matched[:15]:
        lines.append(f"• {item.get('name') or item.get('campaign_id')}: {item.get('reason') or ''}")
    if len(matched) > 15:
        lines.append(f"… и ещё {len(matched) - 15}")
    return "\n".join(lines)
