"""Поддержка: переписка пользователя с командой сервиса.

У каждого пользователя одно обращение (support_threads) — вся его переписка. Сообщение из виджета пишется в базу
и уходит в Telegram-группу поддержки: первое сообщение открывает тему «ID · почта · тариф», дальше всё идёт в неё.
Ответ админа в теме (telegram_notify.handle_update → handle_group_message) или из админки появляется в виджете,
записью в колокольчике и в Telegram пользователя, если он привязан. Ответ, который 10 минут не прочитали, а
Telegram не привязан, уходит письмом (email_unread_replies — планировщик).

К сообщению пользователя прикладывается, где он был: страница, проект, тариф и браузер (context).
"""

from __future__ import annotations

import base64
import binascii
import logging

from psycopg.types.json import Jsonb

from app.config import settings
from app.db import pool

log = logging.getLogger("app.support")

MAX_TEXT = 4000
MAX_IMAGE = 3_000_000  # байт после уменьшения в браузере
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
RATE_PER_HOUR = 30
EMAIL_AFTER_MIN = 10
SUPPORT_LINK = "/cabinet?support=1"


class SupportError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --- Чтение -----------------------------------------------------------------------------------------------


def _message_out(row: dict, *, admin: bool = False) -> dict:
    out = {
        "id": row["id"],
        "author": row["author"],
        "body": row["body"],
        "has_image": bool(row.get("has_image")),
        "created_at": row["created_at"].isoformat(),
    }
    if admin:
        out["context"] = row.get("context") or {}
    return out


def _thread_row(conn, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM support_threads WHERE user_id = %s", (user_id,)).fetchone()


def _messages(conn, thread_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT id, author, body, image IS NOT NULL AS has_image, context, created_at
        FROM support_messages WHERE thread_id = %s ORDER BY created_at, id
        """,
        (thread_id,),
    ).fetchall()


def unread_count(user_id: int) -> int:
    """Ответы поддержки, которые пользователь ещё не видел."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM support_messages m JOIN support_threads t ON t.id = m.thread_id
            WHERE t.user_id = %s AND m.author = 'admin' AND m.created_at > t.user_read_at
            """,
            (user_id,),
        ).fetchone()
    return int(row["n"])


def get_thread(user_id: int) -> dict:
    """Переписка для виджета: { messages, unread, telegram } — telegram: ответ придёт и туда."""
    with pool.connection() as conn:
        thread = _thread_row(conn, user_id)
        messages = _messages(conn, thread["id"]) if thread else []
        tg = conn.execute("SELECT telegram_chat_id FROM users WHERE id = %s", (user_id,)).fetchone()
    return {
        "messages": [_message_out(m) for m in messages],
        "unread": unread_count(user_id) if thread else 0,
        "telegram": bool((tg or {}).get("telegram_chat_id")),
    }


def mark_read(user_id: int) -> dict:
    """Пользователь открыл переписку: ответы прочитаны, их запись в колокольчике — тоже."""
    from app import notifications

    with pool.connection() as conn:
        conn.execute("UPDATE support_threads SET user_read_at = NOW() WHERE user_id = %s", (user_id,))
    notifications.mark_read(user_id, link=SUPPORT_LINK)
    return {"unread": 0}


def image(message_id: int, *, user_id: int | None = None) -> tuple[bytes, str] | None:
    """Скриншот сообщения; user_id — только из своей переписки (админке — любой)."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT m.image, m.image_type FROM support_messages m JOIN support_threads t ON t.id = m.thread_id
            WHERE m.id = %s AND m.image IS NOT NULL AND (%s::bigint IS NULL OR t.user_id = %s)
            """,
            (message_id, user_id, user_id),
        ).fetchone()
    return (bytes(row["image"]), row["image_type"] or "image/jpeg") if row else None


# --- Сообщение пользователя -----------------------------------------------------------------------------


def decode_image(data_url: str | None) -> tuple[bytes | None, str]:
    """«data:image/jpeg;base64,…» → байты и тип. Пусто — без картинки."""
    if not data_url:
        return None, ""
    head, _, payload = data_url.partition(",")
    kind = head.removeprefix("data:").removesuffix(";base64")
    if not head.startswith("data:") or not head.endswith(";base64") or kind not in IMAGE_TYPES:
        raise SupportError("image-type", "Скриншот — картинка JPEG, PNG или WebP")
    if len(payload) > MAX_IMAGE * 4 // 3 + 8:
        raise SupportError("image-size", "Скриншот слишком большой")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SupportError("image-type", "Не удалось прочитать скриншот") from exc
    if len(raw) > MAX_IMAGE:
        raise SupportError("image-size", "Скриншот слишком большой")
    return raw, kind


def _context(conn, user_id: int, raw: dict | None) -> dict:
    """Где был пользователь: страница и браузер — от клиента; проект проверяем, тариф — с сервера."""
    from app.plans import subscription

    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    page = str(raw.get("page") or "")[:300]
    if page.startswith("/"):
        out["page"] = page
    browser = str(raw.get("browser") or "")[:300]
    if browser:
        out["browser"] = browser
    ws_id = raw.get("workspace_id")
    if isinstance(ws_id, int):
        ws = conn.execute(
            "SELECT id, name FROM workspaces WHERE id = %s AND user_id = %s", (ws_id, user_id)
        ).fetchone()
        if ws:
            out["workspace"] = {"id": ws["id"], "name": ws["name"]}
    try:
        sub = subscription(user_id)
        plan = sub.get("plan")
        out["plan"] = plan["name"] if plan else "без тарифа"
    except Exception:
        log.exception("support plan")
    return out


def _context_line(context: dict) -> str:
    parts = []
    if context.get("page"):
        parts.append(f"страница {context['page']}")
    if context.get("workspace"):
        parts.append(f"проект «{context['workspace']['name']}» #{context['workspace']['id']}")
    if context.get("plan"):
        parts.append(f"тариф {context['plan']}")
    if context.get("browser"):
        parts.append(context["browser"])
    return " · ".join(parts)


def post_message(user_id: int, text: str, *, image_data: str | None = None, context: dict | None = None) -> dict:
    """Сообщение из виджета: в базу и в тему группы поддержки. Возвращает переписку."""
    text = (text or "").strip()
    raw, kind = decode_image(image_data)
    if not text and not raw:
        raise SupportError("empty", "Напишите сообщение или приложите скриншот")
    if len(text) > MAX_TEXT:
        raise SupportError("too-long", f"Сообщение длиннее {MAX_TEXT} знаков")
    with pool.connection() as conn:
        recent = conn.execute(
            """
            SELECT COUNT(*) AS n FROM support_messages m JOIN support_threads t ON t.id = m.thread_id
            WHERE t.user_id = %s AND m.author = 'user' AND m.created_at > NOW() - INTERVAL '1 hour'
            """,
            (user_id,),
        ).fetchone()["n"]
        if recent >= RATE_PER_HOUR:
            raise SupportError("rate", "Слишком много сообщений подряд. Мы уже читаем — ответим здесь же.")
        ctx = _context(conn, user_id, context)
        thread = conn.execute(
            """
            INSERT INTO support_threads (user_id) VALUES (%s)
            ON CONFLICT (user_id) DO UPDATE SET updated_at = NOW(), user_read_at = NOW()
            RETURNING *
            """,
            (user_id,),
        ).fetchone()
        message = conn.execute(
            """
            INSERT INTO support_messages (thread_id, author, body, image, image_type, context)
            VALUES (%s, 'user', %s, %s, %s, %s) RETURNING id
            """,
            (thread["id"], text, raw, kind, Jsonb(ctx)),
        ).fetchone()
    _forward(user_id, thread, message["id"], text, raw, kind, ctx)
    return get_thread(user_id)


def _topic_name(conn, user_id: int, ctx: dict) -> str:
    user = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone() or {}
    return f"{user_id} · {user.get('email') or 'без почты'} · {ctx.get('plan') or '—'}"


def _forward(user_id: int, thread: dict, message_id: int, text: str, raw: bytes | None, kind: str, ctx: dict) -> None:
    """В тему группы поддержки; темы ещё нет — открываем. Сбой Telegram не мешает: сообщение уже в базе."""
    from app import telegram_notify as tg

    if not tg.support_configured():
        return
    try:
        topic = thread.get("tg_topic_id")
        if not topic:
            with pool.connection() as conn:
                name = _topic_name(conn, user_id, ctx)
            topic = tg.create_support_topic(name)
            if not topic:
                return
            with pool.connection() as conn:
                conn.execute("UPDATE support_threads SET tg_topic_id = %s WHERE id = %s", (topic, thread["id"]))
        line = _context_line(ctx)
        body = f"{text}\n\n— {line}" if line else text
        from app.admin_alerts import admin_link

        link = admin_link(f"support?user={user_id}")
        body = f"{body or '(скриншот)'}\n{link}" if link else body
        sent = tg.send_to_support_topic(topic, body or "(скриншот)", image=raw, image_type=kind)
        if sent:
            with pool.connection() as conn:
                conn.execute("UPDATE support_messages SET tg_message_id = %s WHERE id = %s", (sent, message_id))
    except Exception:
        log.exception("support forward")


# --- Ответ поддержки -------------------------------------------------------------------------------------


def _add_reply(thread: dict, text: str, *, raw: bytes | None = None, kind: str = "", tg_message_id: int | None = None) -> int | None:
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO support_messages (thread_id, author, body, image, image_type, tg_message_id)
            VALUES (%s, 'admin', %s, %s, %s, %s)
            ON CONFLICT (thread_id, tg_message_id) WHERE tg_message_id IS NOT NULL DO NOTHING
            RETURNING id
            """,
            (thread["id"], text, raw, kind, tg_message_id),
        ).fetchone()
        if row is None:
            return None  # то же сообщение уже пришло (webhook и опрос)
        conn.execute(
            "UPDATE support_threads SET updated_at = NOW(), admin_read_at = NOW() WHERE id = %s", (thread["id"],)
        )
    _notify_reply(thread["user_id"], text)
    return row["id"]


def _notify_reply(user_id: int, text: str) -> None:
    from app.notifications import notify

    preview = " ".join((text or "Скриншот").split())
    notify(
        user_id,
        "support",
        "Ответ поддержки",
        body=preview[:160],
        link=SUPPORT_LINK,
        dedup_key="support",
        telegram_text=f"Толк · ответ поддержки\n{(text or 'Скриншот')[:3000]}\n\nОтветить: "
        f"{(settings.public_base_url or '').rstrip('/')}{SUPPORT_LINK}",
    )


def admin_reply(user_id: int, text: str) -> dict:
    """Ответ из админки: в переписку пользователя и копией в его тему в группе."""
    from app import telegram_notify as tg

    text = (text or "").strip()
    if not text:
        raise SupportError("empty", "Пустой ответ")
    if len(text) > MAX_TEXT:
        raise SupportError("too-long", f"Ответ длиннее {MAX_TEXT} знаков")
    with pool.connection() as conn:
        thread = _thread_row(conn, user_id)
    if thread is None:
        raise SupportError("not-found", "У пользователя нет обращений")
    _add_reply(thread, text)
    if thread.get("tg_topic_id"):
        try:
            tg.send_to_support_topic(thread["tg_topic_id"], f"Ответ из админки:\n{text}")
        except Exception:
            log.exception("support admin copy")
    return admin_thread(user_id)


def handle_group_message(message: dict) -> bool:
    """Сообщение в теме группы поддержки: ответ админа — в переписку пользователя этой темы."""
    from app import telegram_notify as tg

    if (message.get("from") or {}).get("is_bot"):
        return False
    topic = message.get("message_thread_id")
    if not topic or not message.get("is_topic_message", True):
        return False
    text = (message.get("text") or message.get("caption") or "").strip()
    raw, kind = None, ""
    photos = message.get("photo") or []
    if photos:
        biggest = max(photos, key=lambda p: int(p.get("file_size") or 0) or int(p.get("width") or 0))
        raw = tg.download_file(biggest.get("file_id") or "", MAX_IMAGE)
        kind = "image/jpeg" if raw else ""
    if not text and not raw:
        return False  # служебное: тема создана, закреп, стикер
    with pool.connection() as conn:
        thread = conn.execute("SELECT * FROM support_threads WHERE tg_topic_id = %s", (int(topic),)).fetchone()
    if thread is None:
        return False
    return _add_reply(thread, text[:MAX_TEXT], raw=raw, kind=kind, tg_message_id=message.get("message_id")) is not None


def email_unread_replies() -> int:
    """Ответы, которые 10 минут не прочитаны, у пользователя без Telegram, — письмом, один раз."""
    from app import mailer

    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id AS thread_id, u.email, ARRAY_AGG(m.id ORDER BY m.id) AS ids,
                   STRING_AGG(NULLIF(m.body, ''), E'\n\n' ORDER BY m.id) AS text
            FROM support_messages m
            JOIN support_threads t ON t.id = m.thread_id
            JOIN users u ON u.id = t.user_id
            WHERE m.author = 'admin' AND m.emailed_at IS NULL AND m.created_at > t.user_read_at
              AND m.created_at < NOW() - %s * INTERVAL '1 minute'
              AND COALESCE(u.telegram_chat_id, '') = '' AND COALESCE(u.email, '') <> '' AND u.blocked_at IS NULL
            GROUP BY t.id, u.email
            """,
            (EMAIL_AFTER_MIN,),
        ).fetchall()
    sent = 0
    base = (settings.public_base_url or "").rstrip("/")
    for row in rows:
        link = f"{base}{SUPPORT_LINK}"
        body = (
            "Здравствуйте!\n\nПоддержка Толка ответила на ваше сообщение:\n\n"
            f"{row['text'] or 'Скриншот — посмотрите в кабинете.'}\n\n"
            f"Ответить можно в кабинете: {link}\n"
        )
        mailer.send(row["email"], "Ответ поддержки Толка", body, link=link)
        with pool.connection() as conn:
            conn.execute("UPDATE support_messages SET emailed_at = NOW() WHERE id = ANY(%s)", (row["ids"],))
        sent += 1
    return sent


# --- Админка ---------------------------------------------------------------------------------------------


def admin_thread(user_id: int) -> dict:
    """Переписка для админки, с контекстом сообщений; просмотр — прочитано админом."""
    with pool.connection() as conn:
        thread = _thread_row(conn, user_id)
        if thread is None:
            return {"messages": [], "topic": None}
        messages = _messages(conn, thread["id"])
        conn.execute("UPDATE support_threads SET admin_read_at = NOW() WHERE id = %s", (thread["id"],))
    return {"messages": [_message_out(m, admin=True) for m in messages], "topic": thread.get("tg_topic_id")}


def admin_counts() -> dict[int, dict]:
    """По пользователям: сколько сообщений и сколько новых для админа — для списка пользователей."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT t.user_id, COUNT(m.id) AS total,
                   COUNT(m.id) FILTER (WHERE m.author = 'user' AND m.created_at > t.admin_read_at) AS unread
            FROM support_threads t LEFT JOIN support_messages m ON m.thread_id = t.id
            GROUP BY t.user_id
            """
        ).fetchall()
    return {r["user_id"]: {"total": int(r["total"]), "unread": int(r["unread"])} for r in rows}

