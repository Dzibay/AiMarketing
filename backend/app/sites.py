"""Удаление сайта из Толка и возврат (docs/cabinet-polish-plan.md, фаза 3).

Сайт — счётчик Метрики. «Удалить сайт» скрывает номер счётчика у пользователя (hidden_counters),
выключает все его строки metrika_counters (загрузчики читают только включённые), убирает из очереди
ещё не начатую загрузку и отправляет проект в архив. С purge проект удаляется совсем (каскадом —
чаты, правила, эксперименты, привязки кампаний) вместе со статистикой Метрики по счётчику; кампании
и кабинеты Директа остаются и становятся «без проекта». В Метрике и Директе ничего не меняется.

Скрытый счётчик не предлагается в «Добавить сайт» и не превращается в проект сам — ни после
синхронизации, ни через другой логин Яндекса (upsert_metrika вставляет его строки выключенными).
"""

from app import events
from app.db import pool
from app.yandex import YandexError

# Условие для запросов к metrika_counters m: счётчик не удалён из Толка.
NOT_HIDDEN = "NOT EXISTS (SELECT 1 FROM hidden_counters h WHERE h.user_id = m.user_id AND h.counter_id = m.counter_id)"


def unhide(conn, user_id: int, counter_id: int) -> dict | None:
    """Снимает скрытие в открытой транзакции: строки счётчика включаются там, где у логина роль Метрики.

    → снятая запись hidden_counters (had_project — был ли действующий проект) или None.
    """
    gone = conn.execute(
        "DELETE FROM hidden_counters WHERE user_id = %s AND counter_id = %s RETURNING counter_id, had_project",
        (user_id, counter_id),
    ).fetchone()
    if gone is None:
        return None
    conn.execute(
        """
        UPDATE metrika_counters m SET enabled = TRUE
        FROM yandex_connections y
        WHERE y.id = m.connection_id AND y.use_metrika AND m.user_id = %s AND m.counter_id = %s
        """,
        (user_id, counter_id),
    )
    return gone


def is_hidden(user_id: int, counter_id: int) -> bool:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM hidden_counters WHERE user_id = %s AND counter_id = %s", (user_id, counter_id)
        ).fetchone()
    return row is not None


def hidden_sites(user_id: int) -> list[dict]:
    """Удалённые сайты — для «Вернуть»: архивный проект ещё есть или удалён вместе с данными."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT h.counter_id, h.name, h.site, h.had_project, h.hidden_at, w.id AS workspace_id
            FROM hidden_counters h
            LEFT JOIN workspaces w ON w.user_id = h.user_id AND w.counter_id = h.counter_id
            WHERE h.user_id = %s
            ORDER BY h.hidden_at DESC, h.counter_id
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "counter_id": int(r["counter_id"]),
            "name": r["name"],
            "site": r["site"],
            "had_project": bool(r["had_project"]),
            "hidden_at": r["hidden_at"].isoformat(),
            "workspace_id": int(r["workspace_id"]) if r["workspace_id"] is not None else None,
        }
        for r in rows
    ]


def hide_site(user_id: int, counter_id: int, *, purge: bool = False) -> dict:
    """Убирает сайт из Толка: счётчик скрыт и выключен, проект в архиве (с purge — удалён с данными)."""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, name, site FROM metrika_counters WHERE user_id = %s AND counter_id = %s ORDER BY enabled DESC, id",
            (user_id, counter_id),
        ).fetchall()
        ws = conn.execute(
            "SELECT id, name, site, archived_at FROM workspaces WHERE user_id = %s AND counter_id = %s",
            (user_id, counter_id),
        ).fetchone()
        if not rows and ws is None:
            raise YandexError("not-found", "Сайт не найден")
        first = rows[0] if rows else {}
        name = (ws["name"] if ws else "") or first.get("name") or ""
        site = (ws["site"] if ws else "") or first.get("site") or ""
        row_ids = [int(r["id"]) for r in rows]
        had_project = ws is not None and ws["archived_at"] is None
        conn.execute(
            """
            INSERT INTO hidden_counters (user_id, counter_id, name, site, had_project) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, counter_id) DO UPDATE SET
                hidden_at = NOW(), name = EXCLUDED.name, site = EXCLUDED.site,
                had_project = hidden_counters.had_project OR EXCLUDED.had_project
            """,
            (user_id, counter_id, name, site, had_project),
        )
        conn.execute(
            "UPDATE metrika_counters SET enabled = FALSE WHERE user_id = %s AND counter_id = %s", (user_id, counter_id)
        )
        # Загрузка, которая уже идёт, закончится сама (выключенный счётчик она пропустит); ждущую — убираем.
        conn.execute(
            """
            DELETE FROM jobs
            WHERE user_id = %s AND kind = 'load_counter' AND status = 'queued'
              AND (payload ->> 'counter_row_id')::bigint = ANY(%s)
            """,
            (user_id, row_ids),
        )
        workspace_id = int(ws["id"]) if ws is not None else None
        if purge:
            if workspace_id is not None:
                conn.execute("DELETE FROM workspaces WHERE id = %s AND user_id = %s", (workspace_id, user_id))
            conn.execute(
                """
                DELETE FROM stats_daily
                WHERE user_id = %s AND counter_row_id = ANY(%s) AND source IN ('metrika', 'metrika_goal')
                """,
                (user_id, row_ids),
            )
            # Вернут сайт — загрузка начнётся заново, за 30 дней.
            conn.execute(
                "UPDATE metrika_counters SET load_state = '{}'::jsonb, loaded_at = NULL WHERE id = ANY(%s)", (row_ids,)
            )
        elif workspace_id is not None:
            conn.execute(
                "UPDATE workspaces SET archived_at = COALESCE(archived_at, NOW()), updated_at = NOW() WHERE id = %s",
                (workspace_id,),
            )
    events.record(user_id, "site_hidden", counter_id=counter_id, workspace_id=workspace_id, purge=purge)
    return {
        "counter_id": counter_id,
        "name": name,
        "site": site,
        "workspace_id": None if purge else workspace_id,
        "purged": purge,
    }


def restore_site(user_id: int, counter_id: int) -> dict:
    """Возвращает удалённый сайт: счётчик снова включён и предлагается в «Добавить сайт».

    Был действующий проект — он возвращается из архива (или создаётся заново, если удаляли с данными).
    Нет места в тарифе или счётчик пропал из подключений — сайт остаётся в «Добавить сайт», причина — в note.
    """
    from app.workspaces import create_project_for_counter

    with pool.connection() as conn:
        gone = unhide(conn, user_id, counter_id)
    if gone is None:
        raise YandexError("not-found", "Сайт не найден среди удалённых")
    workspace = None
    note = ""
    if gone["had_project"]:
        try:
            workspace = create_project_for_counter(user_id, counter_id)
        except YandexError as exc:
            note = exc.message or "Сайт вернулся в «Добавить сайт»"
    events.record(user_id, "site_restored", counter_id=counter_id, workspace_id=(workspace or {}).get("id"))
    return {"counter_id": counter_id, "workspace": workspace, "note": note}
