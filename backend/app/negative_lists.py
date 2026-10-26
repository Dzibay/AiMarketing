"""Общие списки минус-фраз: один список — на кампании разных клиентов.

Список хранится у нас и вливается в минус-фразы кампаний Директа. Для каждой кампании
помним фразы, которые добавил именно список (added_phrases): при удалении фразы из списка
или отвязке убираем только их — минус-фразы, бывшие в кампании до списка, не трогаем.

Наборы минус-фраз Директа (NegativeKeywordSharedSets) не подошли: они вешаются только
на группы (до 3 на группу, до 30 на аккаунт, 4096 символов), и новые группы их не получают.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Callable

from psycopg.types.json import Jsonb

from app.actions import _call_campaigns
from app.journal import log_action as _log_action
from app.adgroups import _parse_phrases
from app.db import pool
from app.worker_lock import try_worker_lock
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.negative_lists")

MAX_WORDS = 7
MAX_WORD_CHARS = 35
# Суммарная длина минус-фраз кампании; пробелы, дефисы и операторы не считаются.
CAMPAIGN_CHARS_LIMIT = 20_000
MAX_TARGETS = 2000
UPDATE_BATCH = 10  # campaigns.update принимает не более 10 кампаний за вызов
GET_BATCH = 1000

_NOT_COUNTED = re.compile(r'[\s\-!+"\[\]]')
_MINUS_SPLIT = re.compile(r"\s+-(?=\S)")

# Стартовые словари: пользователь копирует их в свой список и правит.
TEMPLATES = [
    {
        "key": "free",
        "name": "Бесплатное и пиратское",
        "phrases": ["бесплатно", "бесплатный", "даром", "халява", "скачать", "торрент", "взлом", "кряк", "crack"],
    },
    {
        "key": "jobs",
        "name": "Работа и вакансии",
        "phrases": ["вакансия", "работа", "резюме", "зарплата", "подработка", "стажировка", "трудоустройство", "hh"],
    },
    {
        "key": "diy",
        "name": "Учёба и «своими руками»",
        "phrases": ["реферат", "курсовая", "диплом", "доклад", "сочинение", "своими руками", "самостоятельно", "чертеж", "схема"],
    },
    {
        "key": "info",
        "name": "Информационные запросы",
        "phrases": ["википедия", "фото", "картинки", "видео", "смотреть", "форум", "история", "определение"],
    },
    {
        "key": "used",
        "name": "Б/у и барахолки",
        "phrases": ["бу", "подержанный", "авито", "юла", "барахолка", "обмен"],
    },
]

BLOCK_REASONS = ("disabled", "archived", "unsupported", "negatives-limit")

Progress = Callable[[int, int, str], None]


def phrase_key(phrase: str) -> str:
    """Директ не различает регистр и ё/е — сравниваем так же."""
    return " ".join(str(phrase).lower().replace("ё", "е").split())


def negatives_length(phrases: list[str]) -> int:
    return sum(len(_NOT_COUNTED.sub("", p)) for p in phrases)


def parse_phrases(value) -> list[str]:
    """Строки и запятые, а также «-слово -слово» как в интерфейсе Директа → уникальные фразы."""
    if isinstance(value, str):
        chunks = re.split(r"[\n,;]", value)
    elif isinstance(value, list):
        chunks = [str(v or "") for v in value]
    else:
        return []
    phrases: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        chunk = chunk.strip()
        parts = _MINUS_SPLIT.split(chunk) if chunk.startswith("-") else [chunk]
        for part in parts:
            text = " ".join(part.strip().lstrip("-").split())
            key = phrase_key(text)
            if text and key not in seen:
                seen.add(key)
                phrases.append(text)
    return phrases


def phrase_problems(phrases: list[str]) -> list[str]:
    problems = []
    for phrase in phrases:
        words = phrase.split()
        if len(words) > MAX_WORDS:
            problems.append(f"«{phrase}»: больше {MAX_WORDS} слов")
        elif any(len(_NOT_COUNTED.sub("", w)) > MAX_WORD_CHARS for w in words):
            problems.append(f"«{phrase}»: слово длиннее {MAX_WORD_CHARS} символов")
        elif any(w.startswith("-") for w in words[1:]):
            problems.append(f"«{phrase}»: минус внутри фразы — разбейте на отдельные строки")
    return problems


def _validated(phrases_raw) -> list[str]:
    phrases = parse_phrases(phrases_raw)
    problems = phrase_problems(phrases)
    if problems:
        more = f" и ещё {len(problems) - 3}" if len(problems) > 3 else ""
        raise YandexError("phrase", "; ".join(problems[:3]) + more)
    if negatives_length(phrases) > CAMPAIGN_CHARS_LIMIT:
        raise YandexError("negatives-limit")
    return phrases


def _name(raw: str | None) -> str:
    name = (raw or "").strip()[:255]
    if not name:
        raise YandexError("empty")
    return name


def plan_campaign(
    current: list[str],
    desired: list[str] | None,
    owned: list[str],
    protected: set[str],
) -> dict:
    """
    Что сделать с минус-фразами одной кампании.

    current — фразы кампании сейчас; desired — фразы списка (None — отвязать список);
    owned — что список добавил раньше; protected — ключи фраз других списков этой кампании:
    такие фразы не убираем, а передаём тому списку (handover).
    """
    cur_keys = {phrase_key(p) for p in current}
    want = {phrase_key(p) for p in desired or []}
    owned_keys = {phrase_key(p) for p in owned}
    drop = {k for k in owned_keys if k not in want and k not in protected}

    add: list[str] = []
    seen = set(cur_keys)
    for phrase in desired or []:
        key = phrase_key(phrase)
        if key not in seen:
            seen.add(key)
            add.append(phrase)
    remove = [p for p in current if phrase_key(p) in drop]
    final = [p for p in current if phrase_key(p) not in drop] + add
    kept = [p for p in owned if phrase_key(p) in want and phrase_key(p) in cur_keys]
    handover = [
        p for p in owned if phrase_key(p) not in want and phrase_key(p) in protected and phrase_key(p) in cur_keys
    ]
    return {
        "add": add,
        "remove": remove,
        "final": final,
        "owned": kept + add,
        "handover": handover,
        "changed": bool(add or remove),
    }


def _blocked(row: dict) -> str:
    if not row.get("enabled"):
        return "disabled"
    if str(row.get("state") or "") == "ARCHIVED":
        return "archived"
    # Медийные кампании минус-фраз не поддерживают.
    if str(row.get("campaign_type") or "").startswith("CPM_"):
        return "unsupported"
    return ""


def _iso(value) -> str | None:
    return value.isoformat() if value else None


# ---------------------------------------------------------------- хранение списков


def _list_row(user_id: int, list_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT * FROM negative_lists WHERE id = %s AND user_id = %s",
            (list_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return row


def _public_list(row: dict) -> dict:
    phrases = list(row.get("phrases") or [])
    return {
        "id": row["id"],
        "name": row["name"],
        "phrases": phrases,
        "phrases_count": len(phrases),
        "chars": negatives_length(phrases),
        "linked": int(row.get("linked") or 0),
        "stale": int(row.get("stale") or 0),
        "phrases_updated_at": _iso(row.get("phrases_updated_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def list_negative_lists(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT l.*,
                   COUNT(lc.campaign_row_id) AS linked,
                   COUNT(lc.campaign_row_id) FILTER (WHERE lc.applied_at < l.phrases_updated_at) AS stale
            FROM negative_lists l
            LEFT JOIN negative_list_campaigns lc ON lc.list_id = l.id
            WHERE l.user_id = %s
            GROUP BY l.id
            ORDER BY l.name ASC, l.id ASC
            """,
            (user_id,),
        ).fetchall()
    return [_public_list(r) for r in rows]


def get_negative_list(user_id: int, list_id: int) -> dict:
    row = _list_row(user_id, list_id)
    with pool.connection() as conn:
        links = conn.execute(
            """
            SELECT lc.campaign_row_id, lc.applied_at, lc.added_phrases,
                   c.campaign_id, c.name AS campaign_name, c.state,
                   a.client_login, cl.name AS client_name
            FROM negative_list_campaigns lc
            JOIN direct_campaigns c ON c.id = lc.campaign_row_id
            JOIN direct_accounts a ON a.id = c.account_id
            LEFT JOIN clients cl ON cl.id = a.client_row_id
            WHERE lc.list_id = %s AND lc.user_id = %s
            ORDER BY cl.name ASC NULLS LAST, a.client_login ASC, c.name ASC
            """,
            (list_id, user_id),
        ).fetchall()
    out = _public_list(
        {
            **row,
            "linked": len(links),
            "stale": sum(1 for link in links if link["applied_at"] < row["phrases_updated_at"]),
        }
    )
    out["campaigns"] = [
        {
            "campaign_row_id": link["campaign_row_id"],
            "campaign_id": link["campaign_id"],
            "campaign_name": link["campaign_name"],
            "state": link["state"],
            "client_login": link["client_login"],
            "client_name": link["client_name"] or "",
            "added": len(link["added_phrases"] or []),
            "applied_at": _iso(link["applied_at"]),
            "stale": link["applied_at"] < row["phrases_updated_at"],
        }
        for link in links
    ]
    return out


def create_negative_list(user_id: int, *, name: str, phrases=None) -> dict:
    name_n = _name(name)
    cleaned = _validated(phrases or [])
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO negative_lists (user_id, name, phrases)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (user_id, name_n, Jsonb(cleaned)),
        ).fetchone()
    return get_negative_list(user_id, row["id"])


def update_negative_list(user_id: int, list_id: int, *, name: str | None = None, phrases=None) -> dict:
    row = _list_row(user_id, list_id)
    sets = ["updated_at = NOW()"]
    params: list = []
    if name is not None:
        sets.append("name = %s")
        params.append(_name(name))
    if phrases is not None:
        cleaned = _validated(phrases)
        if cleaned != list(row["phrases"] or []):
            sets.append("phrases = %s")
            params.append(Jsonb(cleaned))
            if [phrase_key(p) for p in cleaned] != [phrase_key(p) for p in row["phrases"] or []]:
                sets.append("phrases_updated_at = NOW()")
    params.extend([list_id, user_id])
    with pool.connection() as conn:
        conn.execute(
            f"UPDATE negative_lists SET {', '.join(sets)} WHERE id = %s AND user_id = %s",
            params,
        )
    return get_negative_list(user_id, list_id)


def delete_negative_list(user_id: int, list_id: int) -> None:
    _list_row(user_id, list_id)
    with pool.connection() as conn:
        linked = conn.execute(
            "SELECT COUNT(*) AS n FROM negative_list_campaigns WHERE list_id = %s",
            (list_id,),
        ).fetchone()["n"]
        if linked:
            # Иначе фразы списка остались бы в кампаниях без хозяина.
            raise YandexError("in-use")
        conn.execute("DELETE FROM negative_lists WHERE id = %s AND user_id = %s", (list_id, user_id))


# ---------------------------------------------------------------- кампании и план


def _campaigns(user_id: int, ids: list[int] | None = None) -> list[dict]:
    where = "c.user_id = %s"
    params: list = [user_id]
    if ids is not None:
        where += " AND c.id = ANY(%s)"
        params.append(ids)
    with pool.connection() as conn:
        return conn.execute(
            f"""
            SELECT c.id, c.campaign_id, c.name, c.campaign_type, c.state,
                   c.negative_keywords, c.synced_at,
                   a.id AS account_id, a.connection_id, a.client_login, a.enabled,
                   cl.id AS client_id, cl.name AS client_name
            FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            LEFT JOIN clients cl ON cl.id = a.client_row_id
            WHERE {where}
            ORDER BY cl.name ASC NULLS LAST, a.client_login ASC, c.name ASC, c.campaign_id ASC
            """,
            params,
        ).fetchall()


def list_targets(user_id: int) -> list[dict]:
    """Кампании всех клиентов для выбора, к чему применить список."""
    out = []
    for row in _campaigns(user_id):
        negatives = _parse_phrases(row.get("negative_keywords") or "[]")
        out.append(
            {
                "id": row["id"],
                "campaign_id": row["campaign_id"],
                "name": row["name"],
                "campaign_type": row["campaign_type"],
                "state": row["state"],
                "account_id": row["account_id"],
                "client_login": row["client_login"],
                "client_id": row["client_id"],
                "client_name": row["client_name"] or "",
                "negatives": len(negatives),
                "chars": negatives_length(negatives),
                "blocked": _blocked(row),
            }
        )
    return out


def _links(user_id: int, campaign_row_ids: list[int]) -> dict[int, list[dict]]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT lc.list_id, lc.campaign_row_id, lc.added_phrases, l.phrases
            FROM negative_list_campaigns lc
            JOIN negative_lists l ON l.id = lc.list_id
            WHERE lc.user_id = %s AND lc.campaign_row_id = ANY(%s)
            """,
            (user_id, campaign_row_ids),
        ).fetchall()
    by_campaign: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_campaign[row["campaign_row_id"]].append(row)
    return by_campaign


def _linked_ids(list_id: int) -> set[int]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT campaign_row_id FROM negative_list_campaigns WHERE list_id = %s",
            (list_id,),
        ).fetchall()
    return {r["campaign_row_id"] for r in rows}


def _selection(user_id: int, list_id: int, campaign_row_ids: list[int]) -> tuple[dict, set[int], list[dict]]:
    nlist = _list_row(user_id, list_id)
    selected = {int(x) for x in campaign_row_ids}
    if len(selected) > MAX_TARGETS:
        raise YandexError("limit", f"Слишком много кампаний за раз (макс. {MAX_TARGETS}).")
    rows = _campaigns(user_id, sorted(selected | _linked_ids(list_id)))
    if not selected <= {r["id"] for r in rows}:
        raise YandexError("not-found")
    return nlist, selected, rows


def _entry(nlist: dict, row: dict, selected: set[int], links: list[dict], current: list[str]) -> dict:
    mine = next((link for link in links if link["list_id"] == nlist["id"]), None)
    protected = {phrase_key(p) for link in links if link["list_id"] != nlist["id"] for p in link["phrases"] or []}
    is_selected = row["id"] in selected
    desired = list(nlist["phrases"] or []) if is_selected else None
    plan = plan_campaign(current, desired, list(mine["added_phrases"] or []) if mine else [], protected)
    action = "detach" if not is_selected else ("update" if mine else "attach")

    reason = _blocked(row)
    blocked = ""
    if reason == "unsupported" and action != "detach":
        blocked = reason
    elif plan["changed"] and reason:
        blocked = reason
    elif plan["changed"] and negatives_length(plan["final"]) > CAMPAIGN_CHARS_LIMIT:
        blocked = "negatives-limit"
    return {
        "campaign_row_id": row["id"],
        "campaign_id": row["campaign_id"],
        "campaign_name": row["name"],
        "client_name": row["client_name"] or "",
        "client_login": row["client_login"],
        "action": action,
        "add": plan["add"],
        "remove": plan["remove"],
        "before": len(current),
        "after": len(plan["final"]),
        "chars": negatives_length(plan["final"]),
        "changed": plan["changed"],
        "blocked": blocked,
        "_plan": plan,
    }


def _public_entry(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def _totals(entries: list[dict]) -> dict:
    active = [e for e in entries if not e["blocked"]]
    return {
        "campaigns": len(entries),
        "changed": sum(1 for e in active if e["changed"]),
        "add": sum(len(e["add"]) for e in active),
        "remove": sum(len(e["remove"]) for e in active),
        "blocked": sum(1 for e in entries if e["blocked"]),
        "attach": sum(1 for e in active if e["action"] == "attach"),
        "detach": sum(1 for e in active if e["action"] == "detach"),
    }


def preview_apply(user_id: int, list_id: int, campaign_row_ids: list[int]) -> dict:
    """План по данным последней синхронизации; при применении минус-фразы перечитываются из Директа."""
    nlist, selected, rows = _selection(user_id, list_id, campaign_row_ids)
    links = _links(user_id, [r["id"] for r in rows])
    entries = [
        _entry(nlist, row, selected, links.get(row["id"], []), _parse_phrases(row.get("negative_keywords") or "[]"))
        for row in rows
    ]
    synced = [r["synced_at"] for r in rows if r.get("synced_at")]
    return {
        "list": {"id": nlist["id"], "name": nlist["name"], "phrases_count": len(nlist["phrases"] or [])},
        "campaigns": [_public_entry(e) for e in entries],
        "totals": _totals(entries),
        "synced_at": _iso(min(synced)) if synced else None,
    }


# ---------------------------------------------------------------- применение


def fetch_campaign_negatives(access_token: str, client_login: str, campaign_ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for i in range(0, len(campaign_ids), GET_BATCH):
        result = _call_campaigns(
            access_token,
            client_login,
            "get",
            {"SelectionCriteria": {"Ids": campaign_ids[i : i + GET_BATCH]}, "FieldNames": ["Id", "NegativeKeywords"]},
        )
        for item in result.get("Campaigns") or []:
            raw = item.get("NegativeKeywords") or {}
            items = raw.get("Items") if isinstance(raw, dict) else None
            out[int(item["Id"])] = [str(p).strip() for p in items or [] if str(p or "").strip()]
    return out


def _commit(user_id: int, list_id: int, entry: dict) -> None:
    """Локально: минус-фразы кампании, привязка списка и передача общих фраз другим спискам."""
    plan = entry["_plan"]
    campaign_row_id = entry["campaign_row_id"]
    with pool.connection() as conn:
        conn.execute(
            "UPDATE direct_campaigns SET negative_keywords = %s, synced_at = NOW() WHERE id = %s AND user_id = %s",
            (json.dumps(plan["final"], ensure_ascii=False), campaign_row_id, user_id),
        )
        if entry["action"] == "detach":
            conn.execute(
                "DELETE FROM negative_list_campaigns WHERE list_id = %s AND campaign_row_id = %s",
                (list_id, campaign_row_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO negative_list_campaigns (list_id, campaign_row_id, user_id, added_phrases, applied_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (list_id, campaign_row_id) DO UPDATE SET
                    added_phrases = EXCLUDED.added_phrases,
                    applied_at = NOW()
                """,
                (list_id, campaign_row_id, user_id, Jsonb(plan["owned"])),
            )
        if not plan["handover"]:
            return
        others = conn.execute(
            """
            SELECT lc.list_id, lc.added_phrases, l.phrases
            FROM negative_list_campaigns lc
            JOIN negative_lists l ON l.id = lc.list_id
            WHERE lc.campaign_row_id = %s AND lc.list_id <> %s
            ORDER BY lc.list_id
            FOR UPDATE OF lc
            """,
            (campaign_row_id, list_id),
        ).fetchall()
        for other in others:
            keys = {phrase_key(p) for p in other["phrases"] or []}
            added = list(other["added_phrases"] or [])
            take = [p for p in plan["handover"] if phrase_key(p) in keys]
            if not take:
                continue
            added_keys = {phrase_key(p) for p in added}
            added.extend(p for p in take if phrase_key(p) not in added_keys)
            plan["handover"] = [p for p in plan["handover"] if p not in take]
            conn.execute(
                "UPDATE negative_list_campaigns SET added_phrases = %s WHERE list_id = %s AND campaign_row_id = %s",
                (Jsonb(added), other["list_id"], campaign_row_id),
            )


def _update_errors(result: dict) -> list[str]:
    messages = []
    for item in result.get("UpdateResults") or []:
        errors = item.get("Errors") or []
        messages.append(str(errors[0].get("Message") or errors[0].get("Details") or "direct") if errors else "")
    return messages


def _apply_account(user_id: int, nlist: dict, selected: set[int], group: list[dict], out: dict) -> None:
    first = group[0]
    if not first.get("enabled"):
        out["skipped"].extend({"campaign_row_id": r["id"], "reason": "disabled"} for r in group)
        return
    token = get_connection_access_token(first["connection_id"], user_id)
    fresh = fetch_campaign_negatives(token, first["client_login"], [int(r["campaign_id"]) for r in group])
    links = _links(user_id, [r["id"] for r in group])

    entries = []
    for row in group:
        current = fresh.get(int(row["campaign_id"]))
        if current is None:
            out["skipped"].append({"campaign_row_id": row["id"], "reason": "not-found"})
            continue
        entry = _entry(nlist, row, selected, links.get(row["id"], []), current)
        if entry["blocked"]:
            out["skipped"].append({"campaign_row_id": row["id"], "reason": entry["blocked"]})
        elif not entry["changed"]:
            _commit(user_id, nlist["id"], entry)  # привязка без записи в Директ
            out["linked" if entry["action"] != "detach" else "detached"] += 1
        else:
            entries.append(entry)

    for i in range(0, len(entries), UPDATE_BATCH):
        batch = entries[i : i + UPDATE_BATCH]
        campaigns = [
            {
                "Id": int(e["campaign_id"]),
                # Пустой список Директ принимает только как null.
                "NegativeKeywords": {"Items": e["_plan"]["final"]} if e["_plan"]["final"] else None,
            }
            for e in batch
        ]
        result = _call_campaigns(token, first["client_login"], "update", {"Campaigns": campaigns})
        messages = _update_errors(result)
        for n, entry in enumerate(batch):
            message = messages[n] if n < len(messages) else ""
            if message:
                out["errors"].append(
                    {"campaign_row_id": entry["campaign_row_id"], "client_login": first["client_login"], "error": "direct", "message": message}
                )
                continue
            _commit(user_id, nlist["id"], entry)
            out["updated"] += 1
            out["added"] += len(entry["add"])
            out["removed"] += len(entry["remove"])
            if entry["action"] == "detach":
                out["detached"] += 1
            else:
                out["linked"] += 1


def apply_negative_list(
    user_id: int,
    list_id: int,
    campaign_row_ids: list[int],
    progress: Progress | None = None,
) -> dict:
    """Записывает список в выбранные кампании и снимает с тех, где его больше нет. Задачей очереди."""
    with try_worker_lock(f"negative-lists:{user_id}") as locked:
        if not locked:
            # Два применения разом перетёрли бы минус-фразы общей кампании — повтор задачи позже.
            raise YandexError("busy")
        nlist, selected, rows = _selection(user_id, list_id, campaign_row_ids)
        by_account: dict[int, list[dict]] = defaultdict(list)
        for row in rows:
            by_account[row["account_id"]].append(row)

        out = {"updated": 0, "added": 0, "removed": 0, "linked": 0, "detached": 0, "skipped": [], "errors": []}
        for n, group in enumerate(by_account.values()):
            if progress:
                progress(n, len(by_account), group[0]["client_name"] or group[0]["client_login"])
            try:
                _apply_account(user_id, nlist, selected, group, out)
            except YandexError as exc:
                out["errors"].append(
                    {"client_login": group[0]["client_login"], "error": exc.code, "message": exc.message or ""}
                )
            except Exception:
                log.exception("apply negative list %s to %s", list_id, group[0]["client_login"])
                out["errors"].append({"client_login": group[0]["client_login"], "error": "direct"})

    done = out["linked"] + out["detached"]
    out["status"] = "ok" if not out["errors"] else ("partial" if done else "error")
    _log_action(
        user_id,
        "negative_list_apply",
        {"list_id": list_id, "name": nlist["name"], "campaign_row_ids": sorted(selected)},
        out,
        out["status"],
    )
    if out["status"] == "error":
        first_error = out["errors"][0]
        raise YandexError(first_error["error"], first_error.get("message") or None)
    return out
