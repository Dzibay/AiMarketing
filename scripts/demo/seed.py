"""Демо-данные кабинета для проверки интерфейса: 3 проекта, 2 кабинета Директа, 9 кампаний, 7 счётчиков,
60 дней статистики, визиты Метрики, запросы, ключевые фразы, журнал, правила, минус-фразы и чаты.

Запуск (БД — пустая временная, см. README):
  backend/.venv/Scripts/python scripts/demo/seed.py

Токен входа пишется в DEMO_DIR/token.txt, номера проектов и кампаний — в DEMO_DIR/stand.json.
"""

from __future__ import annotations

import os
import random
import secrets
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import env  # noqa: E402  — до app.*: настройки читаются при импорте

from app import ai_agent, journal, negative_lists, rules, search_queries, stats  # noqa: E402
from app.clients import create_client  # noqa: E402
from app.clock import now_msk  # noqa: E402
from app.db import ensure_schema, pool  # noqa: E402
from app.users import register_user  # noqa: E402
from app.web_auth import make_session  # noqa: E402
from tests.dbutil import (  # noqa: E402
    make_account,
    make_campaign,
    make_connection,
    make_counter,
    make_workspace,
    reset_services,
    setup_ai,
)

random.seed(7)
ensure_schema()
with pool.connection() as conn:
    if conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]:
        sys.exit("В базе уже есть пользователи — сид рассчитан на пустую временную БД")

reset_services(pool)
setup_ai(model="gpt-demo", fast="gpt-demo-mini")
user = register_user(email="demo@tolk.test", password=secrets.token_urlsafe(16), name="Демо")
uid = user["id"]
conn_id = make_connection(pool, uid, "agency-lab")
today = now_msk().date()
DAYS = 60


def sql(query, params=()):
    with pool.connection() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if "RETURNING" in query else None


romashka = create_client(uid, name="Цветы Ромашка")
vector = create_client(uid, name="Мебель Вектор")
acc_r = make_account(pool, uid, conn_id, name="Ромашка Директ", login="romashka-flowers")
acc_v = make_account(pool, uid, conn_id, name="Вектор Директ", login="vector-mebel")
sql(
    "UPDATE direct_accounts SET balance_kopecks = %s, balance_currency = 'RUB', balance_synced_at = now() WHERE id = %s",
    (1_850_000, acc_r),
)
sql(
    "UPDATE direct_accounts SET balance_kopecks = %s, balance_currency = 'RUB', balance_synced_at = now() WHERE id = %s",
    (240_000, acc_v),
)

CAMPAIGNS = {}


def campaign(acc, cid, name, ctype="TEXT_CAMPAIGN", state="ON", status="ACCEPTED", budget=3000):
    row = make_campaign(pool, uid, acc, cid, name, state=state, status=status)
    sql(
        "UPDATE direct_campaigns SET campaign_type = %s, daily_budget_kopecks = %s, "
        "strategy = 'WB_MAXIMUM_CONVERSION_RATE' WHERE id = %s",
        (ctype, budget * 100, row),
    )
    CAMPAIGNS[cid] = (acc, row)
    return row


r1 = campaign(acc_r, 1001, "Поиск | Доставка цветов | Москва и МО", budget=5000)
r2 = campaign(acc_r, 1002, "РСЯ | Букеты на 8 марта | ретаргетинг", budget=2000)
r3 = campaign(acc_r, 1003, "Мастер кампаний | Розы", "UNIFIED_CAMPAIGN", budget=4000)
r4 = campaign(acc_r, 1004, "Поиск | Бренд Ромашка", budget=800)
r5 = campaign(acc_r, 1005, "Поиск | Свадебные букеты (тест)", state="OFF", budget=1500)
v1 = campaign(acc_v, 2001, "Поиск | Диваны | СПб", budget=6000)
v2 = campaign(acc_v, 2002, "Товарная кампания | Фид мебели", "SMART_CAMPAIGN", budget=3500)
v3 = campaign(acc_v, 2003, "РСЯ | Кухни на заказ", state="OFF", budget=2500)
v4 = campaign(acc_v, 2004, "Поиск | Шкафы-купе", status="REJECTED", budget=1200)

# Расход в день и заявки: у выключенных кампаний последние 10 дней пусто.
PROFILE = {1001: (4800, 6), 1002: (1700, 1), 1003: (3600, 4), 1004: (600, 3), 1005: (0, 0),
           2001: (5200, 2), 2002: (3000, 3), 2003: (0, 0), 2004: (400, 0)}
for cid, (cost, conv) in PROFILE.items():
    acc = CAMPAIGNS[cid][0]
    for back in range(DAYS + 1):
        if cost == 0 and back < 10:
            continue
        k = random.uniform(0.6, 1.4) * (0.35 if back == 0 else 1)
        cost_rub = int((cost or 1500) * k)
        clicks = max(1, int(cost_rub / random.uniform(28, 65)))
        stats.upsert_direct_stat(
            user_id=uid, day=today - timedelta(days=back), account_id=acc, campaign_id=cid,
            impressions=clicks * random.randint(12, 40), clicks=clicks, cost_kopecks=cost_rub * 100,
            conversions=max(0, int((conv or 1) * random.uniform(0.3, 1.6))) if back else 0,
            revenue_kopecks=(cost_rub * 100 * random.randint(3, 9)) if acc == acc_v else 0,
        )
sql("UPDATE direct_accounts SET stats_synced_at = now() - interval '20 minutes' WHERE user_id = %s", (uid,))

cnt_r = make_counter(pool, uid, conn_id, 51001, "romashka-flowers.ru")
cnt_v = make_counter(pool, uid, conn_id, 52002, "vector-mebel.ru")
extra = [
    make_counter(pool, uid, conn_id, 53000 + i, site)
    for i, site in enumerate(
        ["old.romashka-flowers.ru", "test-landing.tilda.ws", "vector-mebel.ru/kuhni", "promo-8marta.ru",
         "staging.vector-mebel.ru"]
    )
]
sql("UPDATE metrika_counters SET name = site WHERE user_id = %s", (uid,))
# Визиты Метрики у двух проектов; у «Промо 8 марта» счётчик есть, но данных ещё нет.
with pool.connection() as conn:
    for counter_row, visits in ((cnt_r, 140), (cnt_v, 60)):
        for back in range(DAYS + 1):
            v = int(visits * random.uniform(0.75, 1.25))
            conn.execute(
                """
                INSERT INTO stats_daily (user_id, day, source, counter_row_id, visits, users_count, bounce_rate,
                                         updated_at)
                VALUES (%s, %s, 'metrika', %s, %s, %s, %s, now() - interval '2 hours')
                """,
                (uid, today - timedelta(days=back), counter_row, v, int(v * 0.8), round(random.uniform(18, 34), 2)),
            )

ws_r = make_workspace(pool, uid, 51001, "Цветы Ромашка", counter_row_id=cnt_r, client_id=romashka["id"],
                      accounts=[acc_r], campaigns=[r1, r2, r3, r4, r5], kpi_type="cpa", kpi_target=900,
                      monthly_budget_rub=150000)
ws_v = make_workspace(pool, uid, 52002, "Мебель Вектор", counter_row_id=cnt_v, client_id=vector["id"],
                      accounts=[acc_v], campaigns=[v1, v2, v3, v4], kpi_type="drr", kpi_target=20,
                      monthly_budget_rub=200000)
ws_x = make_workspace(pool, uid, 53003, "Промо 8 марта", counter_row_id=extra[3])
for ws, site in ((ws_r, "romashka-flowers.ru"), (ws_v, "vector-mebel.ru"), (ws_x, "promo-8marta.ru")):
    sql("UPDATE workspaces SET site = %s WHERE id = %s", (site, ws))


def adgroups(acc, row, cid, names):
    out = {}
    for j, name in enumerate(names):
        gid = cid * 10 + j
        out[gid] = sql(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name, status, type) "
            "VALUES (%s, %s, %s, %s, %s, 'ACCEPTED', 'TEXT_AD_GROUP') RETURNING id",
            (uid, acc, row, gid, name),
        )[0]["id"]
    return out


groups_r = adgroups(acc_r, r1, 1001, ["Доставка цветов", "Букеты роз", "Цветы недорого"])
groups_v = adgroups(acc_v, v1, 2001, ["Диваны", "Угловые диваны"])
QUERIES_R = ["доставка цветов москва", "купить букет недорого", "цветы с доставкой на дом", "букет роз 101 шт цена",
             "цветы бесплатно скачать картинки", "как вырастить розы", "доставка цветов круглосуточно",
             "букет на день рождения маме", "цветы оптом от производителя", "ромашки купить",
             "работа флористом вакансии", "свадебный букет невесты", "доставка цветов ночью москва", "розы в коробке",
             "тюльпаны 8 марта"]
QUERIES_V = ["купить диван спб", "угловой диван недорого", "диван кровать икеа", "диван своими руками",
             "ремонт дивана на дому", "диван с ортопедическим матрасом", "мебельная фабрика вакансии", "диван бу авито"]


def queries(acc, row, groups, texts):
    items = []
    for text in texts:
        clicks = random.randint(0, 40)
        items.append({
            "query": text, "matched_keyword": " ".join(text.split()[:2]), "adgroup_id": random.choice(list(groups)),
            "impressions": clicks * random.randint(8, 30) + random.randint(1, 50), "clicks": clicks,
            "cost_kopecks": clicks * random.randint(2500, 7000),
            "conversions": random.choice([0, 0, 0, 1, 2]) if clicks > 5 else 0,
        })
    search_queries.upsert_search_queries(
        uid, account_id=acc, campaign_row_id=row, date_from=today - timedelta(days=14),
        date_to=today - timedelta(days=1), items=items, adgroup_row_by_direct_id=groups,
    )


queries(acc_r, r1, groups_r, QUERIES_R)
queries(acc_v, v1, groups_v, QUERIES_V)
for gid, group_row in list(groups_r.items()) + list(groups_v.items()):
    flowers = gid < 20000
    for word in ("купить", "цена", "доставка"):
        sql(
            "INSERT INTO direct_keywords (user_id, account_id, adgroup_row_id, keyword_id, keyword, state, status, "
            "bid_micros, period_clicks, period_cost_kopecks, period_conversions) "
            "VALUES (%s, %s, %s, %s, %s, 'ON', 'ACCEPTED', %s, %s, %s, %s)",
            (uid, acc_r if flowers else acc_v, group_row, gid * 10 + len(word),
             f"{word} {'цветы' if flowers else 'диван'}", random.randint(10, 60) * 1_000_000, random.randint(5, 80),
             random.randint(50_000, 600_000), random.randint(0, 4)),
        )

for action, title, ws in [
    ("set_budget", "Дневной бюджет «Поиск | Доставка цветов» 4 000 → 5 000 ₽", ws_r),
    ("add_negatives", "Добавлены минус-слова: бесплатно, картинки, вакансии (3)", ws_r),
    ("pause_campaign", "Остановлена «РСЯ | Кухни на заказ»", ws_v),
    ("add_negatives", "Минус-слова «Поиск | Диваны»: своими руками, ремонт, бу", ws_v),
    ("create_draft", "Черновик кампании «Поиск | Букеты 8 марта» создан ИИ", ws_r),
]:
    journal.log_action(uid, action, {"campaign_ids": [1001 if ws == ws_r else 2003]}, {"ok": True}, "ok",
                       workspace_id=ws, title=title)

rules.create_rule(uid, name="Стоп при расходе без конверсий", rule_type="spend_no_conv", threshold_value=3000,
                  lookback_days=7, action="pause", dry_run=True, scope_workspace_id=ws_r)
rules.create_rule(uid, name="CPA выше 1500 ₽", rule_type="cpa_above", threshold_value=1500, lookback_days=14,
                  action="pause", dry_run=False, scope_workspace_id=ws_v)
rules.create_rule(uid, name="Дорогая заявка по всем сайтам", rule_type="cpa_above", threshold_value=3500,
                  lookback_days=7, action="pause", dry_run=True)
negative_lists.create_negative_list(uid, name="Общие минус-слова",
                                    phrases=["бесплатно", "скачать", "вакансии", "своими руками", "бу", "авито"])

CHATS = [
    (None, "Сравни расход по проектам", [
        ("user", "Где расход растёт быстрее всего?"),
        ("assistant", "За 7 дней быстрее всего растёт **Мебель Вектор**: +18% к прошлой неделе при ДРР 24% "
                      "(цель 20%).\n\n- «Поиск | Диваны | СПб» — 36 400 ₽, 14 продаж\n"
                      "- «Товарная кампания» — 21 000 ₽, 21 продажа\n\nПредлагаю срезать ставки в «Поиск | Диваны» "
                      "на 10%."),
    ]),
    (ws_r, "Минус-слова для доставки цветов", [
        ("user", "Почисти запросы в поиске"),
        ("assistant", "Нашёл 4 мусорных запроса за 14 дней (2 140 ₽ без заказов): «цветы бесплатно скачать "
                      "картинки», «как вырастить розы», «работа флористом вакансии». Добавить минус-слова?"),
    ]),
]
chats = []
with pool.connection() as conn:
    for ws, title, messages in CHATS:
        chat = conn.execute(
            "INSERT INTO ai_chats (user_id, workspace_id, title, status) VALUES (%s, %s, %s, 'idle') RETURNING id",
            (uid, ws, title),
        ).fetchone()["id"]
        chats.append(chat)
        for role, text in messages:
            ai_agent._insert_message(conn, chat, role, text)

env.TOKEN_FILE.write_text(make_session(uid), encoding="utf-8")
env.save_stand({
    "user_id": uid,
    "workspaces": {"flowers": ws_r, "furniture": ws_v, "promo": ws_x},
    "campaign_row": r1,
    "chats": {"general": chats[0], "flowers": chats[1]},
})
print(f"Готово: пользователь {uid}, проекты {ws_r}, {ws_v}, {ws_x}. Токен и номера — в {env.DEMO_DIR}")
