"""Оплата тарифа через ЮKassa.

Подключение — в админке (service_payments): shopId, секретный ключ (зашифрован), тестовый режим и
включение оплаты для пользователей. Проверка подключения — GET /v3/me.

Платёж: строка payments с суммой, зафиксированной при создании (своя цена тарифа при продлении, цена
со скидкой за переход при смене), → POST /v3/payments с ключом идемпотентности aim-{id} → пользователь
платит на странице ЮKassa и возвращается на «Тариф». Уведомлению ЮKassa не верим: по его id заново
читаем платёж через API и только тогда продлеваем подписку. Продление — в одной транзакции с отметкой
applied_at под блокировкой строки, так что повторное уведомление ничего не продлит второй раз.

Возвраты делают вручную в личном кабинете ЮKassa: сумма возврата подтягивается из платежа (уведомление
refund.succeeded, фоновая проверка или кнопка «Обновить» в админке). Подписку возврат сам не отменяет.

Чеки самозанятого: с 29.12.2025 ЮKassa не регистрирует их в «Мой налог» — админ пробивает чек сам и
отмечает это у платежа (receipt_done).
"""

from __future__ import annotations

import logging
import re
import time
from decimal import Decimal, InvalidOperation

import httpx

from app import events, plans, referrals
from app.clock import MSK, now_msk
from app.config import settings
from app.db import pool
from app.settings_store import public_base_url
from app.token_crypto import decrypt_token, encrypt_token
from app.yandex import YandexError

log = logging.getLogger(__name__)

API_URL = "https://api.yookassa.ru/v3"
TIMEOUT_SECONDS = 20.0
WEBHOOK_PATH = "/api/yookassa/webhook"
# События, на которые нужно подписаться в личном кабинете ЮKassa (HTTP-уведомления).
WEBHOOK_EVENTS = ("payment.succeeded", "payment.canceled", "refund.succeeded")

OPEN = ("pending", "waiting_for_capture")
STATUS_LABELS = {
    "new": "создаётся",
    "pending": "ждёт оплаты",
    "waiting_for_capture": "ждёт подтверждения",
    "succeeded": "оплачен",
    "canceled": "отменён",
    "error": "ошибка",
}
CANCEL_REASONS = {
    "expired_on_confirmation": "Время на оплату истекло",
    "insufficient_funds": "Недостаточно средств",
    "card_expired": "Срок действия карты истёк",
    "3d_secure_failed": "Не пройдено подтверждение 3-D Secure",
    "call_issuer": "Банк отклонил оплату — обратитесь в банк",
    "fraud_suspected": "Банк заподозрил мошенничество",
    "general_decline": "Банк отклонил оплату",
    "payment_method_limit_exceeded": "Превышен лимит по карте или кошельку",
    "payment_method_restricted": "Оплата этим способом запрещена",
    "invalid_card_number": "Неверный номер карты",
    "invalid_csc": "Неверный код CVV/CVC",
    "issuer_unavailable": "Банк недоступен — повторите позже",
    "country_forbidden": "Оплата картой этой страны запрещена",
    "canceled_by_merchant": "Платёж отменён",
    "internal_timeout": "Технический сбой ЮKassa — повторите позже",
}

NOT_READY = "Онлайн-оплата пока не подключена — напишите в поддержку, подключим тариф вручную"
SHOP_ID_RE = re.compile(r"^\d{1,12}$")
PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


# --- Подключение (админка) ----------------------------------------------------------------------


def _row() -> dict:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM service_payments WHERE id = 1").fetchone() or {}


def _configured(row: dict) -> bool:
    return bool(row.get("shop_id") and row.get("secret_key_enc"))


def is_ready() -> bool:
    """Пользователи могут платить: магазин подключён и оплата включена."""
    row = _row()
    return _configured(row) and bool(row.get("enabled"))


def webhook_url() -> str:
    base = public_base_url()
    return f"{base}{WEBHOOK_PATH}" if base else ""


def get_settings() -> dict:
    from app.ai import _key_hint

    row = _row()
    return {
        "configured": _configured(row),
        "enabled": bool(row.get("enabled")),
        "ready": _configured(row) and bool(row.get("enabled")),
        "shop_id": row.get("shop_id") or "",
        "has_key": bool(row.get("secret_key_enc")),
        "key_hint": _key_hint(row.get("secret_key_enc") or ""),
        "test_mode": bool(row.get("test_mode", True)),
        "checked_at": row["checked_at"].isoformat() if row.get("checked_at") else None,
        "check_error": row.get("check_error") or "",
        "check_test": row.get("check_test"),
        "webhook_url": webhook_url(),
        "webhook_events": list(WEBHOOK_EVENTS),
    }


def save_settings(*, shop_id: str = "", secret_key: str | None = None, test_mode: bool = True, enabled: bool = False) -> dict:
    """secret_key: None — оставить прежний, "" — удалить. Новый магазин или ключ сбрасывает итог проверки."""
    shop_id = (shop_id or "").strip()
    if shop_id and not SHOP_ID_RE.match(shop_id):
        raise YandexError("payments-field", "shopId — число из личного кабинета ЮKassa (Интеграция → Ключи API)")
    current = _row()
    if secret_key is None:
        key_enc = current.get("secret_key_enc") or ""
    else:
        secret_key = secret_key.strip()
        if secret_key and (len(secret_key) > 200 or any(ch.isspace() for ch in secret_key)):
            raise YandexError("payments-field", "Секретный ключ — одной строкой без пробелов (live_… или test_…)")
        key_enc = encrypt_token(secret_key) if secret_key else ""
    if key_enc and not shop_id:
        raise YandexError("payments-field", "Укажите shopId магазина, к которому выпущен ключ")
    if enabled and not (shop_id and key_enc):
        raise YandexError("payments-field", "Включить оплату можно после того, как указаны shopId и секретный ключ")
    changed = key_enc != (current.get("secret_key_enc") or "") or shop_id != (current.get("shop_id") or "")
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE service_payments SET shop_id = %s, secret_key_enc = %s, test_mode = %s, enabled = %s,
                   checked_at = CASE WHEN %s THEN NULL ELSE checked_at END,
                   check_error = CASE WHEN %s THEN '' ELSE check_error END,
                   check_test = CASE WHEN %s THEN NULL ELSE check_test END,
                   updated_at = NOW()
            WHERE id = 1
            """,
            (shop_id, key_enc, bool(test_mode), bool(enabled), changed, changed, changed),
        )
    return get_settings()


def _credentials(row: dict | None = None) -> tuple[str, str]:
    row = row if row is not None else _row()
    if not _configured(row):
        raise YandexError("payments-off", NOT_READY)
    try:
        return row["shop_id"], decrypt_token(row["secret_key_enc"])
    except RuntimeError as exc:
        raise YandexError("payments-off", NOT_READY) from exc


def check_connection() -> dict:
    """GET /v3/me: ключ подходит, магазин тестовый или боевой. Итог запоминается."""
    row = _row()
    creds = _credentials(row)
    try:
        me = _request("GET", "/me", creds=creds)
    except YandexError as exc:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE service_payments SET checked_at = NOW(), check_error = %s, check_test = NULL WHERE id = 1",
                ((exc.message or exc.code)[:500],),
            )
        raise
    test = bool(me.get("test"))
    warning = ""
    if test != bool(row.get("test_mode")):
        warning = (
            "Ключ от тестового магазина, а тестовый режим выключен"
            if test
            else "Ключ от боевого магазина, а включён тестовый режим — платежи будут настоящими"
        )
    with pool.connection() as conn:
        conn.execute(
            "UPDATE service_payments SET checked_at = NOW(), check_error = '', check_test = %s WHERE id = 1", (test,)
        )
    return {
        "ok": True,
        "account_id": str(me.get("account_id") or ""),
        "test": test,
        "status": me.get("status") or "",
        "warning": warning,
    }


# --- Запросы к ЮKassa ---------------------------------------------------------------------------


def _error(response: httpx.Response) -> YandexError:
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    detail = str(payload.get("description") or "").strip()
    log.warning("ЮKassa HTTP %s %s %s", response.status_code, payload.get("code"), detail or response.text[:300])
    if response.status_code == 401:
        return YandexError("payments-auth", "ЮKassa не приняла shopId или секретный ключ")
    if response.status_code == 403:
        return YandexError("payments-auth", f"ЮKassa запретила операцию: {detail}" if detail else "ЮKassa запретила операцию")
    if response.status_code == 404:
        return YandexError("payments-remote", "Платёж не найден в ЮKassa")
    if response.status_code == 429:
        return YandexError("payments-remote", "Слишком много запросов к ЮKassa — повторите через минуту")
    if response.status_code == 400:
        return YandexError("payments-remote", f"ЮKassa отклонила запрос: {detail}" if detail else "ЮKassa отклонила запрос")
    return YandexError("payments-remote", f"ЮKassa недоступна (HTTP {response.status_code}) — повторите позже")


def _request(
    method: str, path: str, *, body: dict | None = None, idempotence_key: str = "", creds: tuple[str, str] | None = None
) -> dict:
    shop_id, secret = creds or _credentials()
    headers = {"Content-Type": "application/json"}
    if idempotence_key:
        headers["Idempotence-Key"] = idempotence_key
    for attempt in range(3):
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                response = client.request(method, f"{API_URL}{path}", json=body, headers=headers, auth=(shop_id, secret))
        except httpx.HTTPError as exc:
            # Создание платежа безопасно повторять: тот же ключ идемпотентности вернёт тот же платёж.
            if attempt < 2 and (method == "GET" or idempotence_key):
                time.sleep(0.5 * (attempt + 1))
                continue
            raise YandexError("payments-remote", "ЮKassa не ответила — повторите позже") from exc
        # 202 — запрос с этим ключом идемпотентности ещё обрабатывается: повторяем.
        if response.status_code == 202 and attempt < 2:
            time.sleep(1.0)
            continue
        if response.status_code >= 500 and attempt < 2 and (method == "GET" or idempotence_key):
            time.sleep(0.5 * (attempt + 1))
            continue
        if response.status_code >= 400 or response.status_code == 202:
            raise _error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise YandexError("payments-remote", "ЮKassa вернула не JSON") from exc
        return data if isinstance(data, dict) else {}
    raise YandexError("payments-remote", "ЮKassa не ответила — повторите позже")


def fetch(provider_id: str) -> dict:
    if not PROVIDER_ID_RE.match(provider_id or ""):
        raise YandexError("payments-remote", "Неверный id платежа ЮKassa")
    return _request("GET", f"/payments/{provider_id}")


# --- Платежи ------------------------------------------------------------------------------------


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("-1")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _public(row: dict) -> dict:
    status = row["status"]
    reason = row.get("cancel_reason") or ""
    return {
        "id": int(row["id"]),
        "created_at": _iso(row["created_at"]),
        "paid_at": _iso(row.get("paid_at")),
        "plan_id": row.get("plan_id"),
        "plan_name": row.get("plan_name") or "",
        "days": int(row["days"]),
        "months": int(row.get("months") or 1),
        "kind": row["kind"],
        "amount_rub": float(row["amount_rub"]),
        "refunded_rub": float(row.get("refunded_rub") or 0),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "applied": row.get("applied_at") is not None,
        "last_day": row["last_day"].isoformat() if row.get("last_day") else None,
        "test": bool(row.get("test")),
        "cancel_reason": CANCEL_REASONS.get(reason, reason) if status in ("canceled", "error") else "",
        # Ссылку на оплату отдаём, пока платёж ждёт: можно вернуться и заплатить.
        "confirmation_url": (row.get("confirmation_url") or "") if status == "pending" else "",
    }


def _admin(row: dict) -> dict:
    return {
        **_public(row),
        "user_id": row.get("user_id"),
        "email": row.get("email") or "",
        "provider_id": row.get("provider_id") or "",
        "receipt_done": bool(row.get("receipt_done")),
        "applied_at": _iso(row.get("applied_at")),
        "cancel_code": row.get("cancel_reason") or "",
    }


def quote(user_id: int, plan_id: int, months: int = 1) -> dict:
    """Что пользователь заплатит за тариф на months сроков (1, 3 или 12): продление своего — по цене
    тарифа, переход — со скидкой за переход; за 3 и 12 сроков — ещё скидка за срок из тарифа."""
    if months not in plans.TERMS:
        raise YandexError("payments-field", "Оплатить можно на 1, 3 или 12 месяцев")
    offer = next((o for o in plans.offers(user_id) if o["id"] == plan_id), None)
    if offer is None:
        raise YandexError("not-found", "Этот тариф сейчас нельзя оплатить")
    sub = plans.subscription(user_id)
    renew = bool(sub["plan"] and sub["plan"]["id"] == plan_id)
    price = float(offer["price_rub"] if renew else offer["price_with_discount"])
    term_pct = float(offer["term_discounts"].get(str(months), 0))
    amount = round(price * months * (100 - term_pct) / 100, 2)
    return {
        "plan_id": plan_id,
        "plan_name": offer["name"],
        "months": months,
        "days": int(offer["days"]) * months,
        "kind": "renew" if renew else "change",
        # Цена одного срока (уже со скидкой за переход), без скидки за срок и итог.
        "price_rub": round(price, 2),
        "full_rub": round(price * months, 2),
        "change_discount_pct": 0.0 if renew else float(offer["discount_pct"]),
        "term_discount_pct": term_pct,
        "amount_rub": amount,
        "extends": renew and not sub["expired"] and sub["ends_at"] is not None,
    }


def create_payment(user_id: int, plan_id: int, months: int = 1) -> dict:
    """Платёж в ЮKassa за тариф → {"payment": ..., "confirmation_url"} — туда отправляем пользователя."""
    row = _row()
    if not (_configured(row) and row.get("enabled")):
        raise YandexError("payments-off", NOT_READY)
    base = public_base_url()
    if not base:
        raise YandexError("payments-off", "Не задан адрес сайта (PUBLIC_BASE_URL) — ЮKassa некуда вернуть пользователя")
    q = quote(user_id, plan_id, months)
    if q["amount_rub"] < 1:
        raise YandexError("payments-field", "Этот тариф нельзя оплатить онлайн — напишите в поддержку")
    with pool.connection() as conn:
        pay = conn.execute(
            """
            INSERT INTO payments (user_id, plan_id, plan_name, days, months, kind, amount_rub, test)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id, plan_id, q["plan_name"], q["days"], q["months"], q["kind"], q["amount_rub"],
                bool(row.get("test_mode")),
            ),
        ).fetchone()
    payment_id = int(pay["id"])
    description = f"{settings.app_name}: тариф «{q['plan_name']}» на {q['days']} дн."[:128]
    body = {
        "amount": {"value": f"{q['amount_rub']:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": f"{base}/cabinet/plan?payment={payment_id}"},
        "description": description,
        "metadata": {"payment_id": str(payment_id), "user_id": str(user_id), "plan_id": str(plan_id)},
    }
    try:
        remote = _request("POST", "/payments", body=body, idempotence_key=f"aim-{payment_id}", creds=_credentials(row))
    except YandexError as exc:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE payments SET status = 'error', cancel_reason = %s, updated_at = NOW() WHERE id = %s",
                ((exc.message or exc.code)[:300], payment_id),
            )
        raise
    saved = sync_remote(remote, payment_id=payment_id)
    url = ((remote.get("confirmation") or {}).get("confirmation_url")) or ""
    if not url:
        raise YandexError("payments-remote", "ЮKassa не вернула ссылку на оплату — повторите позже")
    return {"payment": saved, "confirmation_url": url}


def _find(conn, provider_id: str, payment_id: int | None, metadata: dict) -> dict | None:
    row = conn.execute("SELECT * FROM payments WHERE provider_id = %s FOR UPDATE", (provider_id,)).fetchone()
    if row is not None:
        return row
    # Платёж создан, а id ЮKassa ещё не сохранён (сбой между запросом и записью) — ищем по нашему id.
    meta_id = payment_id or (int(metadata["payment_id"]) if str(metadata.get("payment_id") or "").isdigit() else None)
    if meta_id is None:
        return None
    return conn.execute(
        "SELECT * FROM payments WHERE id = %s AND provider_id IS NULL FOR UPDATE", (meta_id,)
    ).fetchone()


def sync_remote(remote: dict, *, payment_id: int | None = None) -> dict | None:
    """Записать состояние платежа из ответа API ЮKassa; оплачен — продлить подписку (один раз)."""
    provider_id = str(remote.get("id") or "")
    if not PROVIDER_ID_RE.match(provider_id):
        log.warning("ЮKassa: платёж без id: %s", remote)
        return None
    metadata = remote.get("metadata") or {}
    amount = remote.get("amount") or {}
    status = str(remote.get("status") or "")
    if status not in STATUS_LABELS:
        status = "error"
    refunded = _money((remote.get("refunded_amount") or {}).get("value"))
    reason = str((remote.get("cancellation_details") or {}).get("reason") or "")
    url = str((remote.get("confirmation") or {}).get("confirmation_url") or "")
    paid_at = remote.get("captured_at") if status == "succeeded" else None
    with pool.connection() as conn, conn.transaction():
        row = _find(conn, provider_id, payment_id, metadata)
        if row is None:
            log.info("ЮKassa: платёж %s не наш — пропускаем", provider_id)
            return None
        if _money(amount.get("value")) != _money(row["amount_rub"]) or amount.get("currency") != "RUB":
            # Сумма не та, что мы просили, — подписку не трогаем, разбирается админ.
            log.error("ЮKassa: сумма платежа %s не совпала: %s против %s", provider_id, amount, row["amount_rub"])
            status, reason = "error", "Сумма в ЮKassa не совпала с суммой платежа"
        before_refund = _money(row["refunded_rub"])
        refunded = max(refunded, Decimal("0"))
        row = conn.execute(
            """
            UPDATE payments SET provider_id = %s, status = %s, test = %s, refunded_rub = %s,
                   cancel_reason = %s,
                   confirmation_url = CASE WHEN %s <> '' THEN %s ELSE confirmation_url END,
                   paid_at = COALESCE(paid_at, %s::timestamptz), updated_at = NOW()
            WHERE id = %s
            RETURNING *
            """,
            (
                provider_id,
                status,
                bool(remote.get("test", row["test"])),
                refunded,
                reason[:300] if status in ("canceled", "error") else "",
                url,
                url,
                paid_at,
                row["id"],
            ),
        ).fetchone()
    if refunded > before_refund and row["user_id"]:
        events.record(
            row["user_id"], "refund", amount_rub=float(refunded - before_refund), payment_id=int(row["id"]), plan_id=row["plan_id"]
        )
    if status == "succeeded" and row["applied_at"] is None:
        row = apply(int(row["id"])) or row
    return _public(row)


def apply(payment_id: int) -> dict | None:
    """Продлить подписку по оплаченному платежу. Под блокировкой строки: второй вызов ничего не делает."""
    now = now_msk()
    result = None
    with pool.connection() as conn, conn.transaction():
        pay = conn.execute("SELECT * FROM payments WHERE id = %s FOR UPDATE", (payment_id,)).fetchone()
        if pay is None or pay["status"] != "succeeded" or pay["applied_at"] is not None or pay["user_id"] is None:
            return pay
        if pay["plan_id"] is None:
            log.error("Платёж %s оплачен, а тариф удалён — продлите подписку вручную", payment_id)
            return conn.execute(
                "UPDATE payments SET cancel_reason = %s, updated_at = NOW() WHERE id = %s RETURNING *",
                ("Тариф удалён — продлите подписку вручную", payment_id),
            ).fetchone()
        result = plans.extend_or_switch(conn, int(pay["user_id"]), int(pay["plan_id"]), int(pay["days"]), now)
        # Первая настоящая оплата приглашённого по личной ссылке — дни ему и пригласившему.
        bonus = None if pay["test"] else referrals.grant_bonus(conn, int(pay["user_id"]), payment_id, now)
        if bonus and bonus["invitee"]["days"]:
            result["ends_at"] = bonus["invitee"]["ends_at"]
        pay = conn.execute(
            """
            UPDATE payments SET applied_at = NOW(), paid_at = COALESCE(paid_at, NOW()), last_day = %s, updated_at = NOW()
            WHERE id = %s RETURNING *
            """,
            (plans.last_day(result["ends_at"]), payment_id),
        ).fetchone()
    user_id = int(pay["user_id"])
    events.record(
        user_id,
        "payment",
        amount_rub=float(pay["amount_rub"]),
        plan_id=pay["plan_id"],
        last_day=pay["last_day"].isoformat() if pay["last_day"] else None,
        renew=result["renewed"],
        payment_id=payment_id,
        test=bool(pay["test"]),
    )
    if result["was_expired"]:
        plans.resume(user_id)
    if bonus:
        _record_bonus(user_id, bonus, payment_id)
    return pay


def _record_bonus(invitee_id: int, bonus: dict, payment_id: int) -> None:
    for uid, part, role in ((invitee_id, bonus["invitee"], "invitee"), (bonus["referrer_id"], bonus["referrer"], "referrer")):
        if not part["days"]:
            continue
        plan = plans.subscription(uid)["plan"]
        events.record(
            uid,
            "ref_bonus",
            role=role,
            days=part["days"],
            plan_id=plan["id"] if plan else None,
            last_day=plans.last_day(part["ends_at"]),
            payment_id=payment_id,
        )
        if part["was_expired"] and uid != invitee_id:
            plans.resume(uid)


def handle_notification(body: dict) -> str:
    """HTTP-уведомление ЮKassa. Телу не верим: берём id и перечитываем платёж через API.

    Ошибка запроса к ЮKassa пробрасывается — вернём 500, и ЮKassa пришлёт уведомление ещё раз.
    """
    event = str((body or {}).get("event") or "")
    obj = (body or {}).get("object") or {}
    if event.startswith("payment."):
        provider_id = str(obj.get("id") or "")
    elif event.startswith("refund."):
        provider_id = str(obj.get("payment_id") or "")
    else:
        return "ignored"
    if not PROVIDER_ID_RE.match(provider_id):
        return "ignored"
    with pool.connection() as conn:
        known = conn.execute("SELECT 1 FROM payments WHERE provider_id = %s", (provider_id,)).fetchone()
    if known is None:
        return "unknown"
    sync_remote(fetch(provider_id))
    return "ok"


def _get(payment_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT p.*, u.email FROM payments p LEFT JOIN users u ON u.id = p.user_id WHERE p.id = %s", (payment_id,)
        ).fetchone()


def refresh(payment_id: int) -> dict:
    """Перечитать платёж из ЮKassa (кнопка «Обновить», возврат на сайт после оплаты)."""
    row = _get(payment_id)
    if row is None:
        raise YandexError("not-found", "Платёж не найден")
    if row["provider_id"]:
        sync_remote(fetch(row["provider_id"]))
        row = _get(payment_id)
    return row


def check_for_user(user_id: int, payment_id: int) -> dict:
    row = _get(payment_id)
    if row is None or row["user_id"] != user_id:
        raise YandexError("not-found", "Платёж не найден")
    if row["status"] in OPEN and row["provider_id"]:
        try:
            row = refresh(payment_id)
        except YandexError:
            log.warning("Не удалось перечитать платёж %s", payment_id, exc_info=True)
    return _public(row)


def list_for_user(user_id: int, limit: int = 30) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM payments WHERE user_id = %s AND status <> 'new'
            ORDER BY created_at DESC, id DESC LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    return [_public(r) for r in rows]


def user_view(user_id: int) -> dict:
    """Для страницы «Тариф»: можно ли платить, тестовый ли режим, платежи."""
    row = _row()
    return {
        "payments_enabled": _configured(row) and bool(row.get("enabled")),
        "payments_test": bool(row.get("test_mode")),
        "payments": list_for_user(user_id),
    }


# --- Админка ------------------------------------------------------------------------------------


def admin_list(*, user_id: int | None = None, status: str = "", limit: int = 300) -> dict:
    where, params = ["p.status <> 'new'"], []
    if user_id:
        where.append("p.user_id = %s")
        params.append(user_id)
    if status == "paid":
        where.append("p.status = 'succeeded'")
    elif status == "refunded":
        where.append("p.refunded_rub > 0")
    elif status == "no_receipt":
        where.append("p.status = 'succeeded' AND NOT p.test AND NOT p.receipt_done")
    elif status in STATUS_LABELS:
        where.append("p.status = %s")
        params.append(status)
    month_start = now_msk().astimezone(MSK).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT p.*, u.email FROM payments p LEFT JOIN users u ON u.id = p.user_id
            WHERE {' AND '.join(where)}
            ORDER BY p.created_at DESC, p.id DESC LIMIT %s
            """,
            (*params, max(1, min(int(limit), 1000))),
        ).fetchall()
        totals = conn.execute(
            """
            SELECT COUNT(*) FILTER (WHERE created_at >= %(m)s) AS month_count,
                   COALESCE(SUM(amount_rub - refunded_rub) FILTER (WHERE created_at >= %(m)s), 0) AS month_rub,
                   COUNT(*) AS total_count,
                   COALESCE(SUM(amount_rub - refunded_rub), 0) AS total_rub,
                   COALESCE(SUM(refunded_rub), 0) AS refunded_rub,
                   COUNT(*) FILTER (WHERE NOT receipt_done) AS no_receipt
            FROM payments WHERE status = 'succeeded' AND NOT test
            """,
            {"m": month_start},
        ).fetchone()
    return {
        "items": [_admin(r) for r in rows],
        "summary": {
            "month_count": int(totals["month_count"]),
            "month_rub": float(totals["month_rub"]),
            "total_count": int(totals["total_count"]),
            "total_rub": float(totals["total_rub"]),
            "refunded_rub": float(totals["refunded_rub"]),
            "no_receipt": int(totals["no_receipt"]),
        },
    }


def admin_refresh(payment_id: int) -> dict:
    return _admin(refresh(payment_id))


def set_receipt(payment_id: int, done: bool) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            "UPDATE payments SET receipt_done = %s, updated_at = NOW() WHERE id = %s RETURNING id", (bool(done), payment_id)
        ).fetchone()
    if row is None:
        raise YandexError("not-found", "Платёж не найден")
    return _admin(_get(payment_id))


def month_revenue() -> dict:
    """Для сводки админки: оплаты за месяц без тестовых и за вычетом возвратов."""
    summary = admin_list(limit=1)["summary"]
    return {"month_count": summary["month_count"], "month_rub": summary["month_rub"], "no_receipt": summary["no_receipt"]}


# --- Фоновая сверка -----------------------------------------------------------------------------


def due_for_sync(now=None) -> list[int]:
    """Какие платежи перечитать: незавершённые за 3 дня и оплаченные за 30 дней раз в сутки (ловим возвраты)."""
    now = now or now_msk()
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id FROM payments
            WHERE provider_id IS NOT NULL AND (
                (status IN ('pending', 'waiting_for_capture') AND created_at > %(now)s - INTERVAL '3 days')
                OR (status = 'succeeded' AND created_at > %(now)s - INTERVAL '30 days'
                    AND updated_at < %(now)s - INTERVAL '1 day')
            )
            ORDER BY created_at
            LIMIT 200
            """,
            {"now": now},
        ).fetchall()
    return [int(r["id"]) for r in rows]


def sync_open(now=None) -> dict:
    if not _configured(_row()):
        return {"checked": 0, "errors": 0}
    checked = errors = 0
    for payment_id in due_for_sync(now):
        try:
            refresh(payment_id)
            checked += 1
        except YandexError:
            errors += 1
            log.warning("Сверка платежа %s не удалась", payment_id, exc_info=True)
    return {"checked": checked, "errors": errors}

