"""История на настоящем Postgres: строки «было → стало», причины ошибок и ссылки на кампании в сервисе."""

import json

from app.journal import list_action_logs, log_action
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace


def _plain(lines: list[str]) -> list[str]:
    return [line.replace("\u00a0", " ") for line in lines]


def test_history_rows_in_words_with_campaign_links(db):
    user = make_user(db, "history@example.com")
    acc = make_account(db, user, make_connection(db, user))
    windows = make_campaign(db, user, acc, 101, "Окна")
    doors = make_campaign(db, user, acc, 102, "Двери")
    ws = make_workspace(db, user, 555, "Сайт окон", campaigns=(windows,))

    budget = log_action(
        user,
        "set_budget",
        {"campaign_ids": [windows, doors], "daily_budget_rub": 1500},
        {"applied": 2, "errors": []},
        "ok",
        undo={"label": "Вернуть бюджеты", "steps": [{"op": "campaign_budgets", "args": {"budgets": {str(windows): 100000, str(doors): 100000}}}]},
    )
    partial = log_action(
        user,
        "pause",
        {"campaign_ids": [windows, doors]},
        {"applied": 1, "errors": [{"campaign_row_id": doors, "error": "direct", "message": "Кампания уже остановлена"}]},
        "partial",
    )
    # Огромный ответ: раньше его резали по 4000 символов и теряли причину.
    huge = log_action(
        user,
        "pause",
        {"campaign_ids": [windows, doors]},
        {
            "applied": 1,
            "details": [{"campaign_row_id": i, "data": "x" * 300} for i in range(200)],
            "errors": [{"campaign_row_id": doors, "error": "direct", "message": "Кампания в архиве"}],
        },
        "partial",
    )
    with db.connection() as conn:
        raw = conn.execute("SELECT result FROM action_logs WHERE id = %s", (huge,)).fetchone()["result"]
    assert json.loads(raw)["errors"][0]["message"] == "Кампания в архиве"  # в базе — целый JSON

    rows = {r["id"]: r for r in list_action_logs(user)}

    row = rows[budget]
    assert _plain(row["changes"]) == ["Дневной бюджет: 1 000 ₽ → 1 500 ₽, 2 кампании"]
    assert row["problems"] == [] and row["cut"] is False
    assert row["campaigns_total"] == 2
    links = {c["id"]: c for c in row["campaigns"]}
    assert links[windows] == {"id": windows, "name": "Окна", "workspace_id": ws, "direct_id": 101}
    assert links[doors]["workspace_id"] is None  # не в проекте — без ссылки, только название

    assert rows[partial]["problems"] == ["1 из 2 не применилось: кампания уже остановлена («Двери»)"]
    assert rows[partial]["changes"] == ["Показы: идут → на паузе"]
    assert rows[huge]["problems"] == ["1 из 2 не применилось: кампания в архиве («Двери»)"]


def test_old_cut_row_from_database(db):
    user = make_user(db, "old@example.com")
    acc = make_account(db, user, make_connection(db, user))
    camp = make_campaign(db, user, acc, 201, "Кухни")
    result = {
        "applied": 0,
        "errors": [{"campaign_row_id": camp, "error": "direct", "message": "Недостаточно средств на счёте"}],
        "details": ["x" * 100 for _ in range(80)],
    }
    with db.connection() as conn:  # так писала старая версия: JSON, обрезанный посередине
        conn.execute(
            """
            INSERT INTO action_logs (user_id, action, payload, result, status, source, title, targets)
            VALUES (%s, 'pause', %s, %s, 'error', 'user', 'Остановить «Кухни»', %s)
            """,
            (user, json.dumps({"campaign_ids": [camp]}), json.dumps(result, ensure_ascii=False)[:4000], [f"campaign:{camp}"]),
        )
    row = list_action_logs(user)[0]
    assert row["cut"] is True and isinstance(row["result"], dict)
    assert row["problems"] == ["«Кухни»: недостаточно средств на счёте", "Часть подробностей в этой старой записи не сохранилась."]
    assert [c["id"] for c in row["campaigns"]] == [camp]
