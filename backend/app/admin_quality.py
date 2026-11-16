"""Качество ИИ и «Проекты и Директ».

Качество: оценки ответов (ai_feedback) по дням, моделям и виду чата, частые причины «вниз»; судьба
предложений ИИ (ai_actions) — по статусам, категориям и дням. Правки доски (board_edit) — не предложения.

Проекты: сколько проектов, подключений Яндекса, аккаунтов и кампаний под управлением; изменения в Директе
из журнала действий по дням и видам; аккаунты с ошибкой доступа; проекты без кампаний.
"""

from __future__ import annotations

from datetime import date

from app import admin_dashboard as dash
from app import ai_feedback, journal
from app.clock import now_msk
from app.db import pool

ACTION_STATUSES = {
    "pending": "Ждёт решения",
    "approved": "Одобрено, выполняется",
    "done": "Выполнено",
    "rejected": "Отклонено",
    "error": "Ошибка",
}
CATEGORY_LABELS = {"": "Без категории"}
CHAT_KINDS = {"project": "Чат проекта", "general": "Общий чат"}
LIST_LIMIT = 50


def _per(p, date_from, date_to, step, now):
    now = now or now_msk()
    return dash.period(p, date_from, date_to, step, now)


def _pub(per: dict) -> dict:
    return {**dash._public_range(per), "prev": dash._public_range(per["prev"])}


def _share(part, whole) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def quality(p: str = "30d", date_from=None, date_to=None, step: str = "", now=None) -> dict:
    per = _per(p, date_from, date_to, step, now)
    step = per["step"]
    params = {"s": per["start"], "e": per["end"]}
    with pool.connection() as conn:
        fb_series = {
            r["b"]: r
            for r in conn.execute(
                f"""
                SELECT {dash._ts_bucket('created_at', step)} AS b, COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE rating < 0) AS down
                FROM ai_feedback WHERE created_at >= %(s)s AND created_at < %(e)s GROUP BY 1
                """,
                params,
            ).fetchall()
        }
        by_model = conn.execute(
            """
            SELECT COALESCE(NULLIF(model, ''), '—') AS key, COUNT(*) AS total, COUNT(*) FILTER (WHERE rating < 0) AS down
            FROM ai_feedback WHERE created_at >= %(s)s AND created_at < %(e)s GROUP BY 1 ORDER BY total DESC
            """,
            params,
        ).fetchall()
        by_kind = conn.execute(
            """
            SELECT CASE WHEN c.workspace_id IS NULL THEN 'general' ELSE 'project' END AS key,
                   COUNT(*) AS total, COUNT(*) FILTER (WHERE f.rating < 0) AS down
            FROM ai_feedback f LEFT JOIN ai_chats c ON c.id = f.chat_id
            WHERE f.created_at >= %(s)s AND f.created_at < %(e)s GROUP BY 1 ORDER BY total DESC
            """,
            params,
        ).fetchall()
        reasons = conn.execute(
            """
            SELECT r AS key, COUNT(*) AS n FROM ai_feedback f, unnest(f.reasons) r
            WHERE f.rating < 0 AND f.created_at >= %(s)s AND f.created_at < %(e)s GROUP BY 1 ORDER BY n DESC
            """,
            params,
        ).fetchall()
        comments = conn.execute(
            """
            SELECT COUNT(*) FILTER (WHERE COALESCE(comment, '') <> '') AS n FROM ai_feedback
            WHERE rating < 0 AND created_at >= %(s)s AND created_at < %(e)s
            """,
            params,
        ).fetchone()["n"]
        act_status = conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM ai_actions
            WHERE tool <> 'board_edit' AND created_at >= %(s)s AND created_at < %(e)s GROUP BY 1
            """,
            params,
        ).fetchall()
        act_cat = conn.execute(
            """
            SELECT COALESCE(category, '') AS key, COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status IN ('approved', 'done', 'error')) AS approved,
                   COUNT(*) FILTER (WHERE status = 'done') AS done,
                   COUNT(*) FILTER (WHERE status = 'rejected') AS rejected,
                   COUNT(*) FILTER (WHERE status = 'error') AS error,
                   COUNT(*) FILTER (WHERE status = 'pending') AS pending
            FROM ai_actions WHERE tool <> 'board_edit' AND created_at >= %(s)s AND created_at < %(e)s
            GROUP BY 1 ORDER BY total DESC
            """,
            params,
        ).fetchall()
        act_series = {
            r["b"]: r
            for r in conn.execute(
                f"""
                SELECT {dash._ts_bucket('created_at', step)} AS b, COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE status = 'done') AS done,
                       COUNT(*) FILTER (WHERE status = 'rejected') AS rejected,
                       COUNT(*) FILTER (WHERE status = 'error') AS error
                FROM ai_actions WHERE tool <> 'board_edit' AND created_at >= %(s)s AND created_at < %(e)s GROUP BY 1
                """,
                params,
            ).fetchall()
        }
        errors = conn.execute(
            """
            SELECT a.id, a.created_at, a.user_id, u.email, a.tool, a.summary, a.category, a.error, a.chat_id
            FROM ai_actions a LEFT JOIN users u ON u.id = a.user_id
            WHERE a.status = 'error' AND a.created_at >= %(s)s AND a.created_at < %(e)s
            ORDER BY a.created_at DESC, a.id DESC LIMIT 20
            """,
            params,
        ).fetchall()
    d1, d2 = date.fromisoformat(per["from"]), date.fromisoformat(per["to"])
    buckets = dash._buckets(d1, d2, step)
    total = sum(int(r["total"]) for r in fb_series.values())
    down = sum(int(r["down"]) for r in fb_series.values())
    statuses = {r["status"]: int(r["n"]) for r in act_status}
    proposed = sum(statuses.values())
    approved = statuses.get("approved", 0) + statuses.get("done", 0) + statuses.get("error", 0)

    def split(rows, labels=None):
        return [
            {
                "key": r["key"],
                "label": (labels or {}).get(r["key"], r["key"]),
                "total": int(r["total"]),
                "down": int(r["down"]),
                "down_share": _share(int(r["down"]), int(r["total"])),
            }
            for r in rows
        ]

    return {
        "period": _pub(per),
        "ratings": {
            "total": total,
            "down": down,
            "down_share": _share(down, total),
            "comments": int(comments),
            "series": [
                {
                    "day": b.isoformat(),
                    "total": int((fb_series.get(b) or {}).get("total") or 0),
                    "down": int((fb_series.get(b) or {}).get("down") or 0),
                    "down_share": _share(int((fb_series.get(b) or {}).get("down") or 0), int((fb_series.get(b) or {}).get("total") or 0)),
                }
                for b in buckets
            ],
            "models": split(by_model),
            "kinds": split(by_kind, CHAT_KINDS),
            "reasons": [
                {"key": r["key"], "label": ai_feedback.REASONS.get(r["key"], r["key"]), "count": int(r["n"]), "share": _share(int(r["n"]), down)}
                for r in reasons
            ],
        },
        "actions": {
            "proposed": proposed,
            "approved": approved,
            "approve_share": _share(approved, proposed - statuses.get("pending", 0)),
            "statuses": [{"key": k, "label": v, "count": statuses.get(k, 0)} for k, v in ACTION_STATUSES.items()],
            "categories": [
                {
                    "key": r["key"],
                    "label": CATEGORY_LABELS.get(r["key"], r["key"]),
                    **{k: int(r[k]) for k in ("total", "approved", "done", "rejected", "error", "pending")},
                    "done_share": _share(int(r["done"]), int(r["total"])),
                }
                for r in act_cat
            ],
            "series": [
                {"day": b.isoformat(), **{k: int((act_series.get(b) or {}).get(k) or 0) for k in ("total", "done", "rejected", "error")}}
                for b in buckets
            ],
            "errors": [
                {
                    "id": r["id"],
                    "created_at": r["created_at"].isoformat(),
                    "user_id": r["user_id"],
                    "email": r["email"] or "",
                    "tool": r["tool"],
                    "summary": r["summary"] or "",
                    "error": (r["error"] or "")[:300],
                    "chat_id": r["chat_id"],
                }
                for r in errors
            ],
        },
    }


# --- Проекты и Директ ----------------------------------------------------------------------------

SOURCE_LABELS = {"user": "Пользователь", "ai": "ИИ", "rule": "Правило"}


def _kind_of(action: str) -> str:
    for kind, actions in journal.ACTION_KINDS.items():
        if action in actions:
            return kind
    return "other"


KIND_LABELS = {"budget": "Деньги", "negatives": "Минус-слова", "state": "Состояние", "edits": "Правки", "undo": "Откаты", "other": "Другое"}


def projects(p: str = "30d", date_from=None, date_to=None, step: str = "", now=None) -> dict:
    per = _per(p, date_from, date_to, step, now)
    step = per["step"]
    params = {"s": per["start"], "e": per["end"], "lim": LIST_LIMIT}
    with pool.connection() as conn:
        counts = conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM workspaces WHERE archived_at IS NULL) AS projects,
                   (SELECT COUNT(DISTINCT user_id) FROM workspaces WHERE archived_at IS NULL) AS project_users,
                   (SELECT COUNT(*) FROM yandex_connections) AS connections,
                   (SELECT COUNT(*) FROM direct_accounts WHERE enabled) AS accounts,
                   (SELECT COUNT(*) FROM workspace_campaigns wc JOIN workspaces w ON w.id = wc.workspace_id
                     WHERE w.archived_at IS NULL) AS managed,
                   (SELECT COUNT(*) FROM direct_campaigns) AS campaigns,
                   (SELECT COUNT(*) FROM workspaces w WHERE w.archived_at IS NULL AND w.created_at >= %(s)s
                     AND w.created_at < %(e)s) AS new_projects
            """,
            params,
        ).fetchone()
        logs = conn.execute(
            f"""
            SELECT {dash._ts_bucket('created_at', step)} AS b, action, source, status, COUNT(*) AS n
            FROM action_logs WHERE created_at >= %(s)s AND created_at < %(e)s GROUP BY 1, 2, 3, 4
            """,
            params,
        ).fetchall()
        failing = conn.execute(
            """
            SELECT 'account' AS kind, a.id, a.user_id, u.email, COALESCE(NULLIF(a.name, ''), a.client_login) AS name,
                   a.last_sync_error AS error, a.last_sync_at AS at
            FROM direct_accounts a LEFT JOIN users u ON u.id = a.user_id
            WHERE a.enabled AND a.last_sync_status = 'error'
            UNION ALL
            SELECT 'connection', c.id, c.user_id, u.email, COALESCE(NULLIF(c.yandex_login, ''), c.display_name),
                   c.last_sync_error, c.last_sync_at
            FROM yandex_connections c LEFT JOIN users u ON u.id = c.user_id
            WHERE c.last_sync_status IN ('error', 'partial')
            ORDER BY at DESC NULLS LAST LIMIT %(lim)s
            """,
            params,
        ).fetchall()
        empty = conn.execute(
            """
            SELECT w.id, w.name, w.site, w.user_id, u.email, w.created_at
            FROM workspaces w LEFT JOIN users u ON u.id = w.user_id
            WHERE w.archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM workspace_campaigns wc WHERE wc.workspace_id = w.id)
            ORDER BY w.created_at DESC LIMIT %(lim)s
            """,
            params,
        ).fetchall()
        empty_total = conn.execute(
            """
            SELECT COUNT(*) AS n FROM workspaces w WHERE w.archived_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM workspace_campaigns wc WHERE wc.workspace_id = w.id)
            """
        ).fetchone()["n"]
    d1, d2 = date.fromisoformat(per["from"]), date.fromisoformat(per["to"])
    series: dict[date, dict] = {}
    kinds: dict[str, int] = {}
    actions: dict[str, dict] = {}
    sources: dict[str, int] = {}
    failed = 0
    for r in logs:
        kind = _kind_of(r["action"])
        n = int(r["n"])
        slot = series.setdefault(r["b"], {})
        slot[kind] = slot.get(kind, 0) + n
        kinds[kind] = kinds.get(kind, 0) + n
        a = actions.setdefault(r["action"], {"action": r["action"], "label": journal.ACTION_TITLES.get(r["action"], r["action"]), "count": 0, "failed": 0})
        a["count"] += n
        if r["status"] in ("error", "partial"):
            a["failed"] += n
            failed += n
        sources[r["source"] or "user"] = sources.get(r["source"] or "user", 0) + n
    kind_keys = [k for k in KIND_LABELS if k in kinds]
    return {
        "period": _pub(per),
        "counts": {k: int(counts[k]) for k in counts.keys()},
        "changes": {
            "total": sum(kinds.values()),
            "failed": failed,
            "kinds": [{"key": k, "label": KIND_LABELS[k], "count": kinds[k]} for k in kind_keys],
            "sources": [{"key": k, "label": SOURCE_LABELS.get(k, k), "count": v} for k, v in sorted(sources.items(), key=lambda x: -x[1])],
            "actions": sorted(actions.values(), key=lambda a: -a["count"]),
            "series": [{"day": b.isoformat(), **{k: (series.get(b) or {}).get(k, 0) for k in kind_keys}} for b in dash._buckets(d1, d2, step)],
        },
        "failing": [
            {
                "kind": r["kind"],
                "id": r["id"],
                "user_id": r["user_id"],
                "email": r["email"] or "",
                "name": r["name"] or "",
                "error": (r["error"] or "")[:300],
                "at": r["at"].isoformat() if r["at"] else None,
            }
            for r in failing
        ],
        "empty_projects": {
            "total": int(empty_total),
            "items": [
                {
                    "id": r["id"],
                    "name": r["name"] or "",
                    "site": r["site"] or "",
                    "user_id": r["user_id"],
                    "email": r["email"] or "",
                    "created_at": r["created_at"].isoformat(),
                }
                for r in empty
            ],
        },
    }
