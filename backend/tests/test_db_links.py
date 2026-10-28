"""Метки групп, быстрые ссылки и ошибки правил на настоящем Postgres."""

from app import links, rules
from app.adgroups import upsert_adgroups
from app.ads import upsert_ads
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace

TEMPLATE = "https://happ-connection.ru/?ref=Yandex&utm_source=yandex&utm_campaign={campaign_id}"
TRACKING = "utm_source=yandex&utm_campaign={campaign_id}"


def _setup(pool):
    user = make_user(pool)
    acc = make_account(pool, user, make_connection(pool, user))
    camp = make_campaign(pool, user, acc, 101)
    ws = make_workspace(pool, user, 1, campaigns=[camp])
    with pool.connection() as conn:
        conn.execute("UPDATE workspaces SET url_template = %s WHERE id = %s", (TEMPLATE, ws))
        conn.execute("UPDATE direct_campaigns SET tracking_params = %s WHERE id = %s", (TRACKING, camp))
    return user, acc, camp, ws


def _ad(ad_id: int, **extra) -> dict:
    return {
        "ad_id": ad_id,
        "adgroup_id": 70,
        "type": "TEXT_AD",
        "state": "ON",
        "title": f"Объявление {ad_id}",
        "href": "https://happ-connection.ru/?ref=Yandex",
        **extra,
    }


def _ad_row(pool, ad_id: int) -> dict:
    with pool.connection() as conn:
        return conn.execute("SELECT sitelink_set_id, sitelink_hrefs FROM direct_ads WHERE ad_id = %s", (ad_id,)).fetchone()


def test_group_tracking_and_sitelinks_are_checked(db):
    user, acc, camp, ws = _setup(db)
    upsert_adgroups(user, acc, camp, [{"adgroup_id": 70, "campaign_id": 101, "name": "Своя", "tracking_params": "utm_source=vk"}])
    # Синхронизация без меток (старый путь) не стирает загруженные.
    upsert_adgroups(user, acc, camp, [{"adgroup_id": 70, "campaign_id": 101, "name": "Своя"}])
    with db.connection() as conn:
        group = conn.execute("SELECT id, tracking_params FROM direct_adgroups WHERE adgroup_id = 70").fetchone()
    assert group["tracking_params"] == "utm_source=vk"

    bad_links = ["https://happ-connection.ru/faq"]
    upsert_ads(user, acc, {70: group["id"]}, [_ad(1, sitelink_set_id=77, sitelink_hrefs=bad_links), _ad(2, sitelink_set_id=None, sitelink_hrefs=[])])
    issues = links.workspace_link_issues(user, ws)
    [c] = issues["campaigns"]
    assert c["campaign_problems"] == [] and c["groups_total"] == 1
    assert c["groups"][0]["fixed_tracking"] == TRACKING
    assert [(a["ad_id"], a["sitelink_problems"]) for a in c["ads"]] == [(1, ["нет ref=Yandex"])]

    # Тот же набор, адреса не прочитались — прежние остаются; другой набор — адреса неизвестны.
    upsert_ads(user, acc, {70: group["id"]}, [_ad(1, sitelink_set_id=77)])
    assert _ad_row(db, 1)["sitelink_hrefs"] == bad_links
    upsert_ads(user, acc, {70: group["id"]}, [_ad(1, sitelink_set_id=78)])
    assert dict(_ad_row(db, 1)) == {"sitelink_set_id": 78, "sitelink_hrefs": None}


def test_rule_run_keeps_partial_apply_error(db, monkeypatch):
    user, acc, camp, ws = _setup(db)
    rule = rules.create_rule(user, name="Дорогой CPA", rule_type="cpa_above", threshold_value=100, dry_run=False)
    monkeypatch.setattr(rules, "_matching_campaigns", lambda user_id, r: [{"campaign_row_id": camp, "name": "К"}])
    monkeypatch.setattr(rules, "_apply", lambda *a: (0, "«К»: кампания в архиве"))
    sent = []
    monkeypatch.setattr(rules, "notify_user", lambda user_id, text: sent.append(text))
    out = rules.run_rule(user, rule["id"])
    assert out["error"] == "«К»: кампания в архиве"
    [run] = rules.list_rule_runs(user)
    assert (run["applied"], run["error"]) == (0, "«К»: кампания в архиве")
    assert "Не всё применилось: «К»: кампания в архиве" in sent[0]
