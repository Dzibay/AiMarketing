"""Срочное: о чём стоит узнать в течение часа — запись в колокольчике и сообщение в Telegram.

Оба канала включены по умолчанию (строка «Срочное» в настройках уведомлений, notifications.KINDS["urgent"]).

События:
- деньги на счёте логина закончились или их хватит меньше чем на 2 дня (проблема «balance» сводки с
  серьёзностью «критично»);
- кампания отклонена модерацией (статус REJECTED) или перестала показываться не по воле пользователя
  (состояние OFF при расходе за неделю). Остановленные самим пользователем (SUSPENDED) и архивные не
  трогаем; кампанию, вставшую на логине без денег, отдельно не называем — о деньгах уже сказано;
- расход без заявок: вчера кампания потратила не меньше max(1,5 × её обычной цены заявки, 500 ₽) и не
  принесла ни одной заявки. Обычная цена заявки — расход / конверсии за 30 дней до вчера, если конверсий
  там не меньше 3; иначе — цель проекта по цене заявки; нет ни того, ни другого — правило молчит.
  День считаем закрытым, только если его статистику перечитали после полуночи;
- заявки подорожали: цена заявки за последние 3 дня (до вчера включительно) не меньше 2 × цены за 30 дней
  перед ними; нужно не меньше 3 конверсий в базе, хотя бы одна за 3 дня (без них это «расход без заявок»)
  и расход за 3 дня от 500 ₽;
- итог модерации после отправки из сервиса (журнал действий за 7 дней): «прошла — можно включать» или
  «отклонено N объявлений: причина… — исправьте с ИИ». Один раз на отправку; ссылка — на страницу кампании.

Проверка раз в час (workers: «urgent» → задача urgent_check). Один объект — не чаще раза в сутки
(dedup_key urgent:<объект>:<день> и проверка «за последние 24 часа»). Тихие часы 22:00–09:00 МСК:
запись в колокольчике сразу, а Telegram — первой проверкой после 9:00 и только если событие ещё есть.
Скрытое пользователем («⋯» у сигнала, snoozes) не присылается: события проверяются как проблемы того же типа.
Текст: клиент и проект, одно предложение, одна ссылка.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from app import snoozes
from app.clock import MSK
from app.db import pool

log = logging.getLogger(__name__)

QUIET_FROM_HOUR = 22
QUIET_TO_HOUR = 9
REPEAT_HOURS = 24
FLOOR_KOPECKS = 50_000  # 500 ₽: копеечный расход без заявок не тревожит
NO_CONV_RATIO = 1.5
CPA_SPIKE_RATIO = 2.0
MIN_BASE_CONVERSIONS = 3
BASE_DAYS = 30
RECENT_DAYS = 3
MONEY_DAYS = 2
MODERATION_LOOKBACK_DAYS = 7
FINAL_AD_STATUSES = ("ACCEPTED", "REJECTED")  # модерация объявления закончилась


# --- Чистые правила (покрыты юнит-тестами) -------------------------------------------------------------


def quiet(now: datetime) -> bool:
    """Тихие часы по Москве: 22:00–09:00."""
    hour = now.astimezone(MSK).hour
    return hour >= QUIET_FROM_HOUR or hour < QUIET_TO_HOUR


def _sum(history: dict, days: list[date]) -> tuple[int, int]:
    cost = conv = 0
    for d in days:
        c, n = history.get(d, (0, 0))
        cost += c
        conv += n
    return cost, conv


def no_conv_event(history: dict[date, tuple[int, int]], yesterday: date, target_cpa_kopecks: float | None = None):
    """Вчера расход ≥ max(1,5 × обычная цена заявки, 500 ₽) и ни одной заявки → {cost, cpa, threshold} или None.

    history: день → (расход в копейках, конверсии). Обычная цена — за 30 дней до вчера при ≥ 3 конверсиях,
    иначе цель проекта по цене заявки.
    """
    cost, conv = history.get(yesterday, (0, 0))
    if conv or cost <= 0:
        return None
    base_cost, base_conv = _sum(history, [yesterday - timedelta(days=i) for i in range(1, BASE_DAYS + 1)])
    if base_conv >= MIN_BASE_CONVERSIONS:
        cpa = base_cost / base_conv
    elif target_cpa_kopecks:
        cpa = float(target_cpa_kopecks)
    else:
        return None
    threshold = max(NO_CONV_RATIO * cpa, FLOOR_KOPECKS)
    if cost < threshold:
        return None
    return {"cost": cost, "cpa": round(cpa), "threshold": round(threshold)}


def cpa_spike_event(history: dict[date, tuple[int, int]], yesterday: date):
    """Цена заявки за 3 дня ≥ 2 × цены за 30 дней перед ними → {cpa, base_cpa, ratio, conversions} или None."""
    recent = [yesterday - timedelta(days=i) for i in range(RECENT_DAYS)]
    base = [yesterday - timedelta(days=i) for i in range(RECENT_DAYS, RECENT_DAYS + BASE_DAYS)]
    cost, conv = _sum(history, recent)
    base_cost, base_conv = _sum(history, base)
    if conv < 1 or base_conv < MIN_BASE_CONVERSIONS or base_cost <= 0 or cost < FLOOR_KOPECKS:
        return None
    cpa, base_cpa = cost / conv, base_cost / base_conv
    if cpa < CPA_SPIKE_RATIO * base_cpa:
        return None
    return {"cpa": round(cpa), "base_cpa": round(base_cpa), "ratio": round(cpa / base_cpa, 1), "conversions": conv}


def _aware(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def moderation_outcome(campaign: dict, ads: list[dict], sent_at: datetime) -> dict | None:
    """Итог модерации после отправки в sent_at, или None — ещё идёт либо данные не перечитаны.

    campaign: {status, state, synced_at, status_clarification}; ads — объявления, которые отправляли
    ({status, state, synced_at, title, reason, extras}).

    synced_at двигают и правки из сервиса (тексты, бюджет, дополнения), поэтому одного «перечитали после
    отправки» мало: итог есть, только когда у каждого отправленного объявления окончательный статус
    Директа (ACCEPTED или REJECTED) и строка обновлена после отправки. Пустой статус (объявление создал
    сервис), MODERATION (его ставит сама отправка, см. actions.moderate_campaign), PREACCEPTED и прочее —
    модерация ещё идёт. Черновик, перечитанный после отправки, не ушёл на модерацию — его не ждём.
    """
    synced = _aware(campaign.get("synced_at"))
    if campaign.get("status") == "MODERATION" or synced is None or synced <= sent_at:
        return None
    live = [a for a in ads if a.get("state") != "ARCHIVED"]

    def fresh(a: dict) -> bool:
        ts = _aware(a.get("synced_at"))
        return ts is not None and ts > sent_at

    def waiting(a: dict) -> bool:
        status = a.get("status") or ""
        if status == "DRAFT":
            return not fresh(a)  # черновик, не перечитанный после отправки, — его статус ещё старый
        return status not in FINAL_AD_STATUSES or not fresh(a)

    if any(waiting(a) for a in live):
        return None
    rejected = [a for a in live if a.get("status") == "REJECTED"]
    if campaign.get("status") == "REJECTED" or rejected:
        return {
            "result": "rejected",
            "count": len(rejected),
            "rows": rejected,
            "reason": campaign.get("status_clarification") or "",
        }
    if not any(a.get("status") == "ACCEPTED" for a in live):
        return None  # принятых после отправки нет (одни черновики) — рано говорить «прошла»
    return {"result": "accepted", "running": campaign.get("state") == "ON"}


def money_critical(balance: dict) -> bool:
    """Строка блока «Деньги» сводки: кончились или меньше чем на 2 дня (при расходе)."""
    if balance.get("balance_kopecks") is None or not balance.get("spend_per_day_kopecks"):
        return False
    days = balance.get("days_on_balance")
    return balance["balance_kopecks"] <= 0 or (days is not None and days < MONEY_DAYS)


def where_of(p: dict) -> str:
    """Клиент и проект одной строкой: «Иванов · Окна»."""
    ws, client = p.get("workspace_name") or "", p.get("client_name") or ""
    if client and ws and client != ws:
        return f"{client} · {ws}"
    return ws or client or "Без проекта"


def object_of(dedup_key: str) -> str:
    """urgent:<объект>:<день> → <объект>."""
    body = dedup_key[len("urgent:") :] if dedup_key.startswith("urgent:") else dedup_key
    return body.rsplit(":", 1)[0]


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _num(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _campaign_link(ws_id: int | None, campaign_row_id: int) -> str:
    return f"/w/{ws_id}/campaigns/{campaign_row_id}" if ws_id else "/cabinet/campaigns"


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(str(text or "").split()).rstrip(".")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --- Сбор событий -------------------------------------------------------------------------------------


def _event(obj: str, p: dict, headline: str, sentence: str, link: str, *, tg_link: str = "") -> dict:
    return {
        "object": obj,
        "where": where_of(p),
        "headline": headline,
        "sentence": sentence,
        "link": link,
        "tg_link": tg_link,
        "workspace_id": p.get("workspace_id"),
    }


def _load_history(user_id: int, date_from: date, date_to: date) -> list[dict]:
    with pool.connection() as conn:
        return list(
            conn.execute(
                """
                SELECT account_id, campaign_id, day, SUM(cost_kopecks) AS cost, SUM(conversions) AS conv,
                       MAX(updated_at) AS updated_at
                FROM stats_daily
                WHERE user_id = %s AND source = 'direct' AND campaign_id IS NOT NULL AND day >= %s AND day <= %s
                GROUP BY account_id, campaign_id, day
                """,
                (user_id, date_from, date_to),
            ).fetchall()
        )


def _running_campaigns(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        return list(
            conn.execute(
                """
                SELECT c.id, c.account_id, c.campaign_id, c.name, c.status, wc.workspace_id
                FROM direct_campaigns c
                JOIN direct_accounts a ON a.id = c.account_id AND a.enabled
                LEFT JOIN workspace_campaigns wc ON wc.campaign_row_id = c.id
                WHERE c.user_id = %s AND c.state = 'ON'
                """,
                (user_id,),
            ).fetchall()
        )


def _json(text) -> dict:
    """payload / result журнала — JSON в тексте; битый или пустой — пустой словарь."""
    if isinstance(text, dict):
        return text
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _count(value) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _moderation_sends(user_id: int, since: datetime) -> list[dict]:
    """Отправки на модерацию из сервиса: кампания целиком (moderate, ушло sent > 0) и новые объявления A/B
    (ads, moderated > 0). Свежие сверху."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, action, payload, result, targets, created_at FROM action_logs
            WHERE user_id = %s AND created_at > %s AND status <> 'error' AND action IN ('moderate', 'ads')
            ORDER BY created_at DESC, id DESC
            """,
            (user_id, since),
        ).fetchall()
    out = []
    for r in rows:
        result = _json(r["result"])
        sent = _count(result.get("sent")) if r["action"] == "moderate" else _count(result.get("moderated"))
        if sent > 0:
            out.append({**r, "payload": _json(r["payload"])})
    return out


def _moderation_events(user_id: int, now: datetime, visible_ws: set, ws_rows: dict) -> list[dict]:
    sends = _moderation_sends(user_id, now - timedelta(days=MODERATION_LOOKBACK_DAYS))
    latest: dict[int, tuple[dict, set[int]]] = {}
    for s in sends:
        payload = s["payload"] or {}
        if s["action"] == "moderate":
            cids = [payload.get("campaign_row_id")]
        else:
            cids = list(payload.get("campaign_row_ids") or [])
        ad_ids = {int(t[3:]) for t in s["targets"] or [] if t.startswith("ad:") and t[3:].isdigit()}
        for cid in cids:
            if isinstance(cid, int) and cid not in latest:
                latest[cid] = (s, ad_ids)  # по кампании — только последняя отправка
    if not latest:
        return []
    with pool.connection() as conn:
        campaigns = conn.execute(
            """
            SELECT c.id, c.account_id, c.name, c.state, c.status, c.status_clarification, c.synced_at,
                   wc.workspace_id
            FROM direct_campaigns c LEFT JOIN workspace_campaigns wc ON wc.campaign_row_id = c.id
            WHERE c.user_id = %s AND c.id = ANY(%s)
            """,
            (user_id, list(latest)),
        ).fetchall()
        ads = conn.execute(
            """
            SELECT ad.id, g.campaign_row_id, ad.status, ad.state, ad.synced_at, ad.title,
                   ad.status_clarification AS reason, ad.moderation AS extras
            FROM direct_ads ad JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
            WHERE ad.user_id = %s AND g.campaign_row_id = ANY(%s)
            ORDER BY ad.id
            """,
            (user_id, list(latest)),
        ).fetchall()
    by_campaign: dict[int, list[dict]] = {}
    for a in ads:
        by_campaign.setdefault(a["campaign_row_id"], []).append(a)
    from app.overview import rejected_detail

    events = []
    for c in campaigns:
        ws_id = c["workspace_id"]
        if ws_id not in visible_ws:
            continue
        send, ad_ids = latest[c["id"]]
        own = by_campaign.get(c["id"], [])
        relevant = [a for a in own if a["id"] in ad_ids] or own if ad_ids else own
        outcome = moderation_outcome(c, relevant, _aware(send["created_at"]))
        if outcome is None:
            continue
        row = ws_rows.get(ws_id) or {}
        # Тип и серьёзность — как у той же проблемы в сводке (скрытие проверяется по ним): про отклонённые
        # объявления — «Отклонённые объявления», про саму кампанию — «Модерация кампании».
        about_ads = bool(outcome["result"] == "rejected" and outcome["count"])
        p = {
            "kind": "ads_rejected" if about_ads else "moderation",
            "severity": "warning" if about_ads else "critical",
            "campaign_row_id": c["id"],
            "account_id": c["account_id"],
            "workspace_id": ws_id,
            "workspace_ids": [ws_id],
            "workspace_name": row.get("name") or "",
            "client_name": row.get("client_name") or "",
            "result": outcome["result"],
        }
        name = c["name"] or "кампания"
        link = _campaign_link(ws_id, c["id"])
        obj = f"mod:c{c['id']}:{send['id']}"
        if outcome["result"] == "accepted":
            tail = "объявления уже показываются" if outcome["running"] else "можно включать"
            p["event"] = _event(obj, p, "модерация пройдена", f"Кампания «{name}» прошла модерацию — {tail}.", link)
        elif outcome["count"]:
            n = outcome["count"]
            what = f"{n} {_plural(n, 'объявление', 'объявления', 'объявлений')}"
            detail = _short(rejected_detail(outcome["rows"], 2), 220)
            p["event"] = _event(
                obj,
                p,
                f"отклонено {what}",
                f"Модерация отклонила {what} в кампании «{name}»: {detail} — исправьте с ИИ на странице кампании.",
                link,
            )
        else:
            reason = _short(outcome["reason"]) if outcome["reason"] else ""
            p["event"] = _event(
                obj,
                p,
                "кампания отклонена",
                f"Модерация отклонила кампанию «{name}»{': ' + reason if reason else ''}"
                " — исправьте с ИИ на странице кампании.",
                link,
            )
        events.append(p)
    return events


def detect(user_id: int, now: datetime | None = None) -> list[dict]:
    """Срочные события, которые есть прямо сейчас (без учёта того, о чём уже сообщали).

    → [{object, where, headline, sentence, link, tg_link, workspace_id}]
    """
    from app.overview import build_overview

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(MSK).date()
    yesterday = today - timedelta(days=1)
    overview = build_overview(user_id, today=today, now=now)  # проблемы уже без скрытых
    ws_rows = {r["id"]: r for r in overview["workspaces"]}
    visible_ws = set(ws_rows)
    ws_client = {r["id"]: r["client_id"] for r in overview["workspaces"] if r["id"] is not None}
    active_snoozes = snoozes.load(user_id)
    money_accounts = {b["account_id"] for b in overview["balances"] if money_critical(b)}
    visible_accounts = {b["account_id"] for b in overview["balances"]}

    rejected_campaigns: set[int] = set()
    # Итоги модерации — первыми: run() отправляет по порядку, и «кампания отклонена» той же кампании
    # в этот же час уже видит отправленный итог (_already_sent молчит о ней сутки, дальше — раз в сутки).
    events: list[dict] = [
        p["event"]
        for p in _moderation_events(user_id, now, visible_ws, ws_rows)
        if p["result"] == "accepted" or not snoozes.is_hidden(p, active_snoozes, ws_client, now)
    ]

    for p in overview["problems"]:
        kind, cid = p["kind"], p.get("campaign_row_id")
        if kind == "balance" and p["severity"] == "critical":
            balance = (p.get("metric") or {}).get("balance")
            days = p.get("days_on_balance")
            account = p.get("object_name") or "логина"
            ws_ids = [w for w in p.get("workspace_ids") or [] if w is not None]
            link = f"/w/{ws_ids[0]}/summary" if len(ws_ids) == 1 else "/cabinet"
            if balance is not None and balance <= 0:
                headline = "деньги закончились"
                sentence = f"На счёте «{account}» закончились деньги — реклама не показывается, пополните счёт в Директе."
            else:
                from app.overview import rub

                headline = "деньги на исходе"
                money = f"{rub(balance)} — " if balance is not None else ""
                runway = f"примерно на {_num(days)} дн." if days else "меньше чем на день"
                sentence = f"На счёте «{account}» {money}этого хватит {runway}, пополните заранее."
            events.append(
                _event(f"money:a{p['account_id']}", p, headline, sentence, link, tg_link=p.get("direct_url") or "")
            )
        elif kind == "moderation" and p["severity"] == "critical" and cid:
            rejected_campaigns.add(cid)
            reason = _short(p.get("detail") or "")
            name = p.get("object_name") or "кампания"
            events.append(
                _event(
                    f"rejected:c{cid}",
                    p,
                    "кампания отклонена",
                    f"Модерация отклонила кампанию «{name}»{': ' + reason if reason else ''}"
                    " — исправьте с ИИ на странице кампании.",
                    _campaign_link(p.get("workspace_id"), cid),
                )
            )
    for p in overview["problems"]:
        cid = p.get("campaign_row_id")
        if p["kind"] != "stopped" or not cid or cid in rejected_campaigns or p.get("account_id") in money_accounts:
            continue
        name = p.get("object_name") or "кампания"
        events.append(
            _event(
                f"stopped:c{cid}",
                p,
                "кампания встала",
                f"Кампания «{name}» перестала показываться, хотя её никто не останавливал, — посмотрите, что с ней.",
                _campaign_link(p.get("workspace_id"), cid),
            )
        )

    events.extend(_stats_events(user_id, now, yesterday, ws_rows, ws_client, visible_accounts, money_accounts, active_snoozes))
    return events


def _stats_events(user_id, now, yesterday, ws_rows, ws_client, visible_accounts, money_accounts, active_snoozes):
    """Расход без заявок и подорожавшие заявки — по своей истории кампании за 33 дня."""
    rows = _load_history(user_id, yesterday - timedelta(days=BASE_DAYS + RECENT_DAYS), yesterday)
    if not rows:
        return []
    day_start = datetime.combine(yesterday + timedelta(days=1), datetime.min.time(), MSK)
    history: dict[tuple[int, int], dict[date, tuple[int, int]]] = {}
    closed: set[tuple[int, int]] = set()
    for r in rows:
        key = (r["account_id"], r["campaign_id"])
        history.setdefault(key, {})[r["day"]] = (int(r["cost"] or 0), int(r["conv"] or 0))
        if r["day"] == yesterday and _aware(r["updated_at"]) and _aware(r["updated_at"]) >= day_start:
            closed.add(key)
    events = []
    for c in _running_campaigns(user_id):
        key = (c["account_id"], c["campaign_id"])
        ws_id = c["workspace_id"]
        if key not in history or ws_id not in ws_rows or c["account_id"] not in visible_accounts:
            continue
        if c["account_id"] in money_accounts or c["status"] == "REJECTED":
            continue  # причина уже названа
        row = ws_rows[ws_id]
        p = {
            "severity": "warning",  # как «Расход без заявок» и «Цена заявки выросла» в сводке
            "campaign_row_id": c["id"],
            "account_id": c["account_id"],
            "workspace_id": ws_id,
            "workspace_ids": [ws_id],
            "workspace_name": row.get("name") or "",
            "client_name": row.get("client_name") or "",
        }
        name = c["name"] or str(c["campaign_id"])
        link = _campaign_link(ws_id, c["id"])
        kpi = row.get("kpi") or {}
        target = kpi["target"] * 100 if kpi.get("type") == "cpa" and kpi.get("target") else None
        from app.overview import rub

        found = no_conv_event(history[key], yesterday, target) if key in closed else None
        if found and not snoozes.is_hidden({**p, "kind": "no_conversions"}, active_snoozes, ws_client, now):
            events.append(
                _event(
                    f"noconv:c{c['id']}",
                    p,
                    "расход без заявок",
                    f"Вчера кампания «{name}» потратила {rub(found['cost'])} и не принесла ни одной заявки"
                    f" (обычно заявка обходится ≈{rub(found['cpa'])}).",
                    link,
                )
            )
            continue
        spike = cpa_spike_event(history[key], yesterday)
        if spike and not snoozes.is_hidden({**p, "kind": "cpa_spike"}, active_snoozes, ws_client, now):
            events.append(
                _event(
                    f"cpa:c{c['id']}",
                    p,
                    "заявки подорожали",
                    f"За 3 дня заявка в кампании «{name}» стоила {rub(spike['cpa'])} — в {_num(spike['ratio'])} раза"
                    f" дороже обычных {rub(spike['base_cpa'])}.",
                    link,
                )
            )
    return events


# --- Отправка -----------------------------------------------------------------------------------------


def _recent_keys(user_id: int, since: datetime) -> list[dict]:
    with pool.connection() as conn:
        return list(
            conn.execute(
                """
                SELECT dedup_key, created_at FROM notifications
                WHERE user_id = %s AND kind = 'urgent' AND created_at > %s
                """,
                (user_id, since),
            ).fetchall()
        )


def _already_sent(event: dict, recent: list[dict], now: datetime) -> bool:
    """Итог модерации — один раз на отправку; остальное — не чаще раза в 24 часа на объект.
    «Кампания отклонена» молчит 24 часа после итога модерации той же кампании, потом напоминает раз в сутки."""
    obj = event["object"]
    day_ago = now - timedelta(hours=REPEAT_HOURS)
    for r in recent:
        sent_obj = object_of(r["dedup_key"])
        if obj.startswith("mod:") and sent_obj == obj:
            return True
        if sent_obj == obj and _aware(r["created_at"]) > day_ago:
            return True
        # Об отклонённой кампании уже сообщил итог модерации за последние сутки.
        if obj.startswith("rejected:c") and sent_obj.startswith(f"mod:{obj[len('rejected:'):]}:"):
            if _aware(r["created_at"]) > day_ago:
                return True
    return False


def _telegram_text(event: dict) -> str:
    from app.settings_store import public_base_url

    url = event["tg_link"] or (f"{(public_base_url() or '').rstrip('/')}{event['link']}" if event["link"] else "")
    return "\n".join(x for x in (f"Срочно · {event['where']}", event["sentence"], url) if x)


def run(user_id: int, now: datetime | None = None) -> dict:
    """Ежечасная проверка: отложенный на утро Telegram и новые срочные события."""
    from app.notifications import flush_telegram, notify, prefs_from

    now = now or datetime.now(timezone.utc)
    events = detect(user_id, now)
    objects = {e["object"] for e in events}
    is_quiet = quiet(now)
    flushed = 0 if is_quiet else flush_telegram(user_id, lambda key: object_of(key) in objects, now)
    with pool.connection() as conn:
        row = conn.execute("SELECT notify_prefs FROM users WHERE id = %s", (user_id,)).fetchone()
    prefs = prefs_from((row or {}).get("notify_prefs"))["urgent"]
    if not (prefs["cabinet"] or prefs["telegram"]):
        return {"events": len(events), "sent": 0, "flushed": flushed}
    recent = _recent_keys(user_id, now - timedelta(days=MODERATION_LOOKBACK_DAYS + 1))
    day = now.astimezone(MSK).date().isoformat()
    sent = 0
    for e in events:
        if _already_sent(e, recent, now):
            continue
        notify(
            user_id,
            "urgent",
            f"{e['where']}: {e['headline']}",
            body=e["sentence"],
            link=e["link"],
            workspace_id=e["workspace_id"],
            severity="error",
            dedup_key=f"urgent:{e['object']}:{day}",
            telegram_text=_telegram_text(e),
            telegram_later=is_quiet,
        )
        recent.append({"dedup_key": f"urgent:{e['object']}:{day}", "created_at": now})
        sent += 1
    return {"events": len(events), "sent": sent, "flushed": flushed}


def covered(user_id: int) -> dict:
    """Счета и кампании, о которых уже говорит срочное, — сигналы о затухании их не повторяют."""
    from app.notifications import prefs_from
    from app.overview import build_overview

    with pool.connection() as conn:
        row = conn.execute("SELECT notify_prefs FROM users WHERE id = %s", (user_id,)).fetchone()
    prefs = prefs_from((row or {}).get("notify_prefs"))["urgent"]
    if not (prefs["cabinet"] or prefs["telegram"]):
        return {"accounts": set(), "campaigns": set()}
    overview = build_overview(user_id)
    accounts = {b["account_id"] for b in overview["balances"] if money_critical(b)}
    campaigns = {
        p["campaign_row_id"]
        for p in overview["problems"]
        if p.get("campaign_row_id")
        and ((p["kind"] == "moderation" and p["severity"] == "critical") or p["kind"] == "stopped")
    }
    if accounts:
        with pool.connection() as conn:
            campaigns |= {
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM direct_campaigns WHERE user_id = %s AND account_id = ANY(%s)",
                    (user_id, sorted(accounts)),
                ).fetchall()
            }
    return {"accounts": accounts, "campaigns": campaigns}
