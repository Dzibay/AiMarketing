"""Статистика счётчика Метрики на настоящем Postgres: ответы Метрики подменены, SQL — настоящий."""

from datetime import date

import httpx

from app import metrika_sync
from tests.dbutil import make_connection, make_counter, make_user, make_workspace

_RealClient = httpx.Client
D1 = date(2026, 10, 24)
D2 = date(2026, 10, 25)


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        return conn.execute(query, params)


def _metrika(deleted: set[int], asked: list[str]):
    """Отчёты Метрики за D1–D2: данные только за D2; отчёт с целью из deleted — ошибка 4002 целиком."""

    def handler(request):
        params = request.url.params
        asked.append(params["metrics"])
        metrics = params["metrics"].split(",")
        if metrics == metrika_sync.DAILY_METRICS.split(","):
            data = [{"dimensions": [{"name": D2.isoformat()}], "metrics": [120, 100, 33.5, 7]}]
        else:
            goals = [int(m.removeprefix("ym:s:goal").removesuffix("reaches")) for m in metrics]
            bad = [goal_id for goal_id in goals if goal_id in deleted]
            if bad:
                message = f"Incorrectly specified metric, value: ym:s:goal{bad[0]}reaches, error code: 4002"
                return httpx.Response(400, json={"code": 400, "message": message})
            if "," in params["dimensions"]:
                data = [
                    {"dimensions": [{"name": D2.isoformat()}, {"id": "555", "name": "Бренд", "direct_id": "N-555"}],
                     "metrics": [2] * len(goals)},
                    {"dimensions": [{"name": D2.isoformat()}, {"id": None, "name": None, "direct_id": None}],
                     "metrics": [1] * len(goals)},
                ]
            else:
                data = [{"dimensions": [{"name": D2.isoformat()}], "metrics": [3] * len(goals)}]
        return httpx.Response(200, json={"data": data, "total_rows": len(data)})

    return handler


def test_counter_stats_load_without_goals_deleted_in_metrika(db, monkeypatch):
    user = make_user(db)
    conn_id = make_connection(db, user)
    counter = make_counter(db, user, conn_id, 4242)
    metrika_sync.upsert_goals(user, counter, [{"goal_id": 10, "name": "Заказ"}, {"goal_id": 30, "name": "Звонок"}])
    # Цель 20 при последней загрузке целей Метрика уже не отдала — synced_at у неё старый.
    _sql(
        db,
        """
        INSERT INTO metrika_goals (user_id, counter_row_id, goal_id, name, synced_at)
        VALUES (%s, %s, 20, 'Удалённая', NOW() - INTERVAL '1 day')
        """,
        (user, counter),
    )
    make_workspace(db, user, 4242, counter_row_id=counter, goal_id=20)
    asked: list[str] = []
    monkeypatch.setattr(metrika_sync, "get_connection_access_token", lambda connection_id, user_id: "token")
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(_metrika({30}, asked)), **kw)
    )

    # Цель 30 удалили уже после загрузки целей: Метрика её отклоняет — пропускаем, остальное грузим.
    result = metrika_sync.sync_counter_stats_range(user, counter, D1, D2)

    assert result == {"days": 2, "goals": 1, "skipped_goals": [30]}
    assert not any("goal20reaches" in metrics for metrics in asked)
    daily = _sql(
        db,
        "SELECT day, visits, goal_reaches FROM stats_daily WHERE counter_row_id = %s AND source = 'metrika' ORDER BY day",
        (counter,),
    ).fetchall()
    assert [(r["day"], r["visits"], r["goal_reaches"]) for r in daily] == [(D1, 0, 0), (D2, 120, 7)]
    goals = _sql(
        db,
        """
        SELECT day, goal_id, campaign_id, goal_reaches FROM stats_daily
        WHERE counter_row_id = %s AND source = 'metrika_goal'
        ORDER BY day, goal_id, campaign_id NULLS FIRST
        """,
        (counter,),
    ).fetchall()
    assert [(r["day"], r["goal_id"], r["campaign_id"], r["goal_reaches"]) for r in goals] == [
        (D1, 10, None, 0),
        (D2, 10, None, 3),
        (D2, 10, 555, 2),
    ]
