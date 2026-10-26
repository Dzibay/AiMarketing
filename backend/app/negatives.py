"""Точечная правка минус-фраз кампаний и групп: добавить и убрать отдельные фразы.

Читаем свежие минус-фразы из Директа (локальная копия могла устареть), сливаем по ключу
фразы (регистр и ё/е не различаются) и возвращаем, что реально добавилось и убралось, —
по этому строится откат, который не заденет чужие правки.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from contextlib import contextmanager

from app import direct_api
from app.actions import _call_campaigns
from app.db import pool
from app.journal import log_action
from app.negative_lists import (
    CAMPAIGN_CHARS_LIMIT,
    UPDATE_BATCH,
    _blocked,
    _update_errors,
    fetch_campaign_negatives,
    negatives_length,
    phrase_key,
)
from app.worker_lock import try_worker_lock
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.negatives")

GROUP_CHARS_LIMIT = 4096  # минус-фразы группы, без пробелов и операторов
ADGROUP_BATCH = 1000

REASONS = {
    "disabled": "кабинет отключён",
    "archived": "кампания в архиве",
    "unsupported": "тип кампании не поддерживает минус-фразы",
    "negatives-limit": "превышен лимит длины минус-фраз",
    "not-found": "не найдено в Директе",
}


@contextmanager
def negatives_lock(user_id: int, wait_s: float = 15.0):
    """Тот же замок, что у общих списков: две записи разом перетёрли бы минус-фразы друг друга."""
    deadline = time.monotonic() + wait_s
    while True:
        with try_worker_lock(f"negative-lists:{user_id}") as locked:
            if locked:
                yield
                return
        if time.monotonic() >= deadline:
            raise YandexError("busy", "Минус-фразы сейчас меняет другая задача — попробуйте через минуту")
        time.sleep(0.5)


def _merge(current: list[str], add: list[str], remove: list[str]) -> tuple[list[str], list[str], list[str]]:
    """→ (итоговый список, реально добавленные, реально убранные)."""
    drop = {phrase_key(p) for p in remove}
    removed = [p for p in current if phrase_key(p) in drop]
    final = [p for p in current if phrase_key(p) not in drop]
    seen = {phrase_key(p) for p in final}
    added = []
    for phrase in add:
        key = phrase_key(phrase)
        if key and key not in seen and key not in drop:
            seen.add(key)
            final.append(phrase)
            added.append(phrase)
    # Если фразу и убирают, и добавляют — она остаётся как была.
    return final, added, removed


def _campaign_rows(user_id: int, ids: list[int]) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT c.id, c.campaign_id, c.name, c.campaign_type, c.state,
                   a.id AS account_id, a.connection_id, a.client_login, a.enabled
            FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE c.user_id = %s AND c.id = ANY(%s)
            """,
            (user_id, ids),
        ).fetchall()


def _adgroup_rows(user_id: int, ids: list[int]) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT g.id, g.adgroup_id, g.name, g.campaign_row_id,
                   c.campaign_type, c.state,
                   a.id AS account_id, a.connection_id, a.client_login, a.enabled
            FROM direct_adgroups g
            JOIN direct_campaigns c ON c.id = g.campaign_row_id
            JOIN direct_accounts a ON a.id = g.account_id
            WHERE g.user_id = %s AND g.id = ANY(%s)
            """,
            (user_id, ids),
        ).fetchall()


def fetch_adgroup_negatives(access_token: str, client_login: str, adgroup_ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for part in direct_api.chunks(adgroup_ids, 10_000):
        result = direct_api.call(
            access_token,
            client_login,
            "adgroups",
            "get",
            {"SelectionCriteria": {"Ids": part}, "FieldNames": ["Id", "NegativeKeywords"]},
        )
        for item in result.get("AdGroups") or []:
            raw = item.get("NegativeKeywords") or {}
            items = raw.get("Items") if isinstance(raw, dict) else None
            out[int(item["Id"])] = [str(p).strip() for p in items or [] if str(p or "").strip()]
    return out


def _normalize(changes: dict | None) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for key, value in (changes or {}).items():
        add = [str(p).strip() for p in (value or {}).get("add") or [] if str(p or "").strip()]
        remove = [str(p).strip() for p in (value or {}).get("remove") or [] if str(p or "").strip()]
        if add or remove:
            out[int(key)] = {"add": add, "remove": remove}
    return out


def _patch_kind(user_id: int, kind: str, changes: dict[int, dict], out: dict) -> None:
    rows = (_campaign_rows if kind == "campaigns" else _adgroup_rows)(user_id, list(changes))
    by_account: dict[int, list[dict]] = defaultdict(list)
    found = set()
    for row in rows:
        found.add(int(row["id"]))
        by_account[int(row["account_id"])].append(row)
    for missing in set(changes) - found:
        out["skipped"].append({"kind": kind, "id": missing, "reason": "not-found"})

    direct_key = "campaign_id" if kind == "campaigns" else "adgroup_id"
    limit = CAMPAIGN_CHARS_LIMIT if kind == "campaigns" else GROUP_CHARS_LIMIT
    for group in by_account.values():
        first = group[0]
        try:
            token = get_connection_access_token(first["connection_id"], user_id)
            ids = [int(r[direct_key]) for r in group]
            fresh = (
                fetch_campaign_negatives(token, first["client_login"], ids)
                if kind == "campaigns"
                else fetch_adgroup_negatives(token, first["client_login"], ids)
            )
            planned = []
            for row in group:
                blocked = _blocked(row)
                current = fresh.get(int(row[direct_key]))
                if not blocked and current is None:
                    blocked = "not-found"
                if blocked:
                    out["skipped"].append({"kind": kind, "id": row["id"], "reason": blocked})
                    continue
                change = changes[int(row["id"])]
                final, added, removed = _merge(current, change["add"], change["remove"])
                if not added and not removed:
                    continue
                if added and negatives_length(final) > limit:
                    out["skipped"].append({"kind": kind, "id": row["id"], "reason": "negatives-limit"})
                    continue
                planned.append((row, final, added, removed))

            batch_size = UPDATE_BATCH if kind == "campaigns" else ADGROUP_BATCH
            for batch in direct_api.chunks(planned, batch_size):
                items = [
                    # Пустой список Директ принимает только как null.
                    {"Id": int(row[direct_key]), "NegativeKeywords": {"Items": final} if final else None}
                    for row, final, _a, _r in batch
                ]
                if kind == "campaigns":
                    result = _call_campaigns(token, first["client_login"], "update", {"Campaigns": items})
                else:
                    result = direct_api.call(token, first["client_login"], "adgroups", "update", {"AdGroups": items})
                messages = _update_errors(result)
                table = "direct_campaigns" if kind == "campaigns" else "direct_adgroups"
                for n, (row, final, added, removed) in enumerate(batch):
                    message = messages[n] if n < len(messages) else ""
                    if message:
                        out["errors"].append({"kind": kind, "id": row["id"], "error": "direct", "message": message})
                        continue
                    with pool.connection() as conn:
                        conn.execute(
                            f"UPDATE {table} SET negative_keywords = %s, synced_at = NOW() WHERE id = %s AND user_id = %s",
                            (json.dumps(final, ensure_ascii=False), row["id"], user_id),
                        )
                    out[kind][int(row["id"])] = {"added": added, "removed": removed, "name": row["name"]}
        except YandexError as exc:
            for row in group:
                if int(row["id"]) not in out[kind]:
                    out["errors"].append({"kind": kind, "id": row["id"], "error": exc.code, "message": exc.message or ""})
        except Exception:
            log.exception("patch negatives %s %s", kind, first["client_login"])
            for row in group:
                if int(row["id"]) not in out[kind]:
                    out["errors"].append({"kind": kind, "id": row["id"], "error": "direct", "message": ""})


def patch_negatives(user_id: int, *, campaigns: dict | None = None, adgroups: dict | None = None) -> dict:
    """
    campaigns / adgroups — {row_id: {"add": [...], "remove": [...]}}.
    → {"campaigns": {row_id: {"added", "removed", "name"}}, "adgroups": {...}, "skipped": [...], "errors": [...]}
    """
    camp_changes = _normalize(campaigns)
    group_changes = _normalize(adgroups)
    out: dict = {"campaigns": {}, "adgroups": {}, "skipped": [], "errors": []}
    if not camp_changes and not group_changes:
        return out
    with negatives_lock(user_id):
        if camp_changes:
            _patch_kind(user_id, "campaigns", camp_changes, out)
        if group_changes:
            _patch_kind(user_id, "adgroups", group_changes, out)
    return out


def inverse(result: dict) -> dict:
    """Шаг отката для patch_negatives: убрать добавленное и вернуть убранное."""
    args: dict = {}
    for kind in ("campaigns", "adgroups"):
        part = {
            str(row_id): {"add": change["removed"], "remove": change["added"]}
            for row_id, change in (result.get(kind) or {}).items()
            if change["added"] or change["removed"]
        }
        if part:
            args[kind] = part
    return args


def counts(result: dict) -> tuple[int, int]:
    added = sum(len(c["added"]) for kind in ("campaigns", "adgroups") for c in result[kind].values())
    removed = sum(len(c["removed"]) for kind in ("campaigns", "adgroups") for c in result[kind].values())
    return added, removed


def targets_of(result: dict) -> list[str]:
    return [f"campaign:{i}" for i in result["campaigns"]] + [f"adgroup:{i}" for i in result["adgroups"]]


def add_negatives_logged(
    user_id: int,
    *,
    campaigns: dict | None = None,
    adgroups: dict | None = None,
    title: str,
    payload: dict | None = None,
) -> dict:
    """patch_negatives + запись в журнал с откатом. Ошибка, если не записалось ничего."""
    result = patch_negatives(user_id, campaigns=campaigns, adgroups=adgroups)
    added, removed = counts(result)
    done = bool(result["campaigns"] or result["adgroups"])
    status = "ok" if not result["errors"] else ("partial" if done else "error")
    camp_ids = set(result["campaigns"]) | {int(k) for k in (campaigns or {})}
    if adgroups:
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT campaign_row_id FROM direct_adgroups WHERE user_id = %s AND id = ANY(%s)",
                (user_id, [int(k) for k in adgroups]),
            ).fetchall()
        camp_ids |= {int(r["campaign_row_id"]) for r in rows}
    summary = {"added": added, "removed": removed, "skipped": result["skipped"][:20], "errors": result["errors"][:20]}
    if done or result["errors"]:
        log_action(
            user_id,
            "negatives",
            {**(payload or {}), "campaign_row_ids": sorted(camp_ids)},
            summary,
            status,
            title=title,
            undo={"label": "Вернуть минус-фразы как было", "steps": [{"op": "negatives", "args": inverse(result)}]},
            targets=targets_of(result) + [f"campaign:{i}" for i in camp_ids],
        )
    if status == "error":
        first = result["errors"][0]
        raise YandexError(first["error"], first.get("message") or None)
    return {**result, "added": added, "removed": removed, "status": status}
