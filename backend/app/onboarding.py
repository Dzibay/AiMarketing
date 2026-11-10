"""Мастер первого входа и шаги «что ещё настроить» — по настоящим данным.

Факты собираются одним запросом из уже существующих таблиц (подключения, кабинеты, проекты,
Telegram, чаты ИИ); шаги из них строят чистые функции build_wizard и build_onboarding — их и тестируем.
Мастер: почта → Яндекс → проекты → загрузка → первый вопрос ИИ. Пока он не пройден и не скрыт,
Сводка ведёт на него; после — остаются необязательные шаги (кампании, шаблон ссылки, цели, Telegram).
"""

from __future__ import annotations

from app.db import pool
from app.telegram_notify import telegram_configured

_FACTS_SQL = """
SELECT
    u.onboarding_dismissed_at IS NOT NULL AS dismissed,
    u.telegram_chat_id <> '' AS telegram_linked,
    u.email_verified_at IS NOT NULL AS email_verified,
    (SELECT COUNT(*) FROM yandex_connections y WHERE y.user_id = u.id AND y.use_metrika) AS metrika_connections,
    EXISTS (SELECT 1 FROM ai_messages m JOIN ai_chats c ON c.id = m.chat_id
            WHERE c.user_id = u.id AND m.role = 'user') AS asked_ai,
    (SELECT COUNT(*) FROM yandex_connections y WHERE y.user_id = u.id AND y.use_direct) AS direct_connections,
    (SELECT COUNT(*) FROM direct_accounts a WHERE a.user_id = u.id AND a.enabled) AS direct_accounts,
    (SELECT COUNT(*) FROM direct_accounts a
        WHERE a.user_id = u.id AND a.enabled AND a.loaded_at IS NOT NULL) AS direct_loaded,
    (SELECT COUNT(*) FROM direct_accounts a
        WHERE a.user_id = u.id AND a.enabled AND a.loaded_at IS NULL
          AND jsonb_path_exists(a.load_state, '$.* ? (@.status == "error")')) AS load_errors
FROM users u
WHERE u.id = %s
"""

_WORKSPACES_SQL = """
SELECT
    w.id,
    w.name,
    (SELECT COUNT(*) FROM workspace_campaigns wc WHERE wc.workspace_id = w.id) AS campaigns,
    w.url_template <> '' AS has_template,
    w.goal_id IS NOT NULL AS has_goal
FROM workspaces w
WHERE w.user_id = %s AND w.archived_at IS NULL
ORDER BY w.id
"""


def load_facts(user_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(_FACTS_SQL, (user_id,)).fetchone() or {}
        workspaces = conn.execute(_WORKSPACES_SQL, (user_id,)).fetchall()
    facts = {
        "dismissed": bool(row.get("dismissed")),
        "telegram_linked": bool(row.get("telegram_linked")),
        "email_verified": bool(row.get("email_verified")),
        "asked_ai": bool(row.get("asked_ai")),
    }
    for key in ("metrika_connections", "direct_connections", "direct_accounts", "direct_loaded", "load_errors"):
        facts[key] = int(row.get(key) or 0)
    facts["workspaces"] = [
        {
            "id": int(w["id"]),
            "name": w["name"],
            "campaigns": int(w["campaigns"]),
            "has_template": bool(w["has_template"]),
            "has_goal": bool(w["has_goal"]),
        }
        for w in workspaces
    ]
    return facts


def _pick(workspaces: list[dict], missing) -> dict | None:
    """Куда вести: проект с кампаниями, где шага нет, иначе любое без шага, иначе первое."""
    with_campaigns = [w for w in workspaces if w["campaigns"] and missing(w)]
    without = [w for w in workspaces if missing(w)]
    return (with_campaigns or without or workspaces or [None])[0]


def _step(step_id: str, title: str, hint: str, done: bool, to: str | None, **extra) -> dict:
    return {
        "id": step_id,
        "title": title,
        "hint": hint,
        "done": done,
        "status": "done" if done else extra.pop("status", "todo"),
        "detail": extra.pop("detail", ""),
        "optional": extra.pop("optional", False),
        "to": to,
    }


def build_wizard(facts: dict) -> dict:
    """Пять шагов мастера первого входа. current — первый несделанный шаг (None — мастер пройден)."""
    workspaces = facts.get("workspaces") or []
    metrika, direct = facts.get("metrika_connections", 0) > 0, facts["direct_connections"] > 0
    accounts, loaded = facts["direct_accounts"], facts["direct_loaded"]
    steps = []

    # Без подтверждённой почты кабинет закрыт; шаг остаётся в списке, чтобы путь был виден целиком.
    steps.append(_step("email", "Почта", "Подтверждение почты — вход и восстановление пароля", facts.get("email_verified", True), None))

    missing = [name for name, ok in (("Метрику", metrika), ("Директ", direct)) if not ok]
    steps.append(
        _step(
            "yandex",
            "Яндекс",
            "Метрика — счётчики сайтов, Директ — кабинеты с кампаниями. Можно одним логином или разными",
            not missing,
            "/cabinet/connections",
            **({"status": "progress", "detail": f"Осталось подключить {missing[0]}"} if len(missing) == 1 else {}),
        )
    )

    steps.append(
        _step(
            "projects",
            "Проекты",
            "Каждый счётчик Метрики — проект сайта. Выберите, какие сайты вести, в пределах тарифа",
            bool(workspaces),
            "/cabinet/connections",
            **({} if metrika else {"status": "blocked", "detail": "Сначала подключите Метрику"}),
        )
    )

    if not direct:
        load = {"status": "blocked", "detail": "Сначала подключите Директ"}
    elif not accounts:
        load = {"status": "progress", "detail": "Ищем кабинеты подключённого логина"}
    elif facts["load_errors"] and not loaded:
        load = {"status": "error", "detail": "Загрузка остановилась с ошибкой — подробности в подключениях"}
    else:
        load = {"status": "progress", "detail": f"Загружено кабинетов: {loaded} из {accounts}"}
    steps.append(
        _step(
            "load",
            "Загрузка",
            "Кампании, группы и объявления загружаются один раз целиком — обычно несколько минут. Страницу можно закрыть",
            loaded > 0,
            "/cabinet/connections",
            **load,
        )
    )

    steps.append(
        _step(
            "ai",
            "Первый вопрос ИИ",
            "ИИ-помощник видит все проекты: спросите, с чего начать. Правки в Директе — только после вашего «да»",
            facts.get("asked_ai", False),
            "/cabinet/chat",
            **({} if workspaces else {"status": "blocked", "detail": "Сначала выберите проекты"}),
        )
    )

    current = next((s["id"] for s in steps if not s["done"]), None)
    return {
        "steps": steps,
        "current": current,
        "complete": current is None,
        "done": sum(s["done"] for s in steps),
        "total": len(steps),
    }


def build_onboarding(facts: dict, *, telegram_available: bool) -> dict:
    workspaces = facts.get("workspaces") or []
    has_ws = bool(workspaces)
    steps = []

    connected = bool(facts["direct_connections"] or facts["direct_accounts"])
    steps.append(
        _step(
            "direct",
            "Подключить кабинет Директа",
            "Войдите через Яндекс логином, у которого есть доступ к кабинетам клиентов",
            connected,
            "/cabinet/connections",
        )
    )

    accounts, loaded = facts["direct_accounts"], facts["direct_loaded"]
    if not connected:
        load = {"status": "blocked", "detail": "Сначала подключите Директ"}
    elif not accounts:
        load = {"status": "progress", "detail": "Ищем кабинеты подключённого логина"}
    elif facts["load_errors"] and not loaded:
        load = {"status": "error", "detail": "Загрузка остановилась с ошибкой — подробности в подключениях"}
    else:
        load = {"status": "progress", "detail": f"Загружено кабинетов: {loaded} из {accounts}"}
    steps.append(
        _step(
            "first_load",
            "Дождаться первой загрузки",
            "Кампании, группы и объявления загружаются один раз целиком — обычно несколько минут",
            loaded > 0,
            "/cabinet/connections",
            **load,
        )
    )

    steps.append(
        _step(
            "workspace",
            "Создать проект для сайта клиента",
            "Подключите Метрику: каждый счётчик сайта станет проектом со своими кампаниями и KPI",
            has_ws,
            "/cabinet/connections",
        )
    )

    blocked = {"status": "blocked", "detail": "Сначала создайте проект"}

    target = _pick(workspaces, lambda w: not w["campaigns"])
    steps.append(
        _step(
            "campaigns",
            "Добавить кампании в проект",
            "Настройки проекта → кабинеты и кампании этого сайта",
            any(w["campaigns"] for w in workspaces),
            f"/w/{target['id']}/settings" if target else None,
            **({} if has_ws else blocked),
        )
    )

    target = _pick(workspaces, lambda w: not w["has_template"])
    steps.append(
        _step(
            "link_template",
            "Задать шаблон ссылки",
            "Эталонная ссылка сайта с utm-метками — по ней проверяем ссылки объявлений",
            any(w["has_template"] for w in workspaces),
            f"/w/{target['id']}/settings#links" if target else None,
            **({} if has_ws else blocked),
        )
    )

    target = _pick(workspaces, lambda w: not w["has_goal"])
    steps.append(
        _step(
            "goals",
            "Подключить цели Метрики",
            "Выберите основную цель — по ней считаются конверсии, CPA и ДРР",
            any(w["has_goal"] for w in workspaces),
            f"/w/{target['id']}/settings" if target else None,
            **({} if has_ws else blocked),
        )
    )

    # Бот не настроен на сервере — шаг не выполнить, поэтому и не показываем.
    if telegram_available:
        steps.append(
            _step(
                "telegram",
                "Подключить Telegram",
                "Утренний дайджест и тревоги по балансу, KPI и модерации — в личку",
                facts["telegram_linked"],
                "/cabinet/settings/notifications#telegram",
            )
        )

    required = [s for s in steps if not s["optional"]]
    complete = all(s["done"] for s in required)
    wizard = build_wizard(facts)
    return {
        "steps": steps,
        "done": sum(s["done"] for s in steps),
        "total": len(steps),
        "complete": complete,
        "dismissed": facts["dismissed"],
        "visible": not complete and not facts["dismissed"],
        "wizard": wizard,
        # Первый вход: проектов ещё нет — Сводка сразу ведёт в мастер (если его не скрыли).
        "start_wizard": not facts["dismissed"] and not wizard["complete"] and not workspaces,
    }


def get_onboarding(user_id: int) -> dict:
    return build_onboarding(load_facts(user_id), telegram_available=telegram_configured())


def set_onboarding_dismissed(user_id: int, dismissed: bool = True) -> dict:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE users SET onboarding_dismissed_at = CASE WHEN %s THEN NOW() END WHERE id = %s",
            (dismissed, user_id),
        )
    return get_onboarding(user_id)
