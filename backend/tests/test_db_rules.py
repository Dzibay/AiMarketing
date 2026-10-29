"""Правило, ограниченное одной кампанией: проверка выбора и что под него попадает только она."""

import pytest

from app import rules
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace


@pytest.fixture
def cabinet(db):
    user = make_user(db)
    conn_id = make_connection(db, user)
    acc = make_account(db, user, conn_id, login="a")
    other_acc = make_account(db, user, conn_id, login="b")
    first = make_campaign(db, user, acc, 101, name="Поиск")
    copy = make_campaign(db, user, acc, 102, name="Поиск")
    foreign = make_campaign(db, user, other_acc, 201)
    ws = make_workspace(db, user, 1, accounts=[acc], campaigns=[first])
    return {"user": user, "acc": acc, "first": first, "copy": copy, "foreign": foreign, "ws": ws}


def _group(db, c, campaign, adgroup_id):
    with db.connection() as conn:
        return conn.execute(
            """
            INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name)
            VALUES (%s, %s, %s, %s, 'Группа') RETURNING id
            """,
            (c["user"], c["acc"], campaign, adgroup_id),
        ).fetchone()["id"]


def test_campaign_rule_matches_only_that_campaign(cabinet):
    c = cabinet
    rule = rules.create_rule(
        c["user"], name="Слив", rule_type="spend_no_conv", threshold_value=0, scope_campaign_row_id=c["copy"]
    )
    assert rule["scope_campaign_row_id"] == c["copy"]
    assert rule["campaign_name"] == "Поиск" and rule["scope_campaign_id"] == 102

    result = rules.run_rule(c["user"], rule["id"], force_dry_run=True)
    assert [m["row_id"] for m in result["items"]] == [c["copy"]]

    # Без кампании — весь кабинет.
    everything = rules.create_rule(c["user"], name="Все", rule_type="spend_no_conv", threshold_value=0)
    found = {m["row_id"] for m in rules.run_rule(c["user"], everything["id"], force_dry_run=True)["items"]}
    assert found == {c["first"], c["copy"], c["foreign"]}


def test_keyword_and_query_rules_respect_campaign(db, cabinet):
    c = cabinet
    g1, g2 = _group(db, c, c["first"], 1), _group(db, c, c["copy"], 2)
    with db.connection() as conn:
        for group, kid in ((g1, 11), (g2, 12)):
            conn.execute(
                """
                INSERT INTO direct_keywords (user_id, account_id, adgroup_row_id, keyword_id, keyword, state,
                                             period_clicks, period_cost_kopecks, period_conversions)
                VALUES (%s, %s, %s, %s, %s, 'ON', 5, 50000, 0)
                """,
                (c["user"], c["acc"], group, kid, f"фраза {kid}"),
            )
        for camp, group, text in ((c["first"], g1, "купить слона"), (c["copy"], g2, "слон даром")):
            conn.execute(
                """
                INSERT INTO direct_search_queries (
                    user_id, account_id, campaign_row_id, adgroup_row_id, query, matched_keyword,
                    impressions, clicks, cost_kopecks, conversions, date_from, date_to
                )
                VALUES (%s, %s, %s, %s, %s, '', 50, 5, 50000, 0, '2026-10-12', '2026-10-25')
                """,
                (c["user"], c["acc"], camp, group, text),
            )

    kw_rule = rules.create_rule(
        c["user"], name="Ключи", target="keyword", rule_type="spend_no_conv", threshold_value=100,
        scope_campaign_row_id=c["first"],
    )
    assert [m["name"] for m in rules.run_rule(c["user"], kw_rule["id"], force_dry_run=True)["items"]] == ["фраза 11"]

    q_rule = rules.create_rule(
        c["user"], name="Запросы", target="query", rule_type="spend_no_conv", threshold_value=100,
        action="negative_adgroup", scope_campaign_row_id=c["copy"],
    )
    assert [m["name"] for m in rules.run_rule(c["user"], q_rule["id"], force_dry_run=True)["items"]] == ["слон даром"]


def test_campaign_must_fit_account_workspace_and_user(db, cabinet):
    c = cabinet
    with pytest.raises(YandexError) as exc:
        rules.create_rule(c["user"], name="x", rule_type="cpa_above", threshold_value=1,
                          scope_account_id=c["acc"], scope_campaign_row_id=c["foreign"])
    assert exc.value.code == "rule-campaign"

    with pytest.raises(YandexError) as exc:
        rules.create_rule(c["user"], name="x", rule_type="cpa_above", threshold_value=1,
                          scope_workspace_id=c["ws"], scope_campaign_row_id=c["copy"])
    assert exc.value.code == "rule-campaign"

    stranger = make_user(db, "s@example.com")
    with pytest.raises(YandexError) as exc:
        rules.create_rule(stranger, name="x", rule_type="cpa_above", threshold_value=1,
                          scope_campaign_row_id=c["first"])
    assert exc.value.code == "not-found"


def test_update_checks_campaign_against_new_scope(cabinet):
    c = cabinet
    rule = rules.create_rule(c["user"], name="x", rule_type="cpa_above", threshold_value=1,
                             scope_campaign_row_id=c["foreign"])
    # Кабинет сменили, а кампания осталась из другого — не даём.
    with pytest.raises(YandexError) as exc:
        rules.update_rule(c["user"], rule["id"], scope_account_id=c["acc"])
    assert exc.value.code == "rule-campaign"

    moved = rules.update_rule(c["user"], rule["id"], scope_account_id=c["acc"], scope_campaign_row_id=c["first"])
    assert moved["scope_campaign_row_id"] == c["first"] and moved["campaign_name"] == "Поиск"

    cleared = rules.update_rule(c["user"], rule["id"], scope_campaign_row_id=None)
    assert cleared["scope_campaign_row_id"] is None and cleared["campaign_name"] == ""


def test_rule_campaign_options(cabinet):
    c = cabinet
    all_options = rules.rule_campaigns(c["user"])
    assert {o["id"] for o in all_options} == {c["first"], c["copy"], c["foreign"]}
    assert next(o for o in all_options if o["id"] == c["first"])["workspace_ids"] == [c["ws"]]
    assert [o["id"] for o in rules.rule_campaigns(c["user"], workspace_id=c["ws"])] == [c["first"]]


def test_rule_goes_away_with_its_campaign(db, cabinet):
    c = cabinet
    rule = rules.create_rule(c["user"], name="x", rule_type="cpa_above", threshold_value=1,
                             scope_campaign_row_id=c["copy"])
    with db.connection() as conn:
        conn.execute("DELETE FROM direct_campaigns WHERE id = %s", (c["copy"],))
    assert rule["id"] not in {r["id"] for r in rules.list_rules(c["user"])}
