"""Честная свежесть данных и кабинет для пополнения — без БД."""

from datetime import datetime, timedelta, timezone

from app.analytics import _money_url, data_freshness, load_eta_minutes, merge_freshness
from app.overview import BALANCE_OUT_TITLE

NOW = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)


def _ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def _acc(id_, name, *, loaded=True, stage=None, rows=None, job=None) -> dict:
    return {
        "id": id_,
        "name": name,
        "login": name.lower(),
        "loaded_at": _ago(days=10) if loaded else None,
        "stage_at": stage,
        "rows_at": rows,
        "job": job,
    }


def test_eta_from_progress():
    job = {"status": "running", "done": 2, "total": 6, "started_at": _ago(minutes=4)}
    assert load_eta_minutes(job, NOW) == 8  # 4 мин на 2 шага → ещё 4 шага ≈ 8 мин
    # Только началось — хотя бы минута, не ноль.
    job = {"status": "running", "done": 5, "total": 6, "started_at": _ago(seconds=10)}
    assert load_eta_minutes(job, NOW) == 1
    # В очереди, без прогресса или уже всё — не знаем.
    assert load_eta_minutes({"status": "queued", "done": 0, "total": 0, "started_at": None}, NOW) is None
    assert load_eta_minutes({"status": "running", "done": 0, "total": 6, "started_at": _ago(minutes=1)}, NOW) is None
    assert load_eta_minutes({"status": "running", "done": 6, "total": 6, "started_at": _ago(minutes=1)}, NOW) is None
    assert load_eta_minutes(None, NOW) is None


def test_direct_time_is_the_oldest_account():
    f = data_freshness(
        [
            _acc(1, "Ромашка", stage=_ago(hours=1)),
            _acc(2, "Вектор", stage=_ago(hours=3), rows=_ago(hours=2)),
        ],
        _ago(minutes=30),
        NOW,
    )
    # У «Вектора» прошлые дни записаны 2 часа назад (ручная загрузка свежее планового чтения).
    assert f["direct_at"] == _ago(hours=2)
    assert f["metrika_at"] == _ago(minutes=30)
    assert f["loading"] is False and f["eta_minutes"] is None
    assert [a["lagging"] for a in f["accounts"]] == [False, False]


def test_lagging_account_is_named():
    f = data_freshness(
        [_acc(1, "Ромашка", stage=_ago(hours=1)), _acc(2, "Вектор", stage=_ago(hours=9))],
        None,
        NOW,
    )
    assert f["direct_at"] == _ago(hours=9)
    assert f["metrika_at"] is None
    lag = [a for a in f["accounts"] if a["lagging"]]
    assert [(a["id"], a["stats_at"]) for a in lag] == [(2, _ago(hours=9))]
    # Кабинет загружен, а статистики нет совсем — тоже отстал, а не «загружается».
    f = data_freshness([_acc(3, "Пусто")], None, NOW)
    assert f["accounts"][0]["lagging"] is True and f["accounts"][0]["loading"] is False
    assert f["direct_at"] is None


def test_first_load_with_eta():
    running = {"status": "running", "done": 1, "total": 4, "started_at": _ago(minutes=3)}
    f = data_freshness(
        [_acc(1, "Ромашка", stage=_ago(hours=1)), _acc(2, "Новый", loaded=False, job=running)],
        None,
        NOW,
    )
    assert f["loading"] is True and f["eta_minutes"] == 9
    new = f["accounts"][1]
    assert new["loading"] is True and new["updating"] is True and new["lagging"] is False
    # Один из загружающихся ещё в очереди — срок неизвестен.
    queued = {"status": "queued", "done": 0, "total": 0, "started_at": None}
    f = data_freshness(
        [_acc(2, "Новый", loaded=False, job=running), _acc(3, "Ещё", loaded=False, job=queued)], None, NOW
    )
    assert f["loading"] is True and f["eta_minutes"] is None


def test_merge_takes_oldest_and_dedupes_accounts():
    a = data_freshness([_acc(1, "Ромашка", stage=_ago(hours=1))], _ago(minutes=10), NOW)
    b = data_freshness(
        [_acc(1, "Ромашка", stage=_ago(hours=1)), _acc(2, "Вектор", stage=_ago(hours=4))], _ago(hours=2), NOW
    )
    m = merge_freshness([a, b, data_freshness([], None, NOW)])
    assert m["direct_at"] == _ago(hours=4)
    assert m["metrika_at"] == _ago(hours=2)
    assert [x["id"] for x in m["accounts"]] == [2, 1]  # по имени: Вектор, Ромашка
    assert m["loading"] is False
    assert merge_freshness([])["direct_at"] is None


def test_money_url_prefers_empty_then_shortest():
    def p(title, url, days):
        return {"kind": "balance", "title": title, "direct_url": url, "days_on_balance": days}

    low = "Деньги на счёте скоро закончатся"
    problems = [
        {"kind": "moderation", "title": "x"},
        p(low, "https://direct.yandex.ru/dna/grid/campaigns?ulogin=a", 3.0),
        p(low, "https://direct.yandex.ru/dna/grid/campaigns?ulogin=b", 1.2),
    ]
    assert _money_url(problems).endswith("ulogin=b")
    problems.append(p(BALANCE_OUT_TITLE, "https://direct.yandex.ru/dna/grid/campaigns?ulogin=c", 0.0))
    assert _money_url(problems).endswith("ulogin=c")
    assert _money_url([{"kind": "token", "title": "x"}]) == ""
