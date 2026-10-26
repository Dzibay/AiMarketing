from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.job_handlers import HANDLERS, dedupe_key, submit
from app.jobs import (
    BACKOFF_MAX_SECONDS,
    NON_RETRYABLE_CODES,
    backoff_seconds,
    is_retryable,
)
from app.workers import SCHEDULES

BACKEND = Path(__file__).resolve().parent.parent


def test_backoff_grows_and_is_capped():
    assert backoff_seconds(1) == 30
    assert backoff_seconds(2) == 60
    assert backoff_seconds(3) == 120
    assert backoff_seconds(50) == BACKOFF_MAX_SECONDS


def test_retryable_codes():
    assert is_retryable("direct")
    assert is_retryable("metrika")
    assert is_retryable("internal")
    for code in NON_RETRYABLE_CODES:
        assert not is_retryable(code)


def test_dedupe_key_ignores_payload_key_order():
    a = dedupe_key("stats_range", 1, {"date_from": "2026-01-01", "date_to": "2026-01-07"})
    b = dedupe_key("stats_range", 1, {"date_to": "2026-01-07", "date_from": "2026-01-01"})
    assert a == b
    assert a != dedupe_key("stats_range", 2, {"date_from": "2026-01-01", "date_to": "2026-01-07"})


def test_submit_rejects_unknown_kind():
    with pytest.raises(ValueError):
        submit("no-such-kind", 1)


def test_handlers_cover_web_and_scheduler_kinds():
    expected = {
        "sync_connection",
        "sync_account_campaigns",
        "sync_campaign_structure",
        "sync_search_queries",
        "sync_goals",
        "stats_day",
        "stats_range",
        "run_rules",
    }
    assert expected <= set(HANDLERS)
    assert set(SCHEDULES) == {"connections", "campaigns", "stats", "rules", "purge_jobs"}
    assert all(interval >= 60 for interval, _ in SCHEDULES.values())


def test_migrations_have_single_head():
    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert len(script.get_heads()) == 1


def test_alembic_ini_is_ascii():
    # Alembic читает ini в кодировке ОС (cp1251 на Windows) — кириллица ломает запуск.
    (BACKEND / "alembic.ini").read_bytes().decode("ascii")
