"""Аудитории групп: ретаргетинг по целям Метрики, готовые условия кабинета (в том числе LAL),
краткосрочные интересы — «поведение и привычки» — и новые LAL из Яндекс Аудиторий.

Аудитория группы — это условие подбора в кабинете (RetargetingList) и нацеливание группы на него
(AudienceTarget). Вид аудитории в спецификации кампании:
  {"type": "retargeting", "name", "rules": [{"operator": ANY|ALL|NONE, "goal_ids": [...], "days": 30}]}
  {"type": "list", "name", "list_id"} — готовое условие кабинета, например LAL из Яндекс Аудиторий;
  {"type": "interests", "name", "interest_ids": [...]} — ключи краткосрочных интересов (InterestKey);
  {"type": "lookalike", "name", "source": {"kind": goal|segment|counter, "id"}, "similarity": 1..5} —
    новый LAL в Яндекс Аудиториях: похожие на выполнивших цель, на свой сегмент Аудиторий (например,
    список из CRM) или на посетителей сайта.

На поиске условие сужает показы по фразам группы до аудитории, в сетях — расширяет: группа
показывается и по фразам, и по аудитории. Интересы работают только в сетях. Одинаковое условие
кабинета используется повторно, новое создаётся с именем аудитории.

LAL создаётся вместе с кампанией: сегмент-источник Метрики (если такого ещё нет), сам LAL, доступ
логину клиента (агентский кабинет) и условие кабинета с сегментом. Директ видит сегмент Аудиторий,
когда тот «Готов» (LAL обрабатывается от двух часов), — до этого нацеливание не пройдёт: маркетолог
получит предупреждение, а сегмент останется в Аудиториях.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from app import direct_api
from app.yandex import YandexError

log = logging.getLogger("app.audiences")

TYPES = ("retargeting", "list", "interests", "lookalike")
TYPE_LABELS = {
    "retargeting": "ретаргетинг",
    "list": "условие кабинета",
    "interests": "интересы и привычки",
    "lookalike": "новый LAL",
}
OPERATORS = ("ANY", "ALL", "NONE")
MAX_RULES = 5
MAX_GOALS = 20
MAX_INTERESTS = 10
MAX_PER_GROUP = 10
DAYS_DEFAULT = 30
DAYS_MAX = 540
NAME_MAX = 250
SEARCH_LIMIT = 30
# Id краткосрочного интереса в Директе — «10» перед InterestKey из 10 цифр (20 — долгосрочный, 30 — любой).
SHORT_PREFIX = "10"
KEY_DIGITS = 10
# Сегменты похожих пользователей (LAL) в правилах условий — id из этого диапазона.
LAL_IDS = range(1_500_000_000, 1_900_000_000)
LAL_NOTE = "Директ временно не даёт добавлять LAL в группы (режим ограниченной функциональности) — может отказать"
# Сегмент Яндекс Аудиторий в правилах условия кабинета — его id плюс 2 млрд.
AUDIENCE_OFFSET = 2_000_000_000
AUDIENCE_IDS = range(AUDIENCE_OFFSET, 2 * AUDIENCE_OFFSET)
AUDIENCE_API = "https://api-audience.yandex.ru/v1/management/"
# Права второго OAuth-приложения (только Аудитории): чтение сегментов и их создание.
AUDIENCE_SCOPES = ("audience:read", "audience:write")
# Второе приложение не задано на сервере — новых LAL нет, готовые условия работают.
DISABLED_TEXT = (
    "Новые LAL на сервисе не подключены (нет приложения Яндекс Аудиторий) — "
    "подойдёт только готовое условие кабинета с LAL из get_audiences"
)
# Источник LAL: цель Метрики, свой сегмент Аудиторий (CRM, пиксель…) или весь счётчик.
SOURCE_KINDS = ("goal", "segment", "counter")
_METRIKA_TYPES = {"goal": "goal_id", "counter": "counter_id"}
SIMILARITY = range(1, 6)
SIMILARITY_DEFAULT = 3
SEGMENT_FAILED = ("processing_failed",)
SEGMENT_STATUS = {
    "uploaded": "загружен, ждёт обработки",
    "is_processed": "обрабатывается",
    "processed": "готов",
    "processing_failed": "ошибка обработки",
    "is_updated": "обновляется",
    "few_data": "мало данных",
}
NEW_LAL_NOTE = (
    "Сегмент создастся в Яндекс Аудиториях вместе с кампанией; Директ увидит его, когда он станет «Готов» "
    "(от двух часов, нужно от 100 похожих людей)"
)
CACHE_TTL = 24 * 3600
RETRY_AFTER = 300

_lock = threading.Lock()
_cache: dict = {"interests": {}, "next": 0.0}


def _norm(text) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def _int(value) -> int | None:
    text = str(value if value is not None else "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _ints(raw, what: str, limit: int) -> list[int]:
    if raw in (None, ""):
        return []
    items = raw if isinstance(raw, list) else [raw]
    out: list[int] = []
    for item in items:
        value = _int(item)
        if value is None:
            raise YandexError("args", f"{what}: «{item}» — не id")
        if value not in out:
            out.append(value)
    if len(out) > limit:
        raise YandexError("args", f"{what}: не больше {limit}")
    return out


# --- Интересы ------------------------------------------------------------------------------------


def _parse_interests(result: dict) -> dict[int, dict]:
    """Краткосрочные интересы {InterestKey: {id, name, path, description, parent}} — текстовым кампаниям доступны только они."""
    short = {}
    for item in result.get("AudienceInterests") or []:
        if item.get("InterestType") == "SHORT_TERM" and _int(item.get("Id")) and _int(item.get("InterestKey")):
            short[int(item["Id"])] = item
    out: dict[int, dict] = {}
    for item in short.values():
        names: list[str] = []
        parent = _int(item.get("ParentId"))
        seen: set[int] = set()
        while parent in short and parent not in seen and len(names) < 4:
            seen.add(parent)
            names.append(str(short[parent].get("Name") or ""))
            parent = _int(short[parent].get("ParentId"))
        top = _int(item.get("ParentId"))
        key = int(item["InterestKey"])
        out[key] = {
            "id": key,
            "name": str(item.get("Name") or key),
            "path": " › ".join(reversed(names)),
            "description": str(item.get("Description") or "").strip(),
            "parent": int(short[top]["InterestKey"]) if top in short else None,
        }
    return out


def interest_catalog(token) -> dict[int, dict]:
    """Справочник интересов на сутки в памяти процесса; пустой — Директ недоступен."""
    with _lock:
        if time.monotonic() < _cache["next"]:
            return _cache["interests"]
        try:
            fresh = _parse_interests(direct_api.call(token, None, "dictionaries", "get", {"DictionaryNames": ["AudienceInterests"]}))
        except Exception:
            log.warning("Справочник интересов Директа недоступен", exc_info=True)
            fresh = {}
        if fresh:
            _cache.update(interests=fresh, next=time.monotonic() + CACHE_TTL)
        else:
            _cache["next"] = time.monotonic() + RETRY_AFTER
        return _cache["interests"]


def find_interests(catalog: dict[int, dict], query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    """Интересы, где есть все слова запроса: сначала совпадения в названии, потом в разделе и описании."""
    words = _norm(query).split()
    if not words:
        return []
    hits: list[tuple[int, str, dict]] = []
    for item in catalog.values():
        name = _norm(item["name"])
        text = f"{name} {_norm(item['path'])} {_norm(item['description'])}"
        if all(w in text for w in words):
            rank = 0 if all(w in name for w in words) else 1
            hits.append((rank, name, item))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits[:limit]]


def interest_roots(catalog: dict[int, dict]) -> list[dict]:
    """Верхние разделы справочника с числом интересов внутри."""
    children: dict[int, int] = {}
    for item in catalog.values():
        if item["parent"]:
            children[item["parent"]] = children.get(item["parent"], 0) + 1
    roots = [
        {"id": item["id"], "name": item["name"], "children": children.get(item["id"], 0)}
        for item in catalog.values()
        if not item["parent"]
    ]
    return sorted(roots, key=lambda r: r["name"])


def interest_key(value) -> int | None:
    """InterestKey из ключа или полного Id справочника (10…/20…/30… + ключ)."""
    key = _int(value)
    if key is None:
        return None
    text = str(key)
    if len(text) == 2 + KEY_DIGITS and text[:2] in ("10", "20", "30"):
        return int(text[2:])
    return key


# --- Условия кабинета ----------------------------------------------------------------------------


def account_lists(token, client_login: str | None) -> list[dict]:
    """Условия подбора аудитории в кабинете: [{id, name, type, scope, available, lal, rules}]."""
    raw = direct_api.get_all(
        token,
        client_login,
        "retargetinglists",
        {"SelectionCriteria": {}, "FieldNames": ["Id", "Type", "Name", "Scope", "IsAvailable", "Rules"]},
        "RetargetingLists",
    )
    out = []
    for item in raw:
        rules = []
        for rule in item.get("Rules") or []:
            args = rule.get("Arguments") or []
            rules.append(
                {
                    "operator": str(rule.get("Operator") or "ANY"),
                    "ids": [int(a["ExternalId"]) for a in args if _int(a.get("ExternalId"))],
                    "days": max([int(a.get("MembershipLifeSpan") or 0) for a in args] or [0]) or None,
                }
            )
        out.append(
            {
                "id": int(item["Id"]),
                "name": str(item.get("Name") or item["Id"]),
                "type": str(item.get("Type") or ""),
                "scope": str(item.get("Scope") or ""),
                "available": str(item.get("IsAvailable") or "YES") != "NO",
                "lal": any(i in LAL_IDS for r in rules for i in r["ids"]),
                "rules": rules,
            }
        )
    return out


def can_target(item: dict) -> bool:
    """Условие годится для нацеливания группы: доступно и не только для корректировок ставок."""
    return item["available"] and item["scope"] != "FOR_ADJUSTMENTS_ONLY"


def account_access(user_id: int, account_id: int) -> tuple[str, str | None]:
    """(токен, логин клиента) кабинета пользователя — для справочников и условий кабинета."""
    from app.actions import _account_creds
    from app.yandex import get_connection_access_token

    account = _account_creds(user_id, account_id)
    return get_connection_access_token(account["connection_id"], user_id), account["client_login"]


# --- Яндекс Аудитории ----------------------------------------------------------------------------


def enabled() -> bool:
    """Второе OAuth-приложение (Яндекс Аудитории) задано — новые LAL возможны."""
    from app.yandex import audience_configured

    return audience_configured()


def has_rights(scopes) -> bool:
    """Токен Аудиторий выдан с правами чтения и создания сегментов — без них LAL не создать."""
    have = set(str(scopes or "").split())
    return all(scope in have for scope in AUDIENCE_SCOPES)


def rights_text(login: str | None = None) -> str:
    who = f"логину @{login}" if login else "логину кабинета"
    return (
        f"Яндекс Аудитории не подключены {who} — новый LAL не создать. «Аккаунты» → «Подключить Аудитории» "
        "у этого логина, войдите тем же логином. Готовые условия кабинета работают и так"
    )


def account_rights(user_id: int, account_id: int) -> dict:
    """
    {"login", "connection_id", "enabled", "rights"} — логин подключения кабинета; enabled — приложение Аудиторий
    задано на сервере; rights — этому логину Аудитории подключены (свой токен с правами).
    """
    from app.db import pool

    on = enabled()
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.yandex_login, c.sandbox, c.audience_token_enc <> '' AS audience, c.audience_scopes
            FROM direct_accounts a
            JOIN yandex_connections c ON c.id = a.connection_id
            WHERE a.id = %s AND a.user_id = %s
            """,
            (account_id, user_id),
        ).fetchone()
    if row is None:
        return {"login": "", "connection_id": None, "enabled": on, "rights": False}
    rights = on and bool(row["audience"]) and not row["sandbox"] and has_rights(row["audience_scopes"])
    return {
        "login": str(row["yandex_login"] or ""),
        "connection_id": int(row["id"]),
        "enabled": on,
        "rights": rights,
    }


def audience_token(rights: dict) -> str:
    """Токен Аудиторий логина кабинета (второе приложение); нет — YandexError с понятным текстом."""
    from app.yandex import get_audience_access_token

    if not rights.get("enabled"):
        raise YandexError("audience-config", DISABLED_TEXT)
    if not rights.get("rights") or rights.get("connection_id") is None:
        raise YandexError("audience-scope", rights_text(rights.get("login")))
    try:
        return get_audience_access_token(int(rights["connection_id"]))
    except YandexError as exc:
        if exc.code == "audience-config":
            raise YandexError("audience-config", DISABLED_TEXT) from exc
        if exc.code == "audience-scope":
            raise YandexError("audience-scope", rights_text(rights.get("login"))) from exc
        raise


def audience_call(token, method: str, path: str, *, body: dict | None = None, params: dict | None = None) -> dict:
    """Запрос к API управления Аудиториями; ошибка — YandexError, нет прав — код audience-scope."""
    if direct_api.is_sandbox(token):
        raise YandexError("sandbox", "У песочницы Директа нет Яндекс Аудиторий")
    try:
        response = direct_api._client().request(
            method,
            AUDIENCE_API + path,
            headers={"Authorization": f"OAuth {token}", "Accept-Language": "ru"},
            json=body,
            params=params,
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        raise YandexError("audience", "Яндекс Аудитории не ответили — попробуйте позже") from exc
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if response.status_code < 400:
        return payload
    errors = [e for e in payload.get("errors") or [] if isinstance(e, dict)]
    detail = "; ".join(str(e.get("message") or "").strip() for e in errors if e.get("message"))
    detail = detail or str(payload.get("message") or "").strip() or f"HTTP {response.status_code}"
    log.warning("Аудитории %s %s: %s %s", method, path, response.status_code, detail)
    # Список своих сегментов закрыт только без прав; 403 на чужой сегмент — обычная ошибка.
    if response.status_code == 401 or (response.status_code == 403 and path == "segments"):
        raise YandexError("audience-scope", rights_text())
    if response.status_code == 429 or any(e.get("error_type") == "quota" for e in errors):
        raise YandexError("audience", f"Лимит запросов Яндекс Аудиторий исчерпан — повторите позже ({detail})")
    raise YandexError("audience", detail)


def list_segments(token) -> list[dict]:
    """Свои и доверенные сегменты Аудиторий: [{id, name, type, status, owner, …}]."""
    return [s for s in audience_call(token, "GET", "segments").get("segments") or [] if _int(s.get("id"))]


def segment_text(seg: dict) -> str:
    """Сегмент Аудиторий словами: что внутри и статус."""
    kind = seg.get("type")
    if kind == "lookalike":
        what = f"LAL от сегмента №{seg.get('lookalike_link')}, похожесть {seg.get('lookalike_value')} из 5"
    elif kind == "metrika":
        mtype = {"goal_id": "цель", "counter_id": "счётчик", "segment_id": "сегмент"}.get(seg.get("metrika_segment_type"))
        what = f"Метрика: {mtype or 'данные'} {seg.get('metrika_segment_id')}"
    else:
        names = {"uploading": "загруженный список (CRM)", "pixel": "пиксель", "appmetrica": "AppMetrica", "geo": "гео"}
        what = names.get(kind, str(kind or "сегмент"))
    status = SEGMENT_STATUS.get(seg.get("status"), str(seg.get("status") or ""))
    return f"{what}; {status}" if status else what


def _login_key(login) -> str:
    # В логинах Яндекса точка и дефис равнозначны.
    return str(login or "").strip().lower().replace(".", "-")


# --- Аудитория спецификации ----------------------------------------------------------------------


def clean_audience(raw, *, name: str | None = None) -> dict:
    """Проверенная аудитория группы; ошибка — YandexError с понятной причиной."""
    if not isinstance(raw, dict):
        raise YandexError("args", "Аудитория — объект {type, name, …}")
    title = " ".join(str(name if name is not None else raw.get("name") or "").split())[:NAME_MAX]
    if not title:
        raise YandexError("args", "У аудитории нет названия")
    kind = str(raw.get("type") or "").strip().lower()
    if not kind:
        kind = (
            "list" if raw.get("list_id")
            else "interests" if raw.get("interest_ids")
            else "lookalike" if raw.get("source") or raw.get("similarity")
            else "retargeting"
        )
    if kind not in TYPES:
        raise YandexError("args", f"Аудитория «{title}»: тип {kind} — нужен retargeting, list, interests или lookalike")
    if kind == "lookalike":
        similarity = raw.get("similarity")
        similarity = SIMILARITY_DEFAULT if similarity in (None, "") else _int(similarity)
        if similarity not in SIMILARITY:
            raise YandexError("args", f"Аудитория «{title}»: похожесть LAL — от 1 до 5")
        return {"type": "lookalike", "name": title, "source": _lookalike_source(raw, title), "similarity": similarity}
    if kind == "list":
        list_id = _int(raw.get("list_id"))
        if list_id is None:
            raise YandexError("args", f"Аудитория «{title}»: нужен list_id условия кабинета")
        return {"type": "list", "name": title, "list_id": list_id}
    if kind == "interests":
        values = raw.get("interest_ids")
        values = [] if values in (None, "") else values if isinstance(values, list) else [values]
        keys = []
        for value in values:
            key = interest_key(value)
            if key is None:
                raise YandexError("args", f"Аудитория «{title}»: «{value}» — не id интереса")
            if key not in keys:
                keys.append(key)
        if not keys:
            raise YandexError("args", f"Аудитория «{title}»: нужен хотя бы один интерес")
        if len(keys) > MAX_INTERESTS:
            raise YandexError("args", f"Аудитория «{title}»: не больше {MAX_INTERESTS} интересов")
        return {"type": "interests", "name": title, "interest_ids": keys}
    rules_raw = raw.get("rules")
    if rules_raw in (None, "", []):
        # Короткая запись: goal_ids — выполнили любую, exclude_goal_ids — не выполнили.
        rules_raw = [{"operator": "ANY", "goal_ids": raw.get("goal_ids"), "days": raw.get("days")}]
        if raw.get("exclude_goal_ids"):
            rules_raw.append({"operator": "NONE", "goal_ids": raw.get("exclude_goal_ids"), "days": raw.get("days")})
    if not isinstance(rules_raw, list) or not all(isinstance(r, dict) for r in rules_raw):
        raise YandexError("args", f"Аудитория «{title}»: rules — список {{operator, goal_ids, days}}")
    if len(rules_raw) > MAX_RULES:
        raise YandexError("args", f"Аудитория «{title}»: не больше {MAX_RULES} правил")
    rules = []
    for rule in rules_raw:
        operator = str(rule.get("operator") or "ANY").strip().upper()
        if operator not in OPERATORS:
            raise YandexError("args", f"Аудитория «{title}»: operator {operator} — нужен ANY, ALL или NONE")
        goals = _ints(rule.get("goal_ids"), f"Аудитория «{title}»", MAX_GOALS)
        if not goals:
            raise YandexError("args", f"Аудитория «{title}»: в правиле нет целей")
        days = rule.get("days")
        days = DAYS_DEFAULT if days in (None, "") else _int(days)
        if days is None or days > DAYS_MAX:
            raise YandexError("args", f"Аудитория «{title}»: срок — от 1 до {DAYS_MAX} дней")
        rules.append({"operator": operator, "goal_ids": goals, "days": days})
    if all(r["operator"] == "NONE" for r in rules):
        # Такое условие Директ разрешает только для корректировок ставок.
        raise YandexError("args", f"Аудитория «{title}»: нужно правило, кто выполнил цель, — одних «не выполнили» мало")
    return {"type": "retargeting", "name": title, "rules": rules}


def _lookalike_source(raw: dict, title: str) -> dict:
    """Источник LAL {"kind", "id"} из source {kind, id}, source {goal_id…} или goal_id/segment_id/counter_id."""
    source = raw.get("source")
    if isinstance(source, dict) and source.get("kind"):
        kind, value = str(source.get("kind") or "").strip().lower(), source.get("id")
    else:
        where = source if isinstance(source, dict) else raw
        found = [(k, where.get(f"{k}_id")) for k in SOURCE_KINDS if where.get(f"{k}_id") not in (None, "")]
        if len(found) != 1:
            raise YandexError(
                "args",
                f"Аудитория «{title}»: источник LAL — одно из goal_id (цель Метрики), segment_id (сегмент Аудиторий) "
                "или counter_id (счётчик)",
            )
        kind, value = found[0]
    if kind not in SOURCE_KINDS:
        raise YandexError("args", f"Аудитория «{title}»: источник LAL {kind} — нужен goal, segment или counter")
    ident = _int(value)
    if ident is None:
        raise YandexError("args", f"Аудитория «{title}»: «{value}» — не id источника LAL")
    return {"kind": kind, "id": ident}


def audience_signature(aud: dict) -> tuple:
    """Суть аудитории без названия: одинаковые аудитории — одно условие кабинета."""
    if aud["type"] == "list":
        return ("list", aud["list_id"])
    if aud["type"] == "lookalike":
        return ("lookalike", aud["source"]["kind"], aud["source"]["id"], aud["similarity"])
    if aud["type"] == "interests":
        return ("interests", tuple(sorted(aud["interest_ids"])))
    rules = sorted((r["operator"], tuple(sorted(r["goal_ids"])), r["days"]) for r in aud["rules"])
    return ("retargeting", tuple(rules))


def goal_ids(aud: dict) -> list[int]:
    if aud.get("type") == "lookalike":
        return [aud["source"]["id"]] if aud["source"]["kind"] == "goal" else []
    out: list[int] = []
    for rule in aud.get("rules") or []:
        out.extend(g for g in rule["goal_ids"] if g not in out)
    return out


_OPERATOR_TEXT = {"ANY": "выполнили любую из", "ALL": "выполнили все", "NONE": "не выполнили"}


def source_text(source: dict, *, goals: dict | None = None, segments: dict | None = None) -> str:
    """Источник LAL словами: «тех, кто выполнил цель «Заявка»»."""
    goals, segments = goals or {}, segments or {}
    if source["kind"] == "goal":
        return f"тех, кто выполнил цель «{goals.get(source['id']) or source['id']}»"
    if source["kind"] == "counter":
        return f"посетителей сайта (счётчик {source['id']})"
    name = segments.get(source["id"])
    return f"сегмент Аудиторий «{name}»" if name else f"сегмент Аудиторий №{source['id']}"


def describe(
    aud: dict,
    *,
    goals: dict | None = None,
    interests: dict | None = None,
    lists: dict | None = None,
    segments: dict | None = None,
) -> str:
    """«Что внутри» человеческими словами; goals/interests/lists/segments — {id: название}."""
    goals, interests, lists = goals or {}, interests or {}, lists or {}
    if aud["type"] == "lookalike":
        return f"похожие на {source_text(aud['source'], goals=goals, segments=segments)}, похожесть {aud['similarity']} из 5"
    if aud["type"] == "list":
        name = lists.get(aud["list_id"])
        return f"условие кабинета «{name}»" if name else f"условие кабинета №{aud['list_id']}"
    if aud["type"] == "interests":
        return "интересы: " + ", ".join(interests.get(k) or str(k) for k in aud["interest_ids"])
    parts = []
    for rule in aud["rules"]:
        names = ", ".join(f"«{goals.get(g) or g}»" for g in rule["goal_ids"])
        verb = _OPERATOR_TEXT[rule["operator"]]
        if rule["operator"] == "ANY" and len(rule["goal_ids"]) == 1:
            verb = "выполнили цель"
        parts.append(f"{verb} {names} за {rule['days']} дн.")
    return "; ".join(parts)


def summary(
    aud: dict,
    *,
    goals: dict | None = None,
    interests: dict | None = None,
    lists: dict | None = None,
    segments: dict | None = None,
) -> str:
    """Вид и суть аудитории одной строкой; lists — {id: условие кабинета из account_lists}."""
    lists = lists or {}
    text = describe(aud, goals=goals, interests=interests, lists={k: v["name"] for k, v in lists.items()}, segments=segments)
    if aud["type"] == "retargeting":
        return f"ретаргетинг: {text}"
    if aud["type"] == "lookalike":
        return f"новый LAL — {text}. {NEW_LAL_NOTE}. {LAL_NOTE}"
    item = lists.get(aud.get("list_id"))
    if item and item["lal"]:
        return f"LAL — {text}. {LAL_NOTE}"
    return text


_LIST_OPERATORS = {"ANY": "любое из", "ALL": "все из", "NONE": "кроме"}


def list_text(item: dict, *, goals: dict | None = None, interests: dict | None = None, limit: int = 6) -> str:
    """Правила условия кабинета словами: цели проекта и интересы — названиями, сегменты — номерами."""
    goals, interests = goals or {}, interests or {}
    parts = []
    for rule in item["rules"]:
        names = []
        for value in rule["ids"][:limit]:
            key = interest_key(value)
            if value in LAL_IDS:
                names.append(f"LAL-сегмент {value}")
            elif value in AUDIENCE_IDS:
                names.append(f"сегмент Аудиторий №{value - AUDIENCE_OFFSET}")
            elif item["type"] == "AUDIENCE" and key in interests:
                names.append(interests[key])
            elif value in goals:
                names.append(f"цель «{goals[value]}»")
            else:
                names.append(f"№{value}")
        more = f" и ещё {len(rule['ids']) - limit}" if len(rule["ids"]) > limit else ""
        days = f" за {rule['days']} дн." if rule["days"] else ""
        parts.append(f"{_LIST_OPERATORS.get(rule['operator'], rule['operator'])} {', '.join(names)}{more}{days}")
    return "; ".join(parts)


def _direct_rules(aud: dict) -> tuple[str, list[dict]]:
    """Тип и правила условия подбора для RetargetingLists.add; у LAL — после создания сегмента (segment_id)."""
    if aud["type"] == "lookalike":
        return "RETARGETING", [{"Operator": "ANY", "Arguments": [{"ExternalId": AUDIENCE_OFFSET + aud["segment_id"]}]}]
    if aud["type"] == "interests":
        args = [{"ExternalId": int(SHORT_PREFIX + str(k).zfill(KEY_DIGITS))} for k in aud["interest_ids"]]
        return "AUDIENCE", [{"Operator": "ANY", "Arguments": args}]
    rules = [
        {"Operator": r["operator"], "Arguments": [{"ExternalId": g, "MembershipLifeSpan": r["days"]} for g in r["goal_ids"]]}
        for r in aud["rules"]
    ]
    return "RETARGETING", rules


def _rules_key(kind: str, rules: list[dict]) -> tuple:
    """Ключ правил для сравнения с условиями кабинета; срок у интересов и сегментов Аудиторий не важен."""
    out = []
    for rule in rules:
        segments_only = bool(rule["ids"]) and all(i in AUDIENCE_IDS for i in rule["ids"])
        days = None if kind == "AUDIENCE" or segments_only else rule.get("days")
        out.append((rule["operator"], tuple(sorted(rule["ids"])), days))
    return (kind, tuple(sorted(out)))


def _aud_key(aud: dict) -> tuple:
    kind, rules = _direct_rules(aud)
    flat = [
        {
            "operator": r["Operator"],
            "ids": [a["ExternalId"] for a in r["Arguments"]],
            "days": r["Arguments"][0].get("MembershipLifeSpan"),
        }
        for r in rules
    ]
    return _rules_key(kind, flat)


# --- Проверка и создание -------------------------------------------------------------------------


def prepare(
    token,
    client_login: str | None,
    groups: list[dict],
    *,
    network: bool,
    allowed_goals: set[int] | None = None,
    rights: dict | None = None,
) -> dict:
    """
    Проверить аудитории групп до создания кампании. Возвращает контекст для attach:
    {"groups": [[аудитории группы i]], "lists": условия кабинета, "made": {ключ: id}, "segments": сегменты Аудиторий}.
    rights — account_rights подключения: новый LAL идёт с токеном Аудиторий (второе приложение), без него — отказ.
    Ошибки — YandexError: кампания не создаётся, пока аудитории не в порядке.
    """
    cleaned: list[list[dict]] = []
    for index, group in enumerate(groups):
        raw = group.get("audiences") or []
        if not isinstance(raw, list):
            raise YandexError("args", "audiences группы — список аудиторий")
        label = str(group.get("name") or "").strip() or f"Группа {index + 1}"
        if len(raw) > MAX_PER_GROUP:
            raise YandexError("args", f"Группа «{label}»: не больше {MAX_PER_GROUP} аудиторий")
        items = [clean_audience(a) for a in raw]
        seen: set = set()
        for aud in items:
            sign = audience_signature(aud)
            if sign in seen:
                raise YandexError("args", f"Группа «{label}»: аудитория «{aud['name']}» повторяется")
            seen.add(sign)
            if aud["type"] == "interests" and not network:
                raise YandexError("args", f"Интересы работают только в сетях — включите сети или уберите «{aud['name']}»")
            if allowed_goals is not None:
                stray = [g for g in goal_ids(aud) if g not in allowed_goals]
                if stray:
                    raise YandexError("args", f"Аудитория «{aud['name']}»: целей {', '.join(map(str, stray))} нет в счётчике проекта")
        if items and not group.get("keywords") and not network:
            raise YandexError("args", f"Группа «{label}» без фраз показывается только в сетях по аудитории — включите сети или добавьте фразы")
        cleaned.append(items)
    ctx: dict = {"groups": cleaned, "lists": [], "made": {}}
    if not any(cleaned):
        return ctx
    lookalikes = [a for items in cleaned for a in items if a["type"] == "lookalike"]
    if lookalikes:
        login = (rights or {}).get("login")
        if rights is not None:
            # Сегменты — токеном второго приложения; запросы Директа ниже — токеном кабинета.
            ctx["audience_token"] = audience_token(rights)
        try:
            ctx["segments"] = list_segments(ctx.get("audience_token", token))
        except YandexError as exc:
            if exc.code == "audience-scope":
                raise YandexError("audience-scope", rights_text(login)) from exc
            raise
        by_segment = {int(s["id"]): s for s in ctx["segments"]}
        for aud in lookalikes:
            if aud["source"]["kind"] != "segment":
                continue
            seg = by_segment.get(aud["source"]["id"])
            if seg is None:
                raise YandexError("args", f"Сегмента Аудиторий №{aud['source']['id']} («{aud['name']}») нет среди ваших сегментов")
            if seg.get("status") in SEGMENT_FAILED:
                raise YandexError("args", f"Сегмент Аудиторий «{seg.get('name')}» не обработан — LAL из него не построить")
    ctx["lists"] = account_lists(token, client_login)
    by_id = {item["id"]: item for item in ctx["lists"]}
    for aud in (a for items in cleaned for a in items):
        if aud["type"] == "list":
            item = by_id.get(aud["list_id"])
            if item is None:
                raise YandexError("args", f"Условия №{aud['list_id']} («{aud['name']}») нет в кабинете")
            if not can_target(item):
                why = "недоступно" if not item["available"] else "годится только для корректировок ставок"
                raise YandexError("args", f"Условие «{item['name']}» {why} — показывать по нему нельзя")
    if any(a["type"] == "interests" for items in cleaned for a in items):
        catalog = interest_catalog(token)
        for aud in (a for items in cleaned for a in items if a["type"] == "interests"):
            unknown = [k for k in aud["interest_ids"] if catalog and k not in catalog]
            if unknown:
                raise YandexError("args", f"Аудитория «{aud['name']}»: интересов {', '.join(map(str, unknown))} нет в справочнике Директа")
    return ctx


def _source_segment(token, aud: dict, ctx: dict) -> dict:
    """Сегмент-источник LAL: указанный, такой же сегмент Метрики в Аудиториях или новый."""
    source = aud["source"]
    segments = ctx.setdefault("segments", [])
    if source["kind"] == "segment":
        return next((s for s in segments if _int(s.get("id")) == source["id"]), {"id": source["id"]})
    mtype = _METRIKA_TYPES[source["kind"]]
    for seg in segments:
        if (
            seg.get("type") == "metrika"
            and seg.get("metrika_segment_type") == mtype
            and _int(seg.get("metrika_segment_id")) == source["id"]
            and seg.get("status") not in SEGMENT_FAILED
        ):
            return seg
    name = f"Источник LAL «{aud['name']}»"[:NAME_MAX]
    body = {"segment": {"name": name, "metrika_segment_type": mtype, "metrika_segment_id": source["id"]}}
    seg = audience_call(token, "POST", "segments/create_metrika", body=body).get("segment") or {}
    if not _int(seg.get("id")):
        raise YandexError("audience", "Яндекс Аудитории не вернули сегмент-источник")
    segments.append(seg)
    ctx.setdefault("segments_created", []).append(int(seg["id"]))
    return seg


def _share(token, client_login: str | None, seg: dict, ctx: dict) -> None:
    """Агентский кабинет: Директ клиента видит сегмент, только если клиенту выдан доступ."""
    owner, client = _login_key(seg.get("owner")), _login_key(client_login)
    if not owner or not client or owner == client:
        return
    shared = ctx.setdefault("shared", set())
    if (int(seg["id"]), client) in shared:
        return
    body = {"grant": {"user_login": client_login, "permission": "view", "comment": "Для кампаний Директа клиента"}}
    try:
        audience_call(token, "PUT", f"segment/{int(seg['id'])}/grant", body=body)
    except YandexError:
        # Доступ уже выдан или сегмент чужой — если Директ его не увидит, attach предупредит.
        log.warning("Аудитории: не выдан доступ к сегменту %s логину %s", seg.get("id"), client_login, exc_info=True)
    shared.add((int(seg["id"]), client))


def _lookalike_segment(token, client_login: str | None, aud: dict, ctx: dict) -> int:
    """Id LAL-сегмента Аудиторий: такой же существующий или новый от источника."""
    key = audience_signature(aud)
    if key in ctx["made"]:
        return ctx["made"][key]
    source = _source_segment(token, aud, ctx)
    source_id = int(source["id"])
    segments = ctx.setdefault("segments", [])
    seg = next(
        (
            s
            for s in segments
            if s.get("type") == "lookalike"
            and _int(s.get("lookalike_link")) == source_id
            and _int(s.get("lookalike_value")) == aud["similarity"]
            and s.get("status") not in SEGMENT_FAILED
        ),
        None,
    )
    if seg is None:
        body = {
            "segment": {
                "name": aud["name"][:NAME_MAX],
                "lookalike_link": source_id,
                "lookalike_value": aud["similarity"],
                "maintain_device_distribution": True,
                "maintain_geo_distribution": True,
            }
        }
        try:
            seg = audience_call(token, "POST", "segments/create_lookalike", body=body).get("segment") or {}
        except YandexError as exc:
            if exc.code != "audience":
                raise
            status = source.get("status")
            wait = (
                ""
                if status in (None, "processed")
                else f". Источник ещё не готов ({SEGMENT_STATUS.get(status, status)}): LAL строится из готового сегмента, "
                "обычно через несколько часов — источник уже в Аудиториях"
            )
            raise YandexError("audience", f"Яндекс Аудитории не создали LAL: {exc.message}{wait}") from exc
        if not _int(seg.get("id")):
            raise YandexError("audience", "Яндекс Аудитории не вернули LAL-сегмент")
        segments.append(seg)
        ctx.setdefault("segments_created", []).append(int(seg["id"]))
    _share(token, client_login, seg, ctx)
    ctx["made"][key] = int(seg["id"])
    return int(seg["id"])


def _list_id(token, client_login: str | None, aud: dict, ctx: dict) -> int:
    """Id условия кабинета для аудитории: готовое, такое же существующее или новое."""
    if aud["type"] == "list":
        return aud["list_id"]
    if aud["type"] == "lookalike":
        aud = {**aud, "segment_id": _lookalike_segment(ctx.get("audience_token", token), client_login, aud, ctx)}
    key = _aud_key(aud)
    if key in ctx["made"]:
        return ctx["made"][key]
    for item in ctx["lists"]:
        if can_target(item) and _rules_key(item["type"], item["rules"]) == key:
            ctx["made"][key] = item["id"]
            return item["id"]
    kind, rules = _direct_rules(aud)
    try:
        result = direct_api.call(
            token,
            client_login,
            "retargetinglists",
            "add",
            {"RetargetingLists": [{"Type": kind, "Name": aud["name"][:NAME_MAX], "Rules": rules}]},
        )
        errors = direct_api.item_errors(result, "AddResults")
        if errors:
            raise YandexError("audience", errors[0][1])
    except YandexError as exc:
        if aud["type"] != "lookalike":
            raise
        # Директ видит сегмент Аудиторий только в статусе «Готов».
        raise YandexError(
            "audience",
            f"LAL-сегмент №{aud['segment_id']} есть в Яндекс Аудиториях, но Директ его пока не принял "
            f"({exc.message or exc.code}). Сегмент обрабатывается от двух часов; когда в Аудиториях он станет «Готов», "
            "добавьте его группе условием подбора аудитории",
        ) from exc
    new_id = int((result.get("AddResults") or [{}])[0]["Id"])
    ctx["made"][key] = new_id
    ctx.setdefault("created", []).append(new_id)
    return new_id


def attach(
    token,
    client_login: str | None,
    adgroup_id: int,
    audiences: list[dict],
    ctx: dict,
    *,
    bid_rub=None,
) -> dict:
    """Нацелить группу на аудитории. Ошибка отдельной аудитории — предупреждение, не отказ."""
    warnings: list[str] = []
    targets: list[dict] = []
    names: list[str] = []
    for aud in audiences:
        try:
            list_id = _list_id(token, client_login, aud, ctx)
        except YandexError as exc:
            warnings.append(f"Аудитория «{aud['name']}» не добавлена: {exc.message or exc.code}")
            continue
        target: dict = {"AdGroupId": int(adgroup_id), "RetargetingListId": list_id}
        if bid_rub not in (None, "", 0):
            target["ContextBid"] = int(round(float(bid_rub) * 1_000_000))
        targets.append(target)
        names.append(aud["name"])
    attached = 0
    if targets:
        try:
            result = direct_api.call(token, client_login, "audiencetargets", "add", {"AudienceTargets": targets})
            failed = dict(direct_api.item_errors(result, "AddResults"))
            for index, name in enumerate(names):
                if index in failed:
                    warnings.append(f"Аудитория «{name}» не добавлена: {failed[index]}")
                else:
                    attached += 1
        except YandexError as exc:
            warnings.append(f"Аудитории не добавлены: {exc.message or exc.code}")
    return {"attached": attached, "warnings": warnings}
