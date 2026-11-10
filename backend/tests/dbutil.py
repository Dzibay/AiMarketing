"""Фабрики строк для интеграционных тестов."""


def make_user(pool, email: str = "u@example.com", yandex_uid: str = "") -> int:
    with pool.connection() as conn:
        return conn.execute(
            "INSERT INTO users (email, yandex_uid) VALUES (%s, %s) RETURNING id",
            (email, yandex_uid),
        ).fetchone()["id"]


def make_connection(pool, user_id: int, login: str = "login") -> int:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO yandex_connections (user_id, yandex_uid, yandex_login)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (user_id, f"uid-{login}", login),
        ).fetchone()["id"]


def make_account(pool, user_id: int, connection_id: int, name: str = "", login: str = "acc") -> int:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO direct_accounts (user_id, connection_id, client_login, name)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (user_id, connection_id, login, name),
        ).fetchone()["id"]


def make_counter(pool, user_id: int, connection_id: int, counter_id: int, site: str = "") -> int:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO metrika_counters (user_id, connection_id, counter_id, site)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (user_id, connection_id, counter_id, site),
        ).fetchone()["id"]


def link(pool, user_id: int, account_id: int, counter_row_id: int) -> None:
    """Связь кабинет ↔ счётчик из схемы до миграции 0006 (для теста переноса данных)."""
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO account_counter_links (user_id, account_id, counter_row_id) VALUES (%s, %s, %s)",
            (user_id, account_id, counter_row_id),
        )


def client_of(pool, table: str, row_id: int) -> int | None:
    assert table in ("direct_accounts", "metrika_counters")
    with pool.connection() as conn:
        return conn.execute(f"SELECT client_row_id FROM {table} WHERE id = %s", (row_id,)).fetchone()[
            "client_row_id"
        ]


def make_campaign(
    pool, user_id: int, account_id: int, campaign_id: int, name: str = "", state: str = "ON", status: str = "ACCEPTED"
) -> int:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO direct_campaigns (user_id, account_id, campaign_id, name, state, status)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (user_id, account_id, campaign_id, name or f"Кампания {campaign_id}", state, status),
        ).fetchone()["id"]


def make_workspace(
    pool,
    user_id: int,
    counter_id: int,
    name: str = "",
    *,
    counter_row_id: int | None = None,
    client_id: int | None = None,
    accounts=(),
    campaigns=(),
    kpi_type: str = "",
    kpi_target: float | None = None,
    monthly_budget_rub: float | None = None,
    goal_id: int | None = None,
) -> int:
    with pool.connection() as conn:
        ws = conn.execute(
            """
            INSERT INTO workspaces (
                user_id, counter_id, counter_row_id, client_row_id, name,
                kpi_type, kpi_target, monthly_budget_kopecks, goal_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                user_id,
                counter_id,
                counter_row_id,
                client_id,
                name or f"Сайт {counter_id}",
                kpi_type,
                kpi_target,
                int(monthly_budget_rub * 100) if monthly_budget_rub is not None else None,
                goal_id,
            ),
        ).fetchone()["id"]
        for account_id in accounts:
            conn.execute(
                "INSERT INTO workspace_accounts (workspace_id, account_id, user_id) VALUES (%s, %s, %s)",
                (ws, account_id, user_id),
            )
        for campaign_row_id in campaigns:
            conn.execute(
                "INSERT INTO workspace_campaigns (workspace_id, campaign_row_id, user_id) VALUES (%s, %s, %s)",
                (ws, campaign_row_id, user_id),
            )
    return ws


def admin_headers() -> dict:
    """Токен админки (вход по ADMIN_PASSWORD): админ — не пользователь кабинета."""
    from app.auth import make_token

    return {"Authorization": f"Bearer {make_token()}"}


SERVICE_TABLES = ("service_ai", "service_wordstat", "service_images", "service_bridge", "service_payments")


def reset_services(pool) -> None:
    """Общие ключи и классы моделей — как после миграции: TRUNCATE users их не трогает."""
    with pool.connection() as conn:
        for table in SERVICE_TABLES:
            conn.execute(f"DELETE FROM {table}")
            conn.execute(f"INSERT INTO {table} (id) VALUES (1)")
        conn.execute("DELETE FROM ai_model_classes")
        conn.execute(
            """
            INSERT INTO ai_model_classes (id, position, name, model, enabled) VALUES
                ('fast', 1, 'Быстрая', 'yandexgpt-lite/latest', TRUE),
                ('standard', 2, 'Стандартная', 'yandexgpt/latest', TRUE),
                ('thinking', 3, 'Думающая', '', FALSE)
            """
        )


def setup_ai(
    *,
    provider: str = "openai",
    api_key: str | None = "sk-test-1234abcd",
    model: str = "gpt-test",
    fast: str = "",
    thinking: str = "",
    folder_id: str = "",
    base_url: str = "",
    prices: dict | None = None,
) -> dict:
    """Общий ключ ИИ сервиса (админка): стандартная модель model, быстрая и думающая — если заданы.

    prices — {класс: {"units_in", "units_out", "cost_in", "cost_out"}}.
    """
    from app import ai

    classes = [
        {"id": "fast", "name": "Быстрая", "model": fast, "enabled": bool(fast)},
        {"id": "standard", "name": "Стандартная", "model": model, "enabled": bool(model)},
        {"id": "thinking", "name": "Думающая", "model": thinking, "enabled": bool(thinking)},
    ]
    for item in classes:
        item.update((prices or {}).get(item["id"], {}))
    return ai.save_service_settings(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        folder_id=folder_id,
        classes=classes,
    )


def set_units_limit(user_id: int, units) -> dict:
    """Лимит единиц пользователя поверх тарифа (как правка подписки в админке); None — без лимита."""
    from app import plans

    return plans.set_subscription(user_id, overrides={"units_limit": units})


def make_plan(**fields) -> dict:
    from app import plans

    data = {"name": "Тариф", "price_rub": 1000, "days": 30, **fields}
    return plans.save_plan(None, data)
