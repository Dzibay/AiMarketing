"""Откат действий из журнала.

Каждая запись журнала может хранить шаги отката — обратные вызовы с прежними значениями.
Откат выполняется один раз: запись атомарно помечается, шаги идут без своих записей в журнале,
а итог пишется одной записью «Откат: …» со ссылкой на исходное действие.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

from app.db import pool
from app.journal import journal_quiet, log_action
from app.yandex import YandexError

log = logging.getLogger("app.undo")

Progress = Callable[[int, int, str], None]

UNDOABLE_STATUSES = ("ok", "partial")

_CAMPAIGN_VERBS = {
    "pause": "Остановить",
    "resume": "Запустить",
    "archive": "Архивировать",
    "unarchive": "Вернуть из архива",
    "delete": "Удалить",
}
_ITEM_VERBS = {"suspend": "Остановить", "resume": "Запустить", "delete": "Удалить", "archive": "Архивировать", "unarchive": "Вернуть из архива"}


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _existing(user_id: int, table: str, ids) -> list[int]:
    """Объекты, которые ещё есть: удалённое после действия пропускаем, а не роняем весь откат."""
    assert table in ("direct_keywords", "direct_ads", "direct_adgroups", "direct_campaigns")
    ids = [int(i) for i in ids or []]
    if not ids:
        return []
    with pool.connection() as conn:
        rows = conn.execute(f"SELECT id FROM {table} WHERE user_id = %s AND id = ANY(%s)", (user_id, ids)).fetchall()
    found = {int(r["id"]) for r in rows}
    return [i for i in ids if i in found]


# --- Шаги ----------------------------------------------------------------------------------------


def _campaigns_action(user_id: int, args: dict) -> dict:
    from app.actions import BULK_LIMIT, bulk_campaign_action

    ids = _existing(user_id, "direct_campaigns", args.get("campaign_ids"))
    applied = 0
    for chunk in _chunks(ids, BULK_LIMIT):
        applied += int(bulk_campaign_action(user_id, campaign_ids=chunk, action=str(args["action"])).get("applied") or 0)
    return {"applied": applied, "missing": len(args.get("campaign_ids") or []) - len(ids)}


def _campaign_budgets(user_id: int, args: dict) -> dict:
    from app.actions import set_campaign_budgets

    return set_campaign_budgets(user_id, args.get("budgets") or {})


def _campaign_update(user_id: int, args: dict) -> dict:
    from app.actions import update_campaign

    return update_campaign(user_id, int(args["campaign_row_id"]), name=args.get("name"))


def _campaign_settings(user_id: int, args: dict) -> dict:
    from app.actions import update_campaign_settings

    return update_campaign_settings(user_id, int(args["campaign_row_id"]), args.get("changes") or {})


def _negatives(user_id: int, args: dict) -> dict:
    from app.negatives import counts, patch_negatives

    result = patch_negatives(user_id, campaigns=args.get("campaigns"), adgroups=args.get("adgroups"))
    if result["errors"] and not (result["campaigns"] or result["adgroups"]):
        first = result["errors"][0]
        raise YandexError(first["error"], first.get("message") or None)
    added, removed = counts(result)
    return {"added": added, "removed": removed, "skipped": len(result["skipped"]), "errors": len(result["errors"])}


def _keywords_action(user_id: int, args: dict) -> dict:
    from app.keywords import bulk_keyword_action

    ids = _existing(user_id, "direct_keywords", args.get("keyword_row_ids"))
    applied = 0
    for chunk in _chunks(ids, 100):
        applied += int(bulk_keyword_action(user_id, keyword_row_ids=chunk, action=str(args["action"])).get("applied") or 0)
    return {"applied": applied, "missing": len(args.get("keyword_row_ids") or []) - len(ids)}


def _keyword_bids(user_id: int, args: dict) -> dict:
    """Прежние ставки — точно в микроединицах, без пересчёта через рубли."""
    from app.keywords import _apply_group, _keyword_row

    bids = {int(k): int(v) for k, v in (args.get("bids") or {}).items() if v}
    ids = _existing(user_id, "direct_keywords", list(bids))
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for rid in ids:
        row = _keyword_row(user_id, rid)
        groups[(row["client_login"], bids[rid])].append(row)
    applied = 0
    for (client_login, micros), rows in groups.items():
        for chunk in _chunks(rows, 100):
            ok, _error = _apply_group(user_id, client_login, chunk, "set_bid", micros)
            applied += len(ok)
    return {"applied": applied, "missing": len(bids) - len(ids)}


def _keyword_update(user_id: int, args: dict) -> dict:
    from app.keywords import update_keyword

    return update_keyword(
        user_id, int(args["keyword_row_id"]), keyword=args.get("keyword"), bid_rub=args.get("bid_rub")
    )


def _keywords_recreate(user_id: int, args: dict) -> dict:
    from app.keywords import create_keywords

    groups: dict[tuple[int, int | None], list[str]] = defaultdict(list)
    for item in args.get("items") or []:
        if item.get("keyword") and item.get("adgroup_row_id"):
            groups[(int(item["adgroup_row_id"]), item.get("bid_micros"))].append(str(item["keyword"]))
    alive = set(_existing(user_id, "direct_adgroups", [g for g, _b in groups]))
    created = errors = 0
    first_error: YandexError | None = None
    for (group_id, micros), phrases in groups.items():
        if group_id not in alive:
            errors += len(phrases)
            continue
        for chunk in _chunks(phrases, 100):
            try:
                res = create_keywords(
                    user_id, adgroup_row_id=group_id, phrases=chunk, bid_rub=int(micros) / 1_000_000 if micros else None
                )
            except YandexError as exc:
                first_error = first_error or exc
                errors += len(chunk)
                continue
            created += int(res.get("created") or 0)
            errors += int(res.get("errors") or 0)
    if errors and not created:
        raise first_error or YandexError("not-found", "Группы этих фраз уже удалены")
    return {"created": created, "errors": errors}


def _ads_action(user_id: int, args: dict) -> dict:
    from app.ads import bulk_ad_action

    ids = _existing(user_id, "direct_ads", args.get("ad_row_ids"))
    applied = 0
    for chunk in _chunks(ids, 100):
        applied += int(bulk_ad_action(user_id, ad_row_ids=chunk, action=str(args["action"])).get("applied") or 0)
    return {"applied": applied, "missing": len(args.get("ad_row_ids") or []) - len(ids)}


def _ad_update(user_id: int, args: dict) -> dict:
    from app.ads import update_text_ad

    fields = {k: args[k] for k in ("title", "title2", "text", "href", "titles", "texts") if k in args}
    return update_text_ad(user_id, int(args["ad_row_id"]), **fields)


def _adgroup_update(user_id: int, args: dict) -> dict:
    from app.adgroups import update_adgroup

    return update_adgroup(user_id, int(args["adgroup_row_id"]), name=args.get("name"))


def _adgroups_delete(user_id: int, args: dict) -> dict:
    from app.adgroups import delete_adgroups

    ids = _existing(user_id, "direct_adgroups", args.get("adgroup_row_ids"))
    if not ids:
        return {"applied": 0, "missing": len(args.get("adgroup_row_ids") or [])}
    return delete_adgroups(user_id, ids)


def _query_labels_restore(user_id: int, args: dict) -> dict:
    from app.query_cleaning import restore_labels
    from app.workspaces import require_workspace

    workspace_id = int(args["workspace_id"])
    require_workspace(user_id, workspace_id)
    return {"restored": restore_labels(user_id, workspace_id, args.get("labels") or [])}


OPS: dict[str, Callable[[int, dict], dict]] = {
    "campaigns_action": _campaigns_action,
    "campaign_budgets": _campaign_budgets,
    "campaign_update": _campaign_update,
    "campaign_settings": _campaign_settings,
    "negatives": _negatives,
    "keywords_action": _keywords_action,
    "keyword_bids": _keyword_bids,
    "keyword_update": _keyword_update,
    "keywords_recreate": _keywords_recreate,
    "ads_action": _ads_action,
    "ad_update": _ad_update,
    "adgroup_update": _adgroup_update,
    "adgroups_delete": _adgroups_delete,
    "query_labels_restore": _query_labels_restore,
}


# --- Описание для подтверждения -----------------------------------------------------------------


def _names(user_id: int, table: str, column: str, ids) -> str:
    ids = [int(i) for i in ids or []]
    if not ids:
        return ""
    with pool.connection() as conn:
        rows = conn.execute(
            f"SELECT {column} AS name FROM {table} WHERE user_id = %s AND id = ANY(%s) ORDER BY id LIMIT 3",
            (user_id, ids),
        ).fetchall()
    names = ", ".join(f"«{r['name']}»" for r in rows if r["name"])
    rest = len(ids) - len(rows)
    return names + (f" и ещё {rest}" if rest > 0 and names else ("" if names else f"{len(ids)} шт."))


def _phrases(items: list[str], limit: int = 5) -> str:
    shown = ", ".join(f"«{p}»" for p in items[:limit])
    return shown + (f" и ещё {len(items) - limit}" if len(items) > limit else "")


def _where(args: dict) -> str:
    """«в 4 кампаниях и 1 группе» — где поменяются минус-фразы."""
    parts = []
    for key, one, many in (("campaigns", "кампании", "кампаниях"), ("adgroups", "группе", "группах")):
        n = len(args.get(key) or {})
        if n:
            parts.append(f"{n} {one if n % 10 == 1 and n % 100 != 11 else many}")
    return "в " + " и ".join(parts) if parts else ""


def _rub(kopecks) -> str:
    return f"{int(kopecks) / 100:g} ₽" if kopecks else "без ограничения"


def describe_step(user_id: int, step: dict) -> str:
    op = step.get("op")
    a = step.get("args") or {}
    if op == "campaigns_action":
        verb = _CAMPAIGN_VERBS.get(a.get("action"), a.get("action"))
        return f"{verb} кампании: {_names(user_id, 'direct_campaigns', 'name', a.get('campaign_ids'))}"
    if op == "campaign_budgets":
        budgets = a.get("budgets") or {}
        if len(budgets) == 1:
            (rid, value), = budgets.items()
            return f"Вернуть дневной бюджет {_names(user_id, 'direct_campaigns', 'name', [rid])}: {_rub(value)}"
        return f"Вернуть прежние дневные бюджеты: {_names(user_id, 'direct_campaigns', 'name', list(budgets))}"
    if op == "campaign_update":
        return f"Вернуть название кампании: «{a.get('name')}»"
    if op == "campaign_settings":
        fields = ", ".join(sorted(a.get("changes") or {}))
        return f"Вернуть настройки {_names(user_id, 'direct_campaigns', 'name', [a.get('campaign_row_id')])}: {fields}"
    if op == "negatives":
        remove = [p for part in ("campaigns", "adgroups") for c in (a.get(part) or {}).values() for p in c.get("remove") or []]
        add = [p for part in ("campaigns", "adgroups") for c in (a.get(part) or {}).values() for p in c.get("add") or []]
        parts = []
        if remove:
            parts.append(f"убрать {_phrases(list(dict.fromkeys(remove)))}")
        if add:
            parts.append(f"вернуть {_phrases(list(dict.fromkeys(add)))}")
        where = _where(a)
        return f"Минус-фразы{' ' + where if where else ''}: " + "; ".join(parts)
    if op == "keywords_action":
        verb = _ITEM_VERBS.get(a.get("action"), a.get("action"))
        return f"{verb} ключевые фразы: {_names(user_id, 'direct_keywords', 'keyword', a.get('keyword_row_ids'))}"
    if op == "keyword_bids":
        return f"Вернуть прежние ставки: {_names(user_id, 'direct_keywords', 'keyword', list(a.get('bids') or {}))}"
    if op == "keyword_update":
        parts = []
        if a.get("keyword"):
            parts.append(f"текст «{a['keyword']}»")
        if a.get("bid_rub"):
            parts.append(f"ставку {float(a['bid_rub']):g} ₽")
        return "Вернуть фразе " + ", ".join(parts)
    if op == "keywords_recreate":
        items = [str(i.get("keyword") or "") for i in a.get("items") or []]
        return f"Добавить фразы заново: {_phrases(items)}"
    if op == "ads_action":
        verb = _ITEM_VERBS.get(a.get("action"), a.get("action"))
        return f"{verb} объявления: {_names(user_id, 'direct_ads', 'title', a.get('ad_row_ids'))}"
    if op == "ad_update":
        names = {"title": "заголовок", "title2": "доп. заголовок", "text": "текст", "href": "ссылку",
                 "titles": "заголовки", "texts": "тексты"}
        return "Вернуть объявлению " + ", ".join(v for k, v in names.items() if k in a)
    if op == "adgroup_update":
        return f"Вернуть название группы: «{a.get('name')}»"
    if op == "adgroups_delete":
        return f"Удалить группы: {_names(user_id, 'direct_adgroups', 'name', a.get('adgroup_row_ids'))}"
    if op == "query_labels_restore":
        return f"Вернуть разметку запросов: {len(a.get('labels') or [])}"
    return str(op)


# --- Предпросмотр и запуск ----------------------------------------------------------------------


def _load(user_id: int, log_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, action, title, status, undo, undone_at, undone_by, workspace_id, targets, created_at
            FROM action_logs WHERE id = %s AND user_id = %s
            """,
            (log_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return row


def _why_not(row: dict) -> str:
    if row.get("undone_at"):
        return "Это действие уже откатили"
    if not row.get("undo") or not (row["undo"] or {}).get("steps"):
        return "У этого действия нет отката"
    if row["status"] not in UNDOABLE_STATUSES:
        return "Действие не выполнилось — откатывать нечего"
    return ""


def _later_changes(user_id: int, row: dict) -> list[dict]:
    targets = list(row.get("targets") or [])
    if not targets:
        return []
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, title, created_at, source FROM action_logs
            WHERE user_id = %s AND id > %s AND targets && %s
              AND undone_at IS NULL AND undo_of IS NULL AND status IN ('ok', 'partial')
            ORDER BY id LIMIT 5
            """,
            (user_id, row["id"], targets),
        ).fetchall()
    return [
        {"id": r["id"], "title": r["title"], "source": r["source"], "created_at": r["created_at"].isoformat()}
        for r in rows
    ]


def undo_preview(user_id: int, log_id: int) -> dict:
    """Что сделает откат: шаги словами и предупреждения — для окна подтверждения."""
    row = _load(user_id, log_id)
    reason = _why_not(row)
    undo = row.get("undo") or {}
    steps = [s for s in undo.get("steps") or [] if isinstance(s, dict)]
    warnings: list[str] = []
    later = _later_changes(user_id, row) if not reason else []
    if later:
        warnings.append("После этого действия те же объекты меняли ещё раз — откат вернёт значения на момент действия.")
    if row["status"] == "partial":
        warnings.append("Действие выполнилось частично — откатится выполненная часть.")
    if any(s.get("op") == "keywords_recreate" for s in steps):
        warnings.append("Удалённые фразы добавятся заново, но без прежней статистики.")
    if any(s.get("op") == "campaigns_action" and (s.get("args") or {}).get("action") == "delete" for s in steps):
        warnings.append("Кампания будет удалена в Директе.")
    return {
        "log_id": row["id"],
        "title": row["title"],
        "label": undo.get("label") or "Откатить",
        "danger": bool(undo.get("danger")),
        "available": not reason,
        "reason": reason,
        "steps": [describe_step(user_id, s) for s in steps] if not reason else [],
        "warnings": warnings,
        "later": later,
    }


def _claim(user_id: int, log_id: int) -> dict:
    """Атомарно помечаем запись откаченной: два клика подряд не запустят откат дважды."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            UPDATE action_logs SET undone_at = NOW()
            WHERE id = %s AND user_id = %s AND undone_at IS NULL AND undo IS NOT NULL AND status IN ('ok', 'partial')
            RETURNING id, title, status, undo, workspace_id, targets
            """,
            (log_id, user_id),
        ).fetchone()
    if row is None:
        reason = _why_not(_load(user_id, log_id)) or "Откат уже выполняется"
        raise YandexError("undo", reason)
    return row


def _release(log_id: int) -> None:
    with pool.connection() as conn:
        conn.execute("UPDATE action_logs SET undone_at = NULL, undone_by = NULL WHERE id = %s", (log_id,))


def run_undo(user_id: int, log_id: int, progress: Progress | None = None) -> dict:
    progress = progress or (lambda *_a: None)
    row = _claim(user_id, log_id)
    steps = [s for s in (row["undo"] or {}).get("steps") or [] if isinstance(s, dict)]
    results: list[dict] = []
    try:
        with journal_quiet():
            for n, step in enumerate(steps):
                text = describe_step(user_id, step)
                progress(n, len(steps), text[:200])
                fn = OPS.get(str(step.get("op")))
                if fn is None:
                    results.append({"op": step.get("op"), "text": text, "status": "error", "message": "Неизвестный шаг"})
                    continue
                try:
                    res = fn(user_id, step.get("args") or {})
                    results.append({"op": step["op"], "text": text, "status": "ok", "result": res})
                except YandexError as exc:
                    log.info("undo %s step %s: %s", log_id, step.get("op"), exc.code)
                    results.append(
                        {"op": step["op"], "text": text, "status": "error", "code": exc.code, "message": exc.message or ""}
                    )
    except Exception:
        _release(log_id)
        raise
    ok = sum(1 for r in results if r["status"] == "ok")
    status = "ok" if ok == len(results) else ("partial" if ok else "error")
    campaign_ids = sorted({int(t.split(":", 1)[1]) for t in row["targets"] or [] if t.startswith("campaign:")})
    summary = {"steps": [{k: v for k, v in r.items() if k != "result"} for r in results]}
    new_id = log_action(
        user_id,
        "undo",
        {"log_id": log_id, "campaign_row_ids": campaign_ids},
        summary,
        status,
        workspace_id=row["workspace_id"],
        title=f"Откат: {row['title']}",
        targets=list(row["targets"] or []),
        undo_of=log_id,
    )
    if status == "error":
        # Ничего не откатилось — запись снова можно откатить.
        _release(log_id)
        first = next(r for r in results if r["status"] == "error")
        raise YandexError(first.get("code") or "undo", first.get("message") or "Не удалось откатить")
    with pool.connection() as conn:
        conn.execute("UPDATE action_logs SET undone_by = %s WHERE id = %s", (new_id, log_id))
    progress(len(steps), len(steps), "Готово")
    return {"status": status, "log_id": new_id, "undone": log_id, "steps": summary["steps"]}
