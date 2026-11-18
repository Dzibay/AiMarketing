"""Запись кампаний при синхронизации: тип ЕПК не затирается устаревшим типом из API v5."""

from app import direct_sync
from tests.dbutil import make_account, make_connection, make_user


def _row(db, account_id: int, campaign_id: int) -> dict:
    with db.connection() as conn:
        return conn.execute(
            "SELECT campaign_type, name FROM direct_campaigns WHERE account_id = %s AND campaign_id = %s",
            (account_id, campaign_id),
        ).fetchone()


def _item(campaign_id: int, ctype: str, **extra) -> dict:
    return {"campaign_id": campaign_id, "name": "Кампания", "campaign_type": ctype, "state": "ON", "status": "ACCEPTED", **extra}


def test_sync_keeps_unified_type_when_v5_calls_it_text(db):
    user = make_user(db)
    acc = make_account(db, user, make_connection(db, user))
    direct_sync.insert_campaign_local(user, acc, campaign_id=70, name="ЕПК", campaign_type="UNIFIED_CAMPAIGN")

    # v5 (запасная попытка без ЕПК) отдаёт ту же кампанию как текстовую — тип в базе остаётся.
    direct_sync.upsert_campaigns(user, acc, [_item(70, "TEXT_CAMPAIGN", type_exact=False, name="ЕПК новая")])
    assert _row(db, acc, 70) == {"campaign_type": "UNIFIED_CAMPAIGN", "name": "ЕПК новая"}

    # Другой тип из v5 не выдумка — записываем.
    direct_sync.upsert_campaigns(user, acc, [_item(70, "DYNAMIC_TEXT_CAMPAIGN", type_exact=False)])
    assert _row(db, acc, 70)["campaign_type"] == "DYNAMIC_TEXT_CAMPAIGN"


def test_sync_heals_text_type_from_v501(db):
    user = make_user(db)
    acc = make_account(db, user, make_connection(db, user))
    direct_sync.upsert_campaigns(user, acc, [_item(71, "TEXT_CAMPAIGN", type_exact=False)])
    assert _row(db, acc, 71)["campaign_type"] == "TEXT_CAMPAIGN"

    saved = direct_sync.upsert_campaigns(user, acc, [_item(71, "UNIFIED_CAMPAIGN", type_exact=True)])
    assert _row(db, acc, 71)["campaign_type"] == "UNIFIED_CAMPAIGN"
    # Смена подписи типа — не правка кампании: в историю изменений не попадает.
    assert saved["changed"] == 0
