"""Чистка запросов поверх одного проекта: память «для всех проектов» и фраза из чистки в общий минус-список.

Память «для всех проектов» (user_cleaning_memory). Решение «вакансии — мусор» маркетолог помечает в памяти решений
проекта — и оно работает во всех его проектах: бесплатное правило разметки (после загрузки запросов и перед ИИ)
и пример для ИИ. Оно слабее памяти самого проекта: проектные правила проверяются первыми, а слово, которое задело
бы запрос, отмеченный в проекте целевым, в этом проекте не действует. Подсказка по общей памяти, задевающая ключи
кампании запроса, не ставится — запрос остаётся ИИ или вам.

«В общий список …». Фраза из чистки уходит в общий список минус-фраз пользователя, а после подтверждения — во все
кампании, где этот список стоит (задачей apply_negative_list в режиме «догнать»: журнал, откат, колокольчик).
Фразы, задевающие ключи этих кампаний, пропускаем, если не попросили «всё равно».
"""

from __future__ import annotations

import logging

from app.db import pool
from app.journal import log_action
from app.negative_lists import (
    _linked_ids,
    _list_row,
    negatives_length,
    phrase_key,
    phrase_problems,
    stale_campaign_ids,
    update_negative_list,
)
from app.workspaces import require_workspace
from app.yandex import YandexError

log = logging.getLogger("app.shared_cleaning")

SHARED_MAX = 500  # записей «для всех проектов» у одного пользователя
TO_LIST_MAX = 100  # фраз из чистки в список за раз
CAMPAIGNS_SHOWN = 20
SHARED_LEVEL = "campaign"  # общая память — про смысл всего бизнеса, а не про одну группу


def _qc():
    # query_cleaning импортирует этот модуль лениво; здесь — тоже лениво, чтобы не было цикла.
    from app import query_cleaning

    return query_cleaning


def _iso(value) -> str | None:
    return value.isoformat() if value else None


# ---------------------------------------------------------------- память «для всех проектов»


def list_shared_memory(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.phrase, m.query, m.workspace_id, m.created_at, w.name AS workspace_name
            FROM user_cleaning_memory m
            LEFT JOIN workspaces w ON w.id = m.workspace_id
            WHERE m.user_id = %s
            ORDER BY m.created_at DESC, m.id DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "phrase": r["phrase"],
            "query": r["query"],
            "workspace_id": r["workspace_id"],
            "workspace_name": r["workspace_name"] or "",
            "created_at": _iso(r["created_at"]),
        }
        for r in rows
    ]


def _insert_shared(user_id: int, entries: list[tuple[str, str, int | None]]) -> int:
    """entries — (фраза, пример запроса, проект). Уже помеченные не дублируем. → сколько добавили."""
    with pool.connection() as conn:
        have = conn.execute(
            "SELECT COUNT(*) AS n FROM user_cleaning_memory WHERE user_id = %s", (user_id,)
        ).fetchone()["n"]
        added = 0
        for phrase, query, workspace_id in entries:
            if have + added >= SHARED_MAX:
                raise YandexError("limit", f"В памяти для всех проектов не больше {SHARED_MAX} записей")
            cur = conn.execute(
                """
                INSERT INTO user_cleaning_memory (user_id, phrase, phrase_key, query, workspace_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, phrase_key) DO NOTHING
                """,
                (user_id, phrase, phrase_key(phrase), query[:500], workspace_id),
            )
            added += max(cur.rowcount, 0)
    return added


def add_shared_memory(
    user_id: int, *, workspace_id: int | None = None, query_norms: list[str] | None = None, phrase: str | None = None
) -> dict:
    """Пометить «для всех проектов»: решения «в минус» из памяти проекта или одно слово / фразу вручную."""
    qc = _qc()
    entries: list[tuple[str, str, int | None]] = []
    if phrase is not None:
        text = qc.clean_negative(str(phrase))
        problems = phrase_problems([text]) if text else ["Пустая минус-фраза"]
        if problems:
            raise YandexError("phrase", problems[0])
        entries.append((text, "", None))
    else:
        if not workspace_id:
            raise YandexError("empty")
        require_workspace(user_id, int(workspace_id))
        norms = [str(n) for n in query_norms or [] if str(n or "").strip()][:SHARED_MAX]
        if not norms:
            raise YandexError("empty", "Не выбрано ни одного решения")
        with pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT query_norm, query, negative_phrase FROM query_labels
                WHERE workspace_id = %s AND user_id = %s AND confirmed = TRUE AND verdict = 'negative'
                  AND negative_phrase <> '' AND query_norm = ANY(%s)
                ORDER BY updated_at DESC
                """,
                (int(workspace_id), user_id, norms),
            ).fetchall()
        # Без фразы — решения «ключ взят в кавычки»: минусовать там нечего (как и в _memory_rules проекта).
        for r in rows:
            text = qc.clean_negative(r["negative_phrase"])
            if text and not phrase_problems([text]):
                entries.append((text, r["query"] or "", int(workspace_id)))
        if not entries:
            raise YandexError("empty", "Для всех проектов подходят только решения «в минус»")
    added = _insert_shared(user_id, entries)
    return {"added": added, "items": list_shared_memory(user_id)}


def remove_shared_memory(user_id: int, ids: list[int]) -> dict:
    ids = [int(i) for i in ids or []]
    if not ids:
        raise YandexError("empty")
    with pool.connection() as conn:
        cur = conn.execute("DELETE FROM user_cleaning_memory WHERE user_id = %s AND id = ANY(%s)", (user_id, ids))
    return {"removed": cur.rowcount}


def _project_view(workspace_id: int) -> tuple[list[dict], list[list[str]], set[str]]:
    """Общая память глазами проекта → (записи владельца проекта, основы целевых запросов, ключи фраз памяти проекта)."""
    with pool.connection() as conn:
        shared = conn.execute(
            """
            SELECT m.phrase, m.phrase_key, m.query
            FROM user_cleaning_memory m
            JOIN workspaces w ON w.user_id = m.user_id
            WHERE w.id = %s
            ORDER BY m.created_at DESC, m.id DESC
            """,
            (workspace_id,),
        ).fetchall()
        if not shared:
            return [], [], set()
        labels = conn.execute(
            """
            SELECT query_norm, verdict, negative_phrase FROM query_labels
            WHERE workspace_id = %s AND confirmed = TRUE
            """,
            (workspace_id,),
        ).fetchall()
    qc = _qc()
    targets = [qc.stems(r["query_norm"]) for r in labels if r["verdict"] == "target"]
    own = {phrase_key(r["negative_phrase"]) for r in labels if r["verdict"] == "negative" and r["negative_phrase"]}
    return list(shared), targets, own


def shared_rules(workspace_id: int) -> list[tuple[str, list[str], str, str, str]]:
    """Правила «для всех проектов» для этого проекта: (фраза, основы, уровень, пример, "shared").

    Без тех, что уже есть в памяти проекта (проектная — первой), и тех, что задели бы запрос, отмеченный
    в проекте целевым: решение проекта сильнее общей памяти."""
    qc = _qc()
    shared, targets, own = _project_view(workspace_id)
    out = []
    for r in shared:
        if r["phrase_key"] in own:
            continue
        neg = qc.stems(r["phrase"])
        if not neg or any(qc.blocks_stems(neg, t) for t in targets):
            continue
        out.append((r["phrase"], neg, SHARED_LEVEL, r["query"] or r["phrase"], "shared"))
    return out


def shared_examples(workspace_id: int, limit: int) -> list[dict]:
    """Примеры для ИИ из общей памяти — после примеров проекта и с теми же исключениями, что у правил."""
    if limit <= 0:
        return []
    return [
        {"query": query, "negative_phrase": phrase, "level": level}
        for phrase, _neg, level, query, _src in shared_rules(workspace_id)[:limit]
    ]


# ---------------------------------------------------------------- фраза из чистки в общий список


def _list_campaigns(user_id: int, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT c.id, c.name, a.client_login, cl.name AS client_name
            FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            LEFT JOIN clients cl ON cl.id = a.client_row_id
            WHERE c.user_id = %s AND c.id = ANY(%s)
            ORDER BY cl.name ASC NULLS LAST, a.client_login ASC, c.name ASC
            """,
            (user_id, ids),
        ).fetchall()


def _clean_to_list_items(items) -> list[dict]:
    out, seen = [], set()
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        norm = str(raw.get("query_norm") or "").strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append({"query_norm": norm, "negative_phrase": str(raw.get("negative_phrase") or "").strip()})
    if not out:
        raise YandexError("empty", "Не выбрано ни одного запроса")
    if len(out) > TO_LIST_MAX:
        raise YandexError("limit", f"Не больше {TO_LIST_MAX} запросов за раз")
    return out


def to_list_preview(user_id: int, workspace_id: int, list_id: int, items: list[dict]) -> dict:
    """Что будет, если отправить фразы запросов в общий список: кампании списка, пересечения с их ключами
    и кампании запросов, где списка нет (туда фраза не попадёт). Ничего не меняет."""
    qc = _qc()
    require_workspace(user_id, workspace_id)
    nlist = _list_row(user_id, list_id)
    items = _clean_to_list_items(items)
    rows = qc._rows_for(user_id, workspace_id, [i["query_norm"] for i in items])
    linked = sorted(_linked_ids(list_id))
    index = qc._keyword_index(user_id, linked)
    place = {"campaign_row_ids": linked, "adgroup_row_ids": []}
    in_list = {phrase_key(p) for p in nlist["phrases"] or []}
    phrases = []
    outside: set[int] = set()
    for item in items:
        row = rows.get(item["query_norm"])
        if row is None:
            phrases.append(
                {"query_norm": item["query_norm"], "query": item["query_norm"], "phrase": "", "problems": ["Запроса нет в отчёте — обновите запросы"], "conflicts": [], "conflicts_total": 0, "in_list": False, "reaches": False}
            )
            continue
        phrase = qc.clean_negative(item["negative_phrase"] or row["query"])
        problems = phrase_problems([phrase]) if phrase else ["Пустая минус-фраза"]
        conflicts = qc.find_conflicts(phrase, "campaign", place, index) if phrase and not problems else []
        own = {int(c) for c in row["campaign_row_ids"] or []}
        outside.update(own - set(linked))
        phrases.append(
            {
                "query_norm": item["query_norm"],
                "query": row["query"],
                "phrase": phrase,
                "problems": problems,
                "conflicts": [qc._conflict_out(k) for k in conflicts[:5]],
                "conflicts_total": len(conflicts),
                "in_list": phrase_key(phrase) in in_list,
                # Стоит ли список хоть в одной кампании самого запроса: иначе фраза до его показов не дойдёт.
                "reaches": bool(own & set(linked)),
            }
        )
    camps = _list_campaigns(user_id, linked)
    out_camps = _list_campaigns(user_id, sorted(outside))
    return {
        "list": {"id": nlist["id"], "name": nlist["name"], "phrases_count": len(nlist["phrases"] or [])},
        "phrases": phrases,
        "campaigns": [
            {"campaign_row_id": c["id"], "campaign_name": c["name"], "client_name": c["client_name"] or c["client_login"]}
            for c in camps[:CAMPAIGNS_SHOWN]
        ],
        "campaigns_total": len(camps),
        "outside": [c["name"] for c in out_camps[:5]],
        "outside_total": len(out_camps),
    }


def send_to_list(user_id: int, workspace_id: int, list_id: int, items: list[dict], *, force: bool = False) -> dict:
    """Фразы запросов → в общий список; решения — в память проекта. В кампании списка пишет задача «догнать»,
    её ставит вызывающий по stale_ids. Пересекающиеся с ключами фразы пропускаем, если не force."""
    qc = _qc()
    plan = to_list_preview(user_id, workspace_id, list_id, items)
    nlist = _list_row(user_id, list_id)
    accepted, results = [], []
    for p in plan["phrases"]:
        if p["problems"]:
            results.append({"query_norm": p["query_norm"], "status": "error", "message": p["problems"][0]})
        elif not p["reaches"]:
            # Запрос остался бы «решённым», а в его кампаниях фразы нет — он тратил бы дальше. Пусть ждёт решения.
            results.append(
                {
                    "query_norm": p["query_norm"],
                    "status": "skipped",
                    "message": "Списка нет в кампаниях этого запроса — минусуйте на группу или кампанию",
                    "phrase": p["phrase"],
                }
            )
        elif p["conflicts_total"] and not force:
            results.append(
                {
                    "query_norm": p["query_norm"],
                    "status": "skipped",
                    "message": "Заденет ключи кампаний списка: " + ", ".join(f"«{k['keyword']}»" for k in p["conflicts"][:3]),
                    "conflicts": p["conflicts"],
                    "phrase": p["phrase"],
                }
            )
        else:
            accepted.append(p)
            results.append({"query_norm": p["query_norm"], "status": "ok", "message": "", "phrase": p["phrase"]})

    current = list(nlist["phrases"] or [])
    keys = {phrase_key(x) for x in current}
    new: list[str] = []
    for p in accepted:
        key = phrase_key(p["phrase"])
        if key not in keys:
            keys.add(key)
            new.append(p["phrase"])
    if new:
        if negatives_length(current + new) > 20_000:
            raise YandexError("negatives-limit")
        update_negative_list(user_id, list_id, phrases=current + new)

    log_id = None
    if accepted:
        decisions = [
            {
                "query_norm": p["query_norm"],
                "query": p["query"],
                "verdict": "negative",
                "negative_phrase": p["phrase"],
                "level": SHARED_LEVEL,
                "reason": f"В общий список «{nlist['name']}»",
                "applied": False,
            }
            for p in accepted
        ]
        previous = qc.snapshot_labels(workspace_id, [d["query_norm"] for d in decisions])
        qc.remember_decisions(user_id, workspace_id, decisions)
        steps = []
        if new:
            steps.append({"op": "negative_list_phrases", "args": {"list_id": list_id, "name": nlist["name"], "remove": new}})
        steps.append({"op": "query_labels_restore", "args": {"workspace_id": workspace_id, "labels": previous}})
        statuses = [r["status"] for r in results]
        log_id = log_action(
            user_id,
            "queries",
            {"query_norms": [p["query_norm"] for p in accepted][:50], "list_id": list_id, "force": force},
            {
                "negative": len(accepted),
                "target": 0,
                "keywords": 0,
                "added_to_list": len(new),
                "skipped": statuses.count("skipped"),
                "errors": statuses.count("error"),
            },
            "ok" if len(accepted) == len(results) else "partial",
            workspace_id=workspace_id,
            title=f"Чистка запросов: в общий список «{nlist['name']}» {len(accepted)}",
            undo={"label": "Вернуть как было", "steps": steps},
            targets=[f"negative_list:{list_id}"],
        )
    statuses = [r["status"] for r in results]
    ok_n = statuses.count("ok")
    return {
        "items": results,
        "negative": ok_n,
        "added_to_list": len(new),
        "skipped": statuses.count("skipped"),
        "errors": statuses.count("error"),
        "status": "ok" if ok_n == len(results) else ("partial" if ok_n else "error"),
        "log_id": log_id,
        "list": {"id": list_id, "name": nlist["name"]},
        "stale_ids": stale_campaign_ids(user_id, list_id) if accepted else [],
        # Задача «догнать» пишет только эти фразы: другие неприменённые правки списка не были в подтверждении.
        "phrases": [p["phrase"] for p in accepted],
    }


def remove_list_phrases(user_id: int, list_id: int, remove: list[str]) -> dict:
    """Шаг отката: убрать из списка фразы, добавленные из чистки. Кампании догонит «Догнать N кампаний»."""
    try:
        nlist = _list_row(user_id, list_id)
    except YandexError:
        return {"applied": 0, "missing": 1}
    drop = {phrase_key(p) for p in remove or []}
    current = list(nlist["phrases"] or [])
    keep = [p for p in current if phrase_key(p) not in drop]
    if len(keep) != len(current):
        update_negative_list(user_id, list_id, phrases=keep)
    return {"applied": len(current) - len(keep)}
