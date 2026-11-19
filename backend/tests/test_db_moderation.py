"""Причины отказа модерации на настоящем Postgres: сводка, список кампаний и get_campaign у ИИ."""

from datetime import timedelta

from app import ai_agent
from app.ads import parse_ads, upsert_ads
from app.analytics import workspace_analytics
from app.overview import build_overview
from tests.test_db_ai import _secret, _seed  # noqa: F401 — _secret: автофикстура


def _group(db, s, campaign_row_id, adgroup_id):
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name) "
            "VALUES (%s, %s, %s, %s, 'Окна') RETURNING id",
            (s["user"], s["acc"], campaign_row_id, adgroup_id),
        ).fetchone()["id"]


def _ad(ad_id, adgroup_id, status="ACCEPTED", reason="", sitelinks=None):
    text_ad = {"Title": f"Окна {ad_id}", "Text": "Скидка", "Href": "https://okna.ru"}
    if sitelinks:
        text_ad["SitelinksModeration"] = {"Status": "REJECTED", "StatusClarification": sitelinks}
    return {"Id": ad_id, "AdGroupId": adgroup_id, "Type": "TEXT_AD", "State": "ON", "Status": status,
            "StatusClarification": reason, "TextAd": text_ad}


def test_rejection_reasons_reach_overview_list_and_ai(db):
    s = _seed(db)
    user = s["user"]
    search_group = _group(db, s, s["search"], 501)
    network_group = _group(db, s, s["network"], 502)
    upsert_ads(user, s["acc"], {501: search_group, 502: network_group}, parse_ads([
        _ad(1, 501, "REJECTED", "Нет подтверждения скидки"),
        _ad(2, 501),
        _ad(3, 502, sitelinks="Ссылка не открывается"),
    ]))
    with db.connection() as conn:
        conn.execute(
            "UPDATE direct_campaigns SET status = 'REJECTED', state = 'OFF', "
            "status_clarification = 'Сайт недоступен' WHERE id = %s",
            (s["network"],),
        )

    problems = {
        (p["campaign_row_id"], p["kind"]): p for p in build_overview(user)["problems"] if p["campaign_row_id"]
    }
    rejected = problems[(s["search"], "ads_rejected")]
    assert (rejected["severity"], rejected["title"]) == ("warning", "Отклонено объявлений: 1")
    assert rejected["detail"] == "«Окна 1» — Нет подтверждения скидки"
    campaign = problems[(s["network"], "moderation")]
    assert (campaign["severity"], campaign["detail"]) == ("critical", "Сайт недоступен")

    today = ai_agent.today_msk()
    data = workspace_analytics(user, s["ws"], date_from=today - timedelta(days=7), date_to=today, with_tops=False)
    rows = {r["campaign_row_id"]: r for r in data["by_campaign"]}
    assert rows[s["search"]]["ads_rejected"] == 1
    # Объявление показывается, отклонены только быстрые ссылки — тоже повод посмотреть.
    assert rows[s["network"]]["ads_rejected"] == 1
    assert rows[s["network"]]["status_clarification"] == "Сайт недоступен"

    ctx = ai_agent.Context(user, s["ws"])
    camp = ai_agent.READ_TOOLS["get_campaign"](ctx, {"campaign_row_id": s["search"]})
    ads = {a["id"]: a for a in camp["ads"]}
    assert [a.get("rejected_reason") for a in ads.values()] == ["Нет подтверждения скидки", None]
    network = ai_agent.READ_TOOLS["get_campaign"](ctx, {"campaign_row_id": s["network"]})
    assert network["campaign"]["status_clarification"] == "Сайт недоступен"
    assert network["ads"][0]["rejected_extras"] == [{"what": "Быстрые ссылки", "reason": "Ссылка не открывается"}]

    # Объявление приняли после правки — причины больше нет.
    upsert_ads(user, s["acc"], {501: search_group}, parse_ads([_ad(1, 501, "ACCEPTED")]))
    camp = ai_agent.READ_TOOLS["get_campaign"](ctx, {"campaign_row_id": s["search"]})
    assert all("rejected_reason" not in a for a in camp["ads"])

    # Снова отказ, и ещё отклонены быстрые ссылки у принятого объявления — заголовок говорит и о них.
    upsert_ads(user, s["acc"], {501: search_group}, parse_ads([
        _ad(1, 501, "REJECTED", "Нет подтверждения скидки"), _ad(4, 501, sitelinks="Ссылка не открывается"),
    ]))
    rejected = next(
        p for p in build_overview(user)["problems"] if (p["campaign_row_id"], p["kind"]) == (s["search"], "ads_rejected")
    )
    assert rejected["title"] == "Отклонено объявлений: 1 и дополнения"
    assert rejected["detail"].endswith("Быстрые ссылки в «Окна 4» — Ссылка не открывается")
