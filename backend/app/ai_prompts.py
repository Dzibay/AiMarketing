"""Готовые задачи ИИ: встроенный набор по категориям и свои задачи пользователя.

Один источник для стартового экрана чата, кнопки «Задачи» и «/» в поле ввода, быстрых вопросов
на главной и в списке кампаний. Место задачи (places):
  general — общий чат по всем проектам; project — чат проекта с кампаниями; first — проект без кампаний;
  campaigns — вопрос про все кампании списка; campaigns_picked — про отмеченные галочкой.
home — кому (ролям) задача стоит быстрым вопросом на главной (общей или проекта); icon — значок @lucide/vue.
roles — кому задача видна вообще (по умолчанию всем). Роль — users.ui.role: owner — владелец бизнеса,
pro — специалист по рекламе; фильтрует клиент, роль без ответа — как у владельца (простой режим по умолчанию).
key — ещё и адрес «?ask=…» из других разделов: чат берёт текст отсюда.
Свои задачи — таблица ai_prompts, видны во всех местах чата.
"""

from __future__ import annotations

from app.db import pool
from app.yandex import YandexError

CATEGORIES = [
    {"key": "review", "label": "Разбор"},
    {"key": "save", "label": "Экономия"},
    {"key": "growth", "label": "Рост"},
    {"key": "ads", "label": "Объявления"},
    {"key": "report", "label": "Отчёт"},
]

NEW_CAMPAIGN = "Хочу собрать новую кампанию. Задай вопросы, которых не хватает, и веди меня по этапам"

BUILTIN: list[dict] = [
    # --- Чат проекта с кампаниями ---
    {
        "key": "status", "category": "report", "places": ["project"], "roles": ["owner"], "home": ["owner"],
        "icon": "MessageCircle",
        "title": "Как дела у рекламы",
        "text": "Расскажи простыми словами, как идёт реклама проекта последние 7 дней: сколько потрачено, сколько "
                "заявок и почём, что идёт хорошо и что плохо. Что сделать в первую очередь?",
    },
    {
        "key": "more_leads", "category": "growth", "places": ["project"], "roles": ["owner"], "home": ["owner"],
        "icon": "TrendingUp",
        "title": "Как получить больше заявок",
        "text": "Как получить больше заявок за те же деньги? Посмотри, что в проекте уже приносит заявки, и предложи, "
                "что повторить или усилить. Объясни простыми словами.",
    },
    {
        "key": "problems", "category": "review", "places": ["project"], "home": ["owner", "pro"], "icon": "Sparkles",
        "title": "Что горит",
        "text": "Разбери проблемы проекта из сводки: где сливается бюджет, что с KPI и темпом расхода. "
                "Дай конкретные действия по важности.",
    },
    {
        "key": "audit", "category": "review", "places": ["project"], "home": ["pro"], "icon": "ListChecks",
        "title": "Аудит кампаний",
        "text": "Проведи аудит всех кампаний проекта: структура, стратегии и бюджеты, минус-фразы, объявления, "
                "поисковые запросы, расход без конверсий. Дай список проблем по важности и план правок.",
    },
    {
        "key": "waste", "category": "save", "places": ["project"], "home": ["owner"], "icon": "SearchCheck",
        "title": "Где сливается бюджет?",
        "text": "Где сливается бюджет? Найди кампании и фразы с расходом без конверсий",
    },
    {
        "key": "negatives", "category": "save", "places": ["project"],
        "title": "Минус-фразы из запросов",
        "text": "Почисти поисковые запросы: предложи минус-фразы",
    },
    {
        "key": "experiment", "category": "growth", "places": ["project"], "home": ["pro"], "icon": "FlaskConical",
        "title": "Предложить эксперимент",
        "text": "Предложи эксперимент: какую гипотезу стоит проверить в этом проекте — перезапуск копией, новое гео, "
                "другие ключи или тип кампании, — на каких кампаниях и почему. Опирайся на то, что уже работает, "
                "и на прошлые эксперименты.",
    },
    {
        "key": "new_campaign", "category": "growth", "places": ["project", "first"],
        "title": "Новая кампания",
        "text": NEW_CAMPAIGN,
    },
    {
        "key": "ads_check", "category": "ads", "places": ["project"],
        "title": "Проверить объявления",
        "text": "Проверь объявления проекта: где низкий CTR, слабые заголовки и тексты, что переписать. "
                "Предложи новые варианты на согласование.",
    },
    {
        "key": "links", "category": "ads", "places": ["project"], "home": ["pro"], "icon": "Link2",
        "title": "Проверить ссылки",
        "text": "Проверь ссылки кампаний по шаблону сайта и предложи исправить косяки.",
    },
    {
        "key": "week_plan", "category": "report", "places": ["project"],
        "title": "План на неделю",
        "text": "Составь план оптимизации на неделю",
    },
    {
        "key": "week_report", "category": "report", "places": ["project"],
        "title": "Отчёт за неделю",
        "text": "Сделай короткий отчёт за последние 7 дней: расход, конверсии, CPA, что изменилось и почему, "
                "что сделать дальше.",
    },
    # --- Проект без кампаний: статистики и запросов нет, спрашивать про них нечего ---
    {
        "key": "brief", "category": "review", "places": ["first"],
        "title": "Заполнить бриф",
        "text": "Помоги заполнить бриф: продукт, оффер, гео, бюджет и цель",
    },
    {
        "key": "start_strategy", "category": "growth", "places": ["first"],
        "title": "Стратегия и бюджет для старта",
        "text": "Какую стратегию и какой бюджет выбрать для первой кампании?",
    },
    # --- Общий чат: ИИ сравнивает проекты и предлагает правки в любом из них ---
    {
        "key": "status_all", "category": "report", "places": ["general"], "roles": ["owner"], "home": ["owner"],
        "icon": "MessageCircle",
        "title": "Как дела у рекламы",
        "text": "Расскажи простыми словами, как идёт реклама по всем проектам последние 7 дней: где всё хорошо, "
                "где деньги уходят без заявок и что сделать в первую очередь.",
    },
    {
        "key": "more_leads_all", "category": "growth", "places": ["general"], "roles": ["owner"], "home": ["owner"],
        "icon": "TrendingUp",
        "title": "Как получить больше заявок",
        "text": "Как получить больше заявок за те же деньги? Посмотри по всем проектам, что уже приносит заявки, и "
                "предложи, что повторить или усилить. Объясни простыми словами.",
    },
    {
        "key": "first", "category": "review", "places": ["general"], "home": ["owner", "pro"], "icon": "Sparkles",
        "title": "С чего начать",
        "text": "Посмотри мои проекты и кампании и скажи, с чего начать: где теряются деньги, что настроено не так "
                "и какие три действия дадут результат быстрее всего.",
    },
    {
        "key": "kpi_off", "category": "review", "places": ["general"],
        "title": "Кто выбивается из KPI?",
        "text": "Какие проекты выбиваются из KPI и почему?",
    },
    {
        "key": "kpi", "category": "review", "places": ["general"], "home": ["pro"], "icon": "Target",
        "title": "Проверить цели проектов",
        "text": "Проверь, в каких проектах не выбрана цель Метрики, не задан KPI или месячный бюджет, "
                "и предложи значения по данным за последний месяц.",
    },
    {
        "key": "waste_all", "category": "save", "places": ["general"], "home": ["owner"], "icon": "SearchCheck",
        "title": "Где сливается бюджет?",
        "text": "Где во всех проектах сливается бюджет без конверсий?",
    },
    {
        "key": "cleanup", "category": "save", "places": ["general"], "home": ["pro"], "icon": "SearchCheck",
        "title": "Почистить запросы",
        "text": "Найди по всем проектам поисковые запросы, которые тратят деньги без конверсий, "
                "и предложи минус-фразы для них.",
    },
    {
        "key": "stop_scale", "category": "growth", "places": ["general"],
        "title": "Что остановить, что масштабировать",
        "text": "Найди кампании, которые стоит остановить, и те, что стоит масштабировать",
    },
    {
        "key": "analytics", "category": "report", "places": ["general"], "home": ["pro"], "icon": "TrendingUp",
        "title": "Сравнить проекты",
        "text": "Сравни проекты за последнюю неделю: где растёт расход без конверсий, у кого CPA или ДРР хуже KPI, "
                "какие кампании тянут результат вниз. Дай по каждому проекту главное действие.",
    },
    {
        "key": "week_plan_all", "category": "report", "places": ["general"],
        "title": "План работ на неделю",
        "text": "Составь план работ на неделю по всем проектам",
    },
    # --- Вопрос про кампании из списка: к тексту добавляется, о каких кампаниях речь ---
    {
        "key": "camp_waste", "category": "save", "places": ["campaigns"],
        "title": "Какие тратят впустую?", "text": "Какие тратят впустую?",
    },
    {
        "key": "camp_off", "category": "save", "places": ["campaigns"],
        "title": "Что отключить?", "text": "Что отключить?",
    },
    {
        "key": "camp_budget", "category": "growth", "places": ["campaigns"],
        "title": "Куда добавить бюджет?", "text": "Куда добавить бюджет?",
    },
    {
        "key": "picked_cpa", "category": "review", "places": ["campaigns_picked"],
        "title": "Почему у них дорогая заявка?", "text": "Почему у них дорогая заявка?",
    },
    {
        "key": "picked_off", "category": "save", "places": ["campaigns_picked"],
        "title": "Что из этого отключить?", "text": "Что из этого отключить?",
    },
    {
        "key": "picked_compare", "category": "report", "places": ["campaigns_picked"],
        "title": "Сравни их между собой", "text": "Сравни их между собой",
    },
]

TITLE_LEN = 80
TEXT_LEN = 8000
OWN_LIMIT = 100


ROLES = ("owner", "pro")


def builtin() -> list[dict]:
    """Встроенный набор: roles — кому видна, home — кому стоит на главной (оба — списки ролей)."""
    return [{"icon": "", **p, "roles": list(p.get("roles") or ROLES), "home": list(p.get("home") or [])} for p in BUILTIN]


def _out(row: dict) -> dict:
    return {"id": row["id"], "title": row["title"], "text": row["text"], "sort": row["sort"]}


def _title(title: str, text: str) -> str:
    """Название задачи; пустое — начало текста одной строкой."""
    title = " ".join((title or "").split())
    if not title:
        title = " ".join(text.split())
    return title if len(title) <= TITLE_LEN else title[: TITLE_LEN - 1].rstrip() + "…"


def own(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_prompts WHERE user_id = %s ORDER BY sort, id", (user_id,)
        ).fetchall()
    return [_out(r) for r in rows]


def overview(user_id: int) -> dict:
    """Всё для выбора задачи: категории, встроенный набор (фильтр по месту — на клиенте) и свои."""
    return {"categories": CATEGORIES, "builtin": builtin(), "own": own(user_id)}


def create(user_id: int, title: str, text: str) -> dict:
    """Своя задача. Тот же текст уже сохранён — возвращаем его, второй копии не будет."""
    text = (text or "").strip()
    if not text:
        raise YandexError("args", "Пустой текст задачи")
    if len(text) > TEXT_LEN:
        raise YandexError("args", f"Текст задачи — до {TEXT_LEN} символов")
    with pool.connection() as conn:
        same = conn.execute(
            "SELECT * FROM ai_prompts WHERE user_id = %s AND text = %s", (user_id, text)
        ).fetchone()
        if same:
            return _out(same)
        n = conn.execute("SELECT COUNT(*) AS n FROM ai_prompts WHERE user_id = %s", (user_id,)).fetchone()["n"]
        if n >= OWN_LIMIT:
            raise YandexError("args", f"Своих задач — не больше {OWN_LIMIT}. Удалите ненужные.")
        row = conn.execute(
            """
            INSERT INTO ai_prompts (user_id, title, text, sort)
            VALUES (%s, %s, %s, (SELECT COALESCE(MAX(sort), 0) + 1 FROM ai_prompts WHERE user_id = %s))
            RETURNING *
            """,
            (user_id, _title(title, text), text, user_id),
        ).fetchone()
    return _out(row)


def update(user_id: int, prompt_id: int, title: str | None = None, text: str | None = None,
           sort: int | None = None) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT * FROM ai_prompts WHERE id = %s AND user_id = %s", (prompt_id, user_id)
        ).fetchone()
        if row is None:
            raise YandexError("not-found")
        new_text = row["text"] if text is None else text.strip()
        if not new_text:
            raise YandexError("args", "Пустой текст задачи")
        if len(new_text) > TEXT_LEN:
            raise YandexError("args", f"Текст задачи — до {TEXT_LEN} символов")
        new_title = row["title"] if title is None else _title(title, new_text)
        row = conn.execute(
            "UPDATE ai_prompts SET title = %s, text = %s, sort = %s, updated_at = NOW() WHERE id = %s RETURNING *",
            (new_title, new_text, row["sort"] if sort is None else int(sort), prompt_id),
        ).fetchone()
    return _out(row)


def delete(user_id: int, prompt_id: int) -> None:
    with pool.connection() as conn:
        gone = conn.execute(
            "DELETE FROM ai_prompts WHERE id = %s AND user_id = %s RETURNING id", (prompt_id, user_id)
        ).fetchone()
    if gone is None:
        raise YandexError("not-found")
