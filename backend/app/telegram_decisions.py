"""Решать из Telegram: кнопки «Выполнить / Отклонить / Открыть в кабинете» у предложений ИИ.

Включается в настройках уведомлений («Решать из Telegram», users.telegram_decide), по умолчанию выключено:
привязанный Telegram становится способом менять рекламу.

Как работает:
- Когда ИИ закончил ход и ждёт решения, бот присылает карточку на каждое новое предложение чата
  (offer_chat). У простых — тех, где нечего выбирать строками (минус-фразы в кампанию, остановить или
  запустить кампании, дневной бюджет, ставка или пауза ключевых фраз), — «Выполнить» и «Отклонить».
  Остальным — только «Открыть в кабинете».
- «Выполнить» всегда переспрашивает: «Добавить 12 минус-фраз в «Окна — поиск»?» → «Да, выполнить» / «Отмена».
  От случайного нажатия защищает только этот вопрос.
- «Да, выполнить» решает предложение тем же ai_agent.decide_action, что и кабинет: выполнение идёт ходом
  чата и пишется в «Историю». Когда действие выполнено (ai_agent._run_action → action_finished), бот
  правит сообщение итогом и предлагает «Вернуть как было» — тоже с повторным вопросом; откат идёт задачей
  undo_action по всем записям решения (undo.ai_action_undo_preview), итог — undo_finished.

Кнопка несёт в callback_data только «d:<код>» (Telegram даёт 64 байта). Код случайный и записан в
telegram_decision_tokens: чей он, в какой чат ушёл, что делает и к какому предложению. Нажатие проверяет,
что чат и нажавший — привязанный Telegram владельца, что код не использован и не старше 48 часов.
Нажатие гасит все кнопки того же предложения: каждый следующий шаг получает новые коды.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from psycopg.types.json import Jsonb

from app.db import pool

log = logging.getLogger(__name__)

TOKEN_TTL = timedelta(hours=48)
KEEP_USED = timedelta(days=7)  # использованные коды держим неделю: по «Да, выполнить» бот найдёт сообщение
PREFIX = "d:"
MAX_CARDS = 5  # карточек за один ход; остальное — одной строкой «и ещё N — в кабинете»

KINDS = ("run", "reject", "yes", "no", "undo", "undo_yes", "undo_no", "open")

# Простые предложения: tool → допустимые action (None — любое). Где надо выбирать строки — нет в списке.
_CAMPAIGN_SIMPLE = {"pause", "resume", "set_budget"}
_KEYWORD_SIMPLE = {"suspend", "resume", "set_bid"}

ALREADY = "Уже решено в кабинете"
EXPIRED = "Кнопка устарела — решите в кабинете"
USED = "Эта кнопка уже нажата"
FOREIGN = "Эта кнопка не для вас"
TURNED_OFF = "Решения из Telegram выключены в настройках уведомлений"
NO_ACCESS = "Кабинет сейчас недоступен — откройте его в браузере"


# --- Чистые правила (юнит-тесты) -----------------------------------------------------------------------


def is_simple(tool: str, args: dict | None) -> bool:
    """Можно ли решить предложение одной кнопкой: в нём нечего выбирать строками."""
    args = args if isinstance(args, dict) else {}
    if tool == "add_campaign_negatives":
        return bool(args.get("campaign_row_id")) and bool(_phrases(args))
    if tool == "campaigns_action":
        return str(args.get("action") or "") in _CAMPAIGN_SIMPLE and bool(args.get("campaign_row_ids"))
    if tool == "update_campaign_settings":
        changes = args.get("changes")
        return isinstance(changes, dict) and set(changes) == {"daily_budget_rub"}
    if tool == "keywords_action":
        return str(args.get("action") or "") in _KEYWORD_SIMPLE and bool(args.get("keyword_row_ids"))
    return False


def _phrases(args: dict) -> list[str]:
    values = args.get("phrases")
    return [str(p).strip() for p in values if str(p or "").strip()] if isinstance(values, list) else []


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def confirm_question(tool: str, args: dict | None, summary: str, campaign_name: str = "") -> str:
    """Повторный вопрос перед выполнением: «Добавить 12 минус-фраз в «Окна — поиск»?»."""
    args = args if isinstance(args, dict) else {}
    summary = " ".join(str(summary or "").split()).rstrip(".?! ")
    if tool == "add_campaign_negatives":
        n = len(_phrases(args))
        where = f" в «{campaign_name}»" if campaign_name else ""
        return f"Добавить {n} {plural(n, 'минус-фразу', 'минус-фразы', 'минус-фраз')}{where}?"
    if tool == "update_campaign_settings" and summary.startswith("Настройки "):
        return f"Изменить настройки {summary[len('Настройки '):]}?"
    if tool == "keywords_action" and str(args.get("action") or "") == "set_bid":
        bid = args.get("bid_rub")
        what = summary.split(":", 1)[1].split("→", 1)[0].strip() if ":" in summary else ""
        if isinstance(bid, (int, float)) and not isinstance(bid, bool):
            return f"Поставить ставку {bid:g} ₽ ключевым фразам {what}".rstrip() + "?"
    return f"{summary}?" if summary else "Выполнить предложение ИИ?"


def usable_url(url: str) -> bool:
    """Telegram принимает в кнопке только настоящий адрес: не localhost и не адрес без схемы."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host:
        return False
    return host not in ("localhost", "127.0.0.1", "::1") and not host.endswith(".localhost") and "." in host


def cabinet_url(link: str) -> str:
    from app.settings_store import public_base_url

    if not link:
        return ""
    return f"{(public_base_url() or '').rstrip('/')}{link}"


def open_row(url: str) -> list[dict]:
    """Строка с URL-кнопкой «Открыть в кабинете»; адрес не годится для Telegram — строки нет."""
    return [{"text": "Открыть в кабинете", "url": url}] if usable_url(url) else []


def markup(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": [r for r in rows if r]}


def open_markup(url: str) -> dict | None:
    """Только «Открыть в кабинете» — или None, если ссылку в кнопку не положить."""
    row = open_row(url)
    return markup([row]) if row else None


def chat_link(chat_id: int, workspace_id: int | None) -> str:
    return f"/w/{workspace_id}/chat/{chat_id}" if workspace_id else f"/cabinet/chat/{chat_id}"


# --- Настройка -----------------------------------------------------------------------------------------


def enabled(user_id: int) -> tuple[bool, str]:
    """(«Решать из Telegram» включено, привязанный чат)."""
    with pool.connection() as conn:
        row = conn.execute("SELECT telegram_decide, telegram_chat_id FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        return False, ""
    return bool(row["telegram_decide"]), str(row["telegram_chat_id"] or "")


# --- Коды кнопок ---------------------------------------------------------------------------------------


def _new_token(conn, user_id: int, chat_id: str, kind: str, action_id: int | None, payload: dict) -> str:
    token = secrets.token_urlsafe(12)  # 16 символов: «d:» + код — 18 байт из 64
    conn.execute(
        """
        INSERT INTO telegram_decision_tokens (token, user_id, chat_id, kind, action_id, payload, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (token, user_id, str(chat_id), kind, action_id, Jsonb(payload), datetime.now(timezone.utc) + TOKEN_TTL),
    )
    return PREFIX + token


def _button(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _purge(conn, user_id: int) -> None:
    conn.execute(
        "DELETE FROM telegram_decision_tokens WHERE user_id = %s AND expires_at < %s",
        (user_id, datetime.now(timezone.utc) - KEEP_USED),
    )


def _decision_markup(conn, user_id: int, chat_id: str, action_id: int, payload: dict) -> dict:
    run = _new_token(conn, user_id, chat_id, "run", action_id, payload)
    reject = _new_token(conn, user_id, chat_id, "reject", action_id, payload)
    return markup([[_button("Выполнить", run), _button("Отклонить", reject)], open_row(payload.get("url") or "")])


def _undo_markup(conn, user_id: int, chat_id: str, action_id: int, payload: dict) -> dict:
    undo = _new_token(conn, user_id, chat_id, "undo", action_id, payload)
    return markup([[_button("Вернуть как было", undo)], open_row(payload.get("url") or "")])


def _with_link(text: str, url: str) -> str:
    """Ссылка в кнопку не помещается (адрес для Telegram не годится) — строкой в тексте."""
    return f"{text}\n{url}" if url and not usable_url(url) else text


# --- Карточки предложений ------------------------------------------------------------------------------


def card_text(item: dict, simple: bool) -> str:
    """Текст карточки предложения: проект, что предлагает ИИ, почему; у сложных — куда идти решать."""
    where = (item.get("project") or {}).get("name") or (item.get("chat") or {}).get("title") or ""
    lines = [f"Толк · ИИ предлагает{' · ' + where if where else ''}"]
    summary = " ".join(str(item.get("summary") or "").split())
    label = str(item.get("label") or "")
    lines.append(f"{label}: {summary}" if label and summary else (summary or label))
    if item.get("reason"):
        lines.append(f"Почему: {' '.join(str(item['reason']).split())[:300]}")
    if not simple:
        lines.append("Здесь нужно выбрать строки или ответить на вопросы — решите в кабинете.")
    return "\n".join(x for x in lines if x)


def offer_chat(user_id: int, chat_id: int) -> int:
    """ИИ ждёт решения в чате — карточки новых предложений в Telegram. → сколько сообщений отправлено.

    Каждое предложение присылается один раз (о присланном помнит telegram_decision_tokens)."""
    from app.ai_agent import _pending_item, _pending_rows

    on, tg_chat = enabled(user_id)
    if not on or not tg_chat:
        return 0
    with pool.connection() as conn:
        offered = {
            int(r["action_id"])
            for r in conn.execute(
                """
                SELECT DISTINCT t.action_id FROM telegram_decision_tokens t JOIN ai_actions a ON a.id = t.action_id
                WHERE a.chat_id = %s AND t.user_id = %s
                """,
                (chat_id, user_id),
            ).fetchall()
        }
    rows = [(r, ws) for r, ws in _pending_rows(user_id) if int(r["chat_id"]) == int(chat_id) and int(r["id"]) not in offered]
    rows.sort(key=lambda x: int(x[0]["id"]))
    if not rows:
        return 0
    sent = 0
    with pool.connection() as conn:
        _purge(conn, user_id)
    # Коды каждой карточки сохраняются (коммит) до отправки: нажатие сразу после доставки их найдёт,
    # а сбой на следующей карточке не откатит коды уже присланных кнопок.
    for row, ws in rows[:MAX_CARDS]:
        item = _pending_item(row, ws)
        simple = is_simple(row["tool"], row["args"])
        url = cabinet_url(chat_link(int(row["chat_id"]), row["chat_workspace_id"]))
        text = card_text(item, simple)
        payload = {"text": text, "url": url}
        with pool.connection() as conn:
            if simple:
                reply = _decision_markup(conn, user_id, tg_chat, int(row["id"]), payload)
            else:
                _new_token(conn, user_id, tg_chat, "open", int(row["id"]), payload)  # отметка «уже присылали»
                reply = open_markup(url)
        sent += bool(_send(tg_chat, _with_link(text, url), reply))
    rest = rows[MAX_CARDS:]
    if rest:
        with pool.connection() as conn:
            for row, _ws in rest:
                _new_token(conn, user_id, tg_chat, "open", int(row["id"]), {"text": "", "url": ""})
        url = cabinet_url(chat_link(int(chat_id), rest[0][0]["chat_workspace_id"]))
        n = len(rest)
        text = f"И ещё {n} {plural(n, 'предложение', 'предложения', 'предложений')} ИИ — в кабинете."
        sent += bool(_send(tg_chat, _with_link(text, url), open_markup(url)))
    return sent


def _send(chat_id: str, text: str, reply: dict | None) -> bool:
    from app.telegram_notify import send_message

    return send_message(chat_id, text, reply) if reply else send_message(chat_id, text)


# --- Нажатия -------------------------------------------------------------------------------------------


def _subscription_ok(user_id: int) -> bool:
    from app import plans

    try:
        return plans.subscription(user_id)["state"] not in ("unverified", "expired")
    except Exception:  # noqa: BLE001
        log.exception("Тариф пользователя %s", user_id)
        return False


def handle_callback(cq: dict) -> None:
    """Нажатие кнопки под сообщением бота. Любой исход — ответ answerCallbackQuery (иначе крутятся часики)."""
    from app.telegram_notify import answer_callback

    callback_id = str(cq.get("id") or "")
    try:
        text = _handle(cq)
    except Exception:  # noqa: BLE001
        log.exception("Telegram: нажатие кнопки")
        text = "Не получилось — попробуйте в кабинете"
    if callback_id:
        answer_callback(callback_id, text)


def _handle(cq: dict) -> str:
    from app.telegram_notify import edit_message

    data = str(cq.get("data") or "")
    message = cq.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "")
    from_id = str((cq.get("from") or {}).get("id") or "")
    message_id = message.get("message_id")
    if not data.startswith(PREFIX) or not chat_id or message_id is None:
        return ""
    token = data[len(PREFIX) :]
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT t.*, u.telegram_chat_id, u.telegram_decide, u.blocked_at
            FROM telegram_decision_tokens t JOIN users u ON u.id = t.user_id
            WHERE t.token = %s
            """,
            (token,),
        ).fetchone()
    if row is None:
        return EXPIRED
    linked = str(row["telegram_chat_id"] or "")
    # Чат сообщения и нажавший — привязанный Telegram владельца кода (личный чат: id чата = id человека).
    if not linked or linked != chat_id or str(row["chat_id"]) != chat_id or (from_id and from_id != linked):
        return FOREIGN
    if row["kind"] not in KINDS or row["kind"] == "open":
        return ""
    if row["used_at"] is not None:
        return USED
    if row["expires_at"] <= datetime.now(timezone.utc):
        return EXPIRED
    user_id = int(row["user_id"])
    if not row["telegram_decide"]:
        return TURNED_OFF
    if row["blocked_at"] is not None or not _subscription_ok(user_id):
        return NO_ACCESS
    with pool.connection() as conn:
        claimed = conn.execute(
            """
            UPDATE telegram_decision_tokens SET used_at = NOW(), message_id = %s
            WHERE token = %s AND used_at IS NULL AND expires_at > NOW()
            RETURNING *
            """,
            (int(message_id), token),
        ).fetchone()
        if claimed is None:
            return USED
        if claimed["action_id"] is not None:
            # Соседние кнопки того же предложения гаснут: следующий шаг получит новые коды.
            conn.execute(
                "UPDATE telegram_decision_tokens SET used_at = NOW() WHERE action_id = %s AND user_id = %s AND used_at IS NULL",
                (claimed["action_id"], user_id),
            )
    payload = dict(claimed["payload"] or {})
    action_id = int(claimed["action_id"]) if claimed["action_id"] is not None else None
    step = _STEPS.get(claimed["kind"])
    if step is None or action_id is None:
        return ""
    return step(user_id, chat_id, int(message_id), action_id, payload, edit_message)


def _action(user_id: int, action_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT a.*, ch.workspace_id AS chat_workspace_id
            FROM ai_actions a JOIN ai_chats ch ON ch.id = a.chat_id
            WHERE a.id = %s AND a.user_id = %s
            """,
            (action_id, user_id),
        ).fetchone()


def _campaign_name(user_id: int, args: dict) -> str:
    try:
        cid = int((args or {}).get("campaign_row_id"))
    except (TypeError, ValueError):
        return ""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT name FROM direct_campaigns WHERE id = %s AND user_id = %s", (cid, user_id)
        ).fetchone()
    return (row or {}).get("name") or ""


def _decided(text: str, url: str, chat_id: str, message_id: int, edit, note: str = ALREADY) -> str:
    edit(chat_id, message_id, _with_link(f"{text}\n\n{note}.", url), open_markup(url))
    return note


def _step_run(user_id, chat_id, message_id, action_id, payload, edit) -> str:
    """«Выполнить» → повторный вопрос с «Да, выполнить» / «Отмена»."""
    text, url = payload.get("text") or "", payload.get("url") or ""
    row = _action(user_id, action_id)
    if row is None or row["status"] != "pending":
        return _decided(text, url, chat_id, message_id, edit)
    args = dict(row["args"] or {})
    question = confirm_question(row["tool"], args, row["summary"], _campaign_name(user_id, args))
    with pool.connection() as conn:
        yes = _new_token(conn, user_id, chat_id, "yes", action_id, payload)
        no = _new_token(conn, user_id, chat_id, "no", action_id, payload)
    edit(chat_id, message_id, f"{text}\n\n{question}", markup([[_button("Да, выполнить", yes), _button("Отмена", no)]]))
    return ""


def _step_no(user_id, chat_id, message_id, action_id, payload, edit) -> str:
    """«Отмена» после вопроса — снова карточка с «Выполнить» / «Отклонить»."""
    text, url = payload.get("text") or "", payload.get("url") or ""
    row = _action(user_id, action_id)
    if row is None or row["status"] != "pending":
        return _decided(text, url, chat_id, message_id, edit)
    with pool.connection() as conn:
        reply = _decision_markup(conn, user_id, chat_id, action_id, payload)
    edit(chat_id, message_id, text, reply)
    return "Отменено"


def _step_reject(user_id, chat_id, message_id, action_id, payload, edit) -> str:
    from app.ai_agent import decide_action
    from app.plain_errors import plain_error
    from app.yandex import YandexError

    text, url = payload.get("text") or "", payload.get("url") or ""
    row = _action(user_id, action_id)
    if row is None or row["status"] != "pending":
        return _decided(text, url, chat_id, message_id, edit)
    try:
        decide_action(user_id, action_id, approve=False)
    except YandexError as exc:
        if exc.code == "decided":
            return _decided(text, url, chat_id, message_id, edit)
        return _decided(text, url, chat_id, message_id, edit, f"Не получилось: {plain_error(exc.code, exc.message)}")
    return _decided(text, url, chat_id, message_id, edit, "Отклонено")


def _step_yes(user_id, chat_id, message_id, action_id, payload, edit) -> str:
    """«Да, выполнить» — тем же decide_action, что и кабинет. Итог допишет action_finished."""
    from app import events
    from app.ai_agent import decide_action
    from app.plain_errors import plain_error
    from app.yandex import YandexError

    text, url = payload.get("text") or "", payload.get("url") or ""
    row = _action(user_id, action_id)
    if row is None or row["status"] != "pending":
        return _decided(text, url, chat_id, message_id, edit)
    # Сначала «выполняю», потом решение: быстрый итог action_finished не затрётся этим текстом.
    edit(chat_id, message_id, _with_link(f"{text}\n\nПринято — выполняю. Итог пришлю сюда.", url), open_markup(url))
    try:
        out = decide_action(user_id, action_id, approve=True)
    except YandexError as exc:
        if exc.code == "decided":
            return _decided(text, url, chat_id, message_id, edit)
        return _decided(text, url, chat_id, message_id, edit, f"Не получилось: {plain_error(exc.code, exc.message)}")
    events.record(user_id, "action_applied", action_id=action_id, source="telegram")
    if not out.get("job") and out.get("pending"):
        edit(
            chat_id,
            message_id,
            _with_link(
                f"{text}\n\nПринято. ИИ выполнит это, когда вы решите остальные предложения этого чата.", url
            ),
            open_markup(url),
        )
    return "Принято"


def _step_undo(user_id, chat_id, message_id, action_id, payload, edit) -> str:
    """«Вернуть как было» → повторный вопрос со списком того, что вернётся."""
    from app.undo import ai_action_undo_preview
    from app.yandex import YandexError

    text, url = payload.get("text") or "", payload.get("url") or ""
    try:
        preview = ai_action_undo_preview(user_id, action_id)
    except YandexError:
        preview = {"available": False, "reason": "Это вернуть нельзя"}
    if not preview["available"]:
        return _decided(text, url, chat_id, message_id, edit, preview["reason"] or "Это вернуть нельзя")
    steps = preview.get("steps") or []
    lines = ["Вернуть как было?"] + [f"• {s}" for s in steps[:5]]
    if len(steps) > 5:
        lines.append(f"… и ещё {len(steps) - 5}")
    lines += preview.get("warnings") or []
    # Что не вернётся — как «Это вернуть нельзя» в окне кабинета: иначе кажется, что вернётся всё.
    blocked = preview.get("blocked") or []
    lines += [f"Не вернётся: {b}" for b in blocked[:5]]
    if len(blocked) > 5:
        lines.append(f"… и ещё {len(blocked) - 5}, что не вернётся")
    with pool.connection() as conn:
        yes = _new_token(conn, user_id, chat_id, "undo_yes", action_id, payload)
        no = _new_token(conn, user_id, chat_id, "undo_no", action_id, payload)
    edit(
        chat_id,
        message_id,
        f"{text}\n\n" + "\n".join(lines),
        markup([[_button("Да, вернуть", yes), _button("Отмена", no)]]),
    )
    return ""


def _step_undo_no(user_id, chat_id, message_id, action_id, payload, edit) -> str:
    text, url = payload.get("text") or "", payload.get("url") or ""
    with pool.connection() as conn:
        reply = _undo_markup(conn, user_id, chat_id, action_id, payload)
    edit(chat_id, message_id, text, reply)
    return "Отменено"


def _step_undo_yes(user_id, chat_id, message_id, action_id, payload, edit) -> str:
    """«Да, вернуть» — задача отката, как «Вернуть как было» в карточке кабинета. Итог — undo_finished."""
    from app.job_handlers import submit
    from app.jobs import PRIORITY_USER
    from app.undo import ai_action_undo_preview
    from app.yandex import YandexError

    text, url = payload.get("text") or "", payload.get("url") or ""
    try:
        preview = ai_action_undo_preview(user_id, action_id)
    except YandexError:
        preview = {"available": False, "reason": "Это вернуть нельзя"}
    if not preview["available"]:
        return _decided(text, url, chat_id, message_id, edit, preview["reason"] or "Это вернуть нельзя")
    edit(chat_id, message_id, _with_link(f"{text}\n\nВозвращаю как было…", url), open_markup(url))
    tg = {"chat_id": chat_id, "message_id": message_id, "text": text, "url": url}
    if preview.get("blocked"):
        tg["partial"] = True  # часть решения вернуть нельзя — итог не «Вернули как было»
    submit("undo_action", user_id, {"log_ids": preview["log_ids"], "telegram": tg}, priority=PRIORITY_USER, max_attempts=1)
    return "Возвращаю"


_STEPS = {
    "run": _step_run,
    "no": _step_no,
    "reject": _step_reject,
    "yes": _step_yes,
    "undo": _step_undo,
    "undo_no": _step_undo_no,
    "undo_yes": _step_undo_yes,
}


# --- Итоги ---------------------------------------------------------------------------------------------


def action_finished(action_id: int) -> None:
    """Предложение, одобренное из Telegram, выполнено — итог в том же сообщении и «Вернуть как было»."""
    from app.ai_agent import _shown_tool_error
    from app.telegram_notify import edit_message
    from app.undo import ai_action_log_ids

    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT t.user_id, t.chat_id, t.message_id, t.payload, a.status, a.error, u.telegram_chat_id
            FROM telegram_decision_tokens t
            JOIN ai_actions a ON a.id = t.action_id
            JOIN users u ON u.id = t.user_id
            WHERE t.action_id = %s AND t.kind = 'yes' AND t.message_id IS NOT NULL
              AND t.used_at IS NOT NULL
            ORDER BY t.used_at DESC LIMIT 1
            """,
            (action_id,),
        ).fetchone()
    if row is None or str(row["telegram_chat_id"] or "") != str(row["chat_id"]):
        return
    if row["status"] not in ("done", "error"):
        return
    user_id, chat_id, message_id = int(row["user_id"]), str(row["chat_id"]), int(row["message_id"])
    payload = dict(row["payload"] or {})
    text, url = payload.get("text") or "", payload.get("url") or ""
    if row["status"] == "error":
        reason = _shown_tool_error(row["error"] or "") or "ошибка при выполнении"
        edit_message(chat_id, message_id, _with_link(f"{text}\n\nНе получилось: {reason}", url), open_markup(url))
        return
    done = f"{text}\n\nГотово."
    try:
        has_undo = bool(ai_action_log_ids(user_id, action_id))
    except Exception:  # noqa: BLE001
        log.exception("Записи журнала предложения %s", action_id)
        has_undo = False
    if not has_undo:
        edit_message(chat_id, message_id, _with_link(done, url), open_markup(url))
        return
    result_payload = {"text": done, "url": url}
    with pool.connection() as conn:
        reply = _undo_markup(conn, user_id, chat_id, action_id, result_payload)
    edit_message(chat_id, message_id, _with_link(done, url), reply)


def undo_finished(tg: dict, result: dict | None = None, error: str = "") -> None:
    """Откат, запущенный из Telegram, закончился — итог в том же сообщении."""
    from app.telegram_notify import edit_message

    if not isinstance(tg, dict) or not tg.get("chat_id") or not tg.get("message_id"):
        return
    text, url = tg.get("text") or "", tg.get("url") or ""
    if error:
        note = f"Не удалось вернуть: {error}"
    elif (result or {}).get("status") == "partial" or tg.get("partial"):
        note = "Вернули не всё — подробности в «Истории»."
    else:
        note = "Вернули как было."
    edit_message(tg["chat_id"], int(tg["message_id"]), _with_link(f"{text}\n\n{note}", url), open_markup(url))
