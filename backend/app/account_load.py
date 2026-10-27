"""Загрузка данных кабинета Директа и счётчика Метрики.

Первая загрузка — всё за 30 дней по этапам: кампании → структура (группы, объявления, фразы) →
статистика по дням → поисковые запросы и статистика групп и фраз. Состояние этапов лежит
в load_state: прерванная загрузка (кончились баллы, кабинет выключили) продолжается с того же места.
Дальше — актуализация: структура только у кампаний, изменённых по Changes API, статистика
за последнюю неделю раз в 6 часов, запросы раз в сутки.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app import direct_api
from app.adgroups import ADGROUP_FIELDS, parse_adgroups, upsert_adgroups
from app.ads import AD_FIELDS, TEXT_AD_FIELDS, parse_ads, upsert_ads
from app.clock import today_msk, yesterday_msk
from app.db import pool
from app.direct_reports import fetch_report
from app.direct_sync import sync_account_campaigns
from app.entity_stats import _agg_metrics, write_account_period_stats
from app.keywords import KEYWORD_FIELDS, parse_keywords, upsert_keywords
from app.metrika_sync import canonical_counters, sync_counter_goals, sync_counter_stats_range
from app.search_queries import aggregate_query_rows, upsert_search_queries
from app.stats import parse_campaign_day_rows, replace_direct_stats, set_account_stats_status
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.account_load")

Progress = Callable[[int, int, str], None]

STAGES = ("campaigns", "structure", "stats", "queries")
COUNTER_STAGES = ("goals", "stats")
STAGE_TITLES = {
    "campaigns": "Кампании",
    "structure": "Структура",
    "stats": "Статистика",
    "queries": "Поисковые запросы",
    "goals": "Цели",
}
HISTORY_DAYS = 30
REFRESH_STATS_DAYS = 7
# adgroups.get, ads.get и keywords.get принимают до 10 кампаний.
STRUCTURE_CHUNK = 10
STRUCTURE_EVERY = timedelta(hours=24)
STATS_EVERY = timedelta(hours=6)
QUERIES_EVERY = timedelta(hours=24)
GOALS_EVERY = timedelta(hours=24)
# Меньше баллов — структуру при актуализации не читаем: пусть останутся на правки пользователя.
UNITS_RESERVE = 1000
# Такие ошибки повтор по одной кампании не исправит — этап останавливается.
FATAL_CODES = frozenset({"units", "token-expired", "unauthorized", "disabled", "not-found", "direct-denied"})
# Шкала прогресса задачи: каждый этап — 100 делений.
STAGE_SCALE = 100


class _Stopped(Exception):
    """Кабинет выключили во время загрузки."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age(stage: dict | None) -> timedelta | None:
    raw = (stage or {}).get("at")
    if not raw:
        return None
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(raw)
    except ValueError:
        return None


def is_due(stage: dict | None, every: timedelta, *, initial: bool) -> bool:
    """Нужен ли этап: недоделанный — всегда; готовый — при первой загрузке нет, потом — по давности."""
    stage = stage or {}
    if stage.get("status") != "ok":
        return True
    if initial:
        return False
    age = _age(stage)
    return age is None or age >= every


def stats_days(stage: dict | None, *, initial: bool) -> int:
    """За сколько дней перечитать статистику: при первой загрузке и после долгого перерыва — за все 30."""
    age = _age(stage)
    if initial or (stage or {}).get("status") != "ok" or age is None or age > timedelta(days=REFRESH_STATS_DAYS - 1):
        return HISTORY_DAYS
    return REFRESH_STATS_DAYS


def parse_changed_campaigns(result: dict) -> set[int]:
    """Кампании из changes.checkCampaigns, у которых поменялись параметры или содержимое."""
    out: set[int] = set()
    for item in result.get("Campaigns") or []:
        if {"SELF", "CHILDREN"} & set(item.get("ChangesIn") or []):
            try:
                out.add(int(item["CampaignId"]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _account_enabled(account_id: int) -> bool:
    with pool.connection() as conn:
        row = conn.execute("SELECT enabled FROM direct_accounts WHERE id = %s", (account_id,)).fetchone()
    return bool(row and row["enabled"])


class _AccountLoad:
    def __init__(self, user_id: int, account: dict, progress: Progress):
        self.user_id = user_id
        self.account_id = int(account["id"])
        self.connection_id = int(account["connection_id"])
        self.login = account["client_login"]
        self.changes_ts = account.get("changes_ts") or ""
        self.state: dict = dict(account.get("load_state") or {})
        self.initial = account.get("loaded_at") is None
        self.progress = progress
        self.plan: list[str] = []
        self.current = ""
        self._token = None

    @property
    def token(self):
        if self._token is None:
            self._token = get_connection_access_token(self.connection_id, self.user_id)
        return self._token

    # --- состояние ---

    def stage(self, name: str) -> dict:
        return self.state.setdefault(name, {})

    def save(self, *, loaded: bool = False, changes_ts: str | None = None) -> None:
        units = direct_api.last_units(self.login)
        with pool.connection() as conn:
            conn.execute(
                """
                UPDATE direct_accounts SET
                    load_state = %s,
                    loaded_at = CASE WHEN %s THEN NOW() ELSE loaded_at END,
                    changes_ts = COALESCE(%s, changes_ts),
                    units_left = COALESCE(%s, units_left),
                    units_limit = COALESCE(%s, units_limit),
                    units_at = CASE WHEN %s THEN NOW() ELSE units_at END
                WHERE id = %s
                """,
                (
                    Jsonb(self.state),
                    loaded,
                    changes_ts,
                    units["left"] if units else None,
                    units["limit"] if units else None,
                    units is not None,
                    self.account_id,
                ),
            )

    def begin(self, name: str, total: int = 0) -> dict:
        self.current = name
        stage = self.stage(name)
        stage.update(status="running", started_at=_now_iso(), done=0, total=total, error="")
        self.report(name, 0, total)
        self.save()
        return stage

    def report(self, name: str, done: int, total: int, text: str = "") -> None:
        """Прогресс задачи: этапы плана по 100 делений, внутри этапа — доля сделанного."""
        index = self.plan.index(name) if name in self.plan else 0
        part = int(STAGE_SCALE * done / total) if total else 0
        label = STAGE_TITLES[name] + (f": {done}/{total}" if total else "")
        self.progress(index * STAGE_SCALE + part, len(self.plan) * STAGE_SCALE, f"{self.login} · {text or label}")

    def finish(self, name: str, **extra) -> None:
        stage = self.stage(name)
        stage.update(status="ok", at=_now_iso(), error="", **extra)
        stage["done"] = stage.get("total") or stage.get("done") or 0
        self.current = ""
        self.save()

    def fail(self, code: str) -> None:
        if not self.current:
            return
        stage = self.stage(self.current)
        stage.update(status="stopped" if code == "stopped" else "error", error=code)
        self.save()

    def check_enabled(self) -> None:
        if not _account_enabled(self.account_id):
            raise _Stopped()

    # --- этапы ---

    def campaigns(self) -> list[int]:
        self.begin("campaigns")
        result = sync_account_campaigns(self.user_id, self.account_id)
        self.finish("campaigns", count=_int(result.get("campaigns")))
        return [int(x) for x in result.get("fresh") or []]

    def _campaign_rows(self) -> list[dict]:
        with pool.connection() as conn:
            return conn.execute(
                """
                SELECT id, campaign_id, state FROM direct_campaigns
                WHERE account_id = %s AND user_id = %s
                ORDER BY campaign_id
                """,
                (self.account_id, self.user_id),
            ).fetchall()

    def _changes_timestamp(self) -> str | None:
        """Метка времени Changes API до чтения структуры: следующая проверка — изменения после неё."""
        try:
            result = direct_api.call(self.token, self.login, "changes", "checkDictionaries", {})
        except YandexError as exc:
            if exc.code in FATAL_CODES:
                raise
            log.warning("changes timestamp %s: %s", self.login, exc.code)
            return None
        return str(result.get("Timestamp") or "") or None

    def _changed_campaigns(self) -> tuple[set[int], str] | None:
        if not self.changes_ts:
            return None
        try:
            result = direct_api.call(
                self.token, self.login, "changes", "checkCampaigns", {"Timestamp": self.changes_ts}
            )
        except YandexError as exc:
            if exc.code in FATAL_CODES:
                raise
            log.warning("changes.checkCampaigns %s: %s", self.login, exc.code)
            return None
        return parse_changed_campaigns(result), str(result.get("Timestamp") or "")

    def structure(self, rows: list[dict], *, resume: bool) -> None:
        stage = self.stage("structure")
        cursor = _int(stage.get("cursor")) if resume and stage.get("status") != "ok" else 0
        failed = list(stage.get("failed") or []) if cursor else []
        todo = [r for r in rows if int(r["campaign_id"]) > cursor]
        done = len(rows) - len(todo)
        self.begin("structure", total=len(rows))
        stage.update(done=done, cursor=cursor, failed=failed)
        for start in range(0, len(todo), STRUCTURE_CHUNK):
            self.check_enabled()
            chunk = todo[start : start + STRUCTURE_CHUNK]
            failed += self._structure_chunk_safe(chunk)
            done += len(chunk)
            stage.update(done=done, cursor=int(chunk[-1]["campaign_id"]), failed=failed[-50:])
            self.report("structure", done, len(rows), f"Структура: {done}/{len(rows)} кампаний")
            self.save()
        stage.pop("cursor", None)
        self.finish("structure", count=len(rows))

    def _structure_chunk_safe(self, chunk: list[dict]) -> list[int]:
        """Структура пачки кампаний; при сбое — по одной. Возвращает Id кампаний, которые не прочитались."""
        try:
            self._structure_chunk(chunk)
            return []
        except YandexError as exc:
            if exc.code in FATAL_CODES:
                raise
            if len(chunk) == 1:
                return [int(chunk[0]["campaign_id"])]
            first_error = exc
        failed = []
        for row in chunk:
            try:
                self._structure_chunk([row])
            except YandexError as exc:
                if exc.code in FATAL_CODES:
                    raise
                failed.append(int(row["campaign_id"]))
        if len(failed) == len(chunk):
            # Не прочиталась ни одна — дело не в кампаниях, а в кабинете.
            raise first_error
        return failed

    def _structure_chunk(self, chunk: list[dict]) -> None:
        ids = [int(r["campaign_id"]) for r in chunk]
        row_ids = [int(r["id"]) for r in chunk]
        selection = {"SelectionCriteria": {"CampaignIds": ids}}
        with pool.connection() as conn:
            started = conn.execute("SELECT NOW() AS t").fetchone()["t"]
        groups = parse_adgroups(
            direct_api.get_all(
                self.token, self.login, "adgroups", {**selection, "FieldNames": ADGROUP_FIELDS}, "AdGroups"
            )
        )
        ads = parse_ads(
            direct_api.get_all(
                self.token,
                self.login,
                "ads",
                {**selection, "FieldNames": AD_FIELDS, "TextAdFieldNames": TEXT_AD_FIELDS},
                "Ads",
            )
        )
        keywords = parse_keywords(
            direct_api.get_all(
                self.token, self.login, "keywords", {**selection, "FieldNames": KEYWORD_FIELDS}, "Keywords"
            )
        )
        row_by_campaign = {int(r["campaign_id"]): int(r["id"]) for r in chunk}
        for campaign_id, row_id in row_by_campaign.items():
            upsert_adgroups(
                self.user_id, self.account_id, row_id, [g for g in groups if g["campaign_id"] == campaign_id]
            )
        with pool.connection() as conn:
            group_rows = conn.execute(
                "SELECT id, adgroup_id FROM direct_adgroups WHERE account_id = %s AND campaign_row_id = ANY(%s)",
                (self.account_id, row_ids),
            ).fetchall()
        group_map = {int(g["adgroup_id"]): int(g["id"]) for g in group_rows}
        upsert_ads(self.user_id, self.account_id, group_map, ads)
        upsert_keywords(self.user_id, self.account_id, group_map, keywords)
        # Чего в Директе больше нет — удаляем; записанное после начала чтения (правки из сервиса) не трогаем.
        with pool.connection() as conn:
            conn.execute(
                """
                DELETE FROM direct_adgroups
                WHERE account_id = %s AND campaign_row_id = ANY(%s)
                  AND NOT (adgroup_id = ANY(%s)) AND synced_at < %s
                """,
                (self.account_id, row_ids, [g["adgroup_id"] for g in groups], started),
            )
            for table, id_column, seen in (
                ("direct_ads", "ad_id", [a["ad_id"] for a in ads]),
                ("direct_keywords", "keyword_id", [k["keyword_id"] for k in keywords]),
            ):
                conn.execute(
                    f"""
                    DELETE FROM {table} t USING direct_adgroups g
                    WHERE t.adgroup_row_id = g.id AND g.account_id = %s AND g.campaign_row_id = ANY(%s)
                      AND NOT (t.{id_column} = ANY(%s)) AND t.synced_at < %s
                    """,
                    (self.account_id, row_ids, seen, started),
                )

    def stats(self, days: int) -> None:
        date_to = today_msk()
        date_from = yesterday_msk() - timedelta(days=days - 1)
        self.begin("stats")
        self.report("stats", 0, 0, f"Статистика за {days} дн.")
        try:
            raw = fetch_report(
                self.token,
                self.login,
                report_type="CAMPAIGN_PERFORMANCE_REPORT",
                field_names=["Date", "CampaignId", "Impressions", "Clicks", "Cost", "Conversions", "Revenue"],
                date_from=date_from,
                date_to=date_to,
            )
        except YandexError as exc:
            set_account_stats_status(self.account_id, exc.code)
            raise
        rows = parse_campaign_day_rows(raw)
        replace_direct_stats(self.user_id, self.account_id, date_from, date_to, rows)
        set_account_stats_status(self.account_id, "")
        self.finish("stats", count=len(rows), date_from=date_from.isoformat(), date_to=date_to.isoformat())

    def queries(self) -> None:
        date_to = yesterday_msk()
        date_from = date_to - timedelta(days=HISTORY_DAYS - 1)
        self.begin("queries", total=3)
        self.report("queries", 0, 3, "Поисковые запросы")
        raw = fetch_report(
            self.token,
            self.login,
            report_type="SEARCH_QUERY_PERFORMANCE_REPORT",
            field_names=[
                "Query", "CampaignId", "AdGroupId", "Criterion", "Impressions", "Clicks", "Cost", "Conversions",
            ],
            date_from=date_from,
            date_to=date_to,
        )
        by_campaign: dict[int, list[dict]] = {}
        for item in raw:
            by_campaign.setdefault(_int(item.get("CampaignId")), []).append(item)
        with pool.connection() as conn:
            stored = {
                int(r["campaign_row_id"])
                for r in conn.execute(
                    "SELECT DISTINCT campaign_row_id FROM direct_search_queries WHERE account_id = %s",
                    (self.account_id,),
                )
            }
            group_map = {
                int(r["adgroup_id"]): int(r["id"])
                for r in conn.execute(
                    "SELECT id, adgroup_id FROM direct_adgroups WHERE account_id = %s", (self.account_id,)
                )
            }
        count = 0
        for row in self._campaign_rows():
            items = by_campaign.get(int(row["campaign_id"]))
            if items is None and int(row["id"]) not in stored:
                continue
            count += upsert_search_queries(
                self.user_id,
                account_id=self.account_id,
                campaign_row_id=int(row["id"]),
                date_from=date_from,
                date_to=date_to,
                items=aggregate_query_rows(items or []),
                adgroup_row_by_direct_id=group_map,
            )
        self.check_enabled()
        self.stage("queries")["done"] = 1
        self.report("queries", 1, 3, "Статистика групп")
        adgroups = _agg_metrics(
            fetch_report(
                self.token,
                self.login,
                report_type="ADGROUP_PERFORMANCE_REPORT",
                field_names=["AdGroupId", "Impressions", "Clicks", "Cost", "Conversions"],
                date_from=date_from,
                date_to=date_to,
            ),
            "AdGroupId",
        )
        self.stage("queries")["done"] = 2
        self.report("queries", 2, 3, "Статистика фраз")
        criteria = fetch_report(
            self.token,
            self.login,
            report_type="CRITERIA_PERFORMANCE_REPORT",
            field_names=["CriteriaId", "CriteriaType", "Impressions", "Clicks", "Cost", "Conversions"],
            date_from=date_from,
            date_to=date_to,
            filters=[{"Field": "CriteriaType", "Operator": "EQUALS", "Values": ["KEYWORD"]}],
        )
        keywords = _agg_metrics([r for r in criteria if str(r.get("CriteriaType") or "") == "KEYWORD"], "CriteriaId")
        write_account_period_stats(
            self.user_id,
            self.account_id,
            date_from=date_from,
            date_to=date_to,
            adgroup_metrics=adgroups,
            keyword_metrics=keywords,
        )
        self.finish("queries", count=count, adgroups=len(adgroups), keywords=len(keywords))

    # --- сценарии ---

    def run(self) -> dict:
        initial = self.initial
        plan = ["campaigns"]
        for name, every in (("structure", STRUCTURE_EVERY), ("stats", STATS_EVERY), ("queries", QUERIES_EVERY)):
            # При актуализации структура проверяется каждый раз: что менялось, скажет Changes API.
            if (name == "structure" and not initial) or is_due(self.stage(name), every, initial=initial):
                plan.append(name)
        self.plan = plan

        fresh = self.campaigns()
        self.check_enabled()
        if "structure" in plan:
            rows = [r for r in self._campaign_rows() if (r["state"] or "") != "ARCHIVED"]
            if initial:
                changes_ts = self._changes_timestamp()
                self.structure(rows, resume=True)
                self.save(changes_ts=changes_ts)
            else:
                self._refresh_structure(rows, fresh)
            self.check_enabled()
        if "stats" in plan:
            self.stats(stats_days(self.stage("stats"), initial=initial))
            self.check_enabled()
        if "queries" in plan:
            self.queries()
        loaded = initial and all(self.stage(s).get("status") == "ok" for s in STAGES)
        self.save(loaded=loaded)
        return {"status": "ok", "mode": "initial" if initial else "refresh", "stages": plan}

    def _refresh_structure(self, rows: list[dict], fresh: list[int]) -> None:
        stage = self.stage("structure")
        units = direct_api.last_units(self.login)
        if units and units["left"] < UNITS_RESERVE:
            # Баллы на исходе: метку Changes не двигаем — изменения подхватим в следующий раз.
            stage["skipped"] = "units"
            self.save()
            return
        stage.pop("skipped", None)
        # Новые кампании и те, что в прошлый раз не прочитались, перечитываем в любом случае.
        retry = {int(x) for x in stage.get("failed") or []}
        changed = self._changed_campaigns()
        if changed is None:
            if stage.get("status") != "ok" or is_due(stage, STRUCTURE_EVERY, initial=False):
                # Changes API недоступен — раз в сутки перечитываем всё.
                changes_ts = self._changes_timestamp()
                self.structure(rows, resume=False)
                self.save(changes_ts=changes_ts)
                return
            rows = [r for r in rows if int(r["id"]) in fresh or int(r["campaign_id"]) in retry]
            if rows:
                self.structure(rows, resume=False)
            return
        campaign_ids, changes_ts = changed
        campaign_ids |= retry
        picked = [r for r in rows if int(r["campaign_id"]) in campaign_ids or int(r["id"]) in fresh]
        if picked:
            self.structure(picked, resume=False)
        else:
            stage.update(status="ok", at=_now_iso(), error="")
        self.save(changes_ts=changes_ts or None)


def run_account(user_id: int, account_id: int, progress: Progress) -> dict:
    """Задача load_account: первая загрузка кабинета или его актуализация."""
    with pool.connection() as conn:
        account = conn.execute(
            """
            SELECT id, connection_id, client_login, enabled, load_state, loaded_at, changes_ts
            FROM direct_accounts WHERE id = %s AND user_id = %s
            """,
            (account_id, user_id),
        ).fetchone()
    if account is None:
        raise YandexError("not-found")
    if not account["enabled"]:
        return {"status": "disabled"}
    load = _AccountLoad(user_id, account, progress)
    try:
        return load.run()
    except _Stopped:
        load.fail("stopped")
        return {"status": "stopped"}
    except YandexError as exc:
        load.fail(exc.code)
        if exc.code == "disabled":
            return {"status": "stopped"}
        if exc.code == "units":
            # Баллы восстановятся в течение часа-суток: продолжит плановая актуализация.
            return {"status": "units", "stage": load.current}
        raise
    except Exception:
        load.fail("internal")
        raise


def reset_account_load(user_id: int, account_id: int) -> bool:
    """Полная перезагрузка кабинета: этапы с нуля. Идущую загрузку не трогаем — она и так грузит."""
    with pool.connection() as conn:
        running = conn.execute(
            """
            SELECT 1 FROM jobs
            WHERE user_id = %s AND kind = 'load_account' AND status = 'running'
              AND payload->>'account_id' = %s
            """,
            (user_id, str(account_id)),
        ).fetchone()
        if running:
            return False
        row = conn.execute(
            """
            UPDATE direct_accounts SET load_state = '{}'::jsonb, loaded_at = NULL, changes_ts = ''
            WHERE id = %s AND user_id = %s RETURNING id
            """,
            (account_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return True


# --- Метрика ---


def run_counter(user_id: int, counter_row_id: int, progress: Progress) -> dict:
    """Задача load_counter: цели и визиты счётчика — за 30 дней при первой загрузке, потом за неделю."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, name, counter_id, enabled, load_state, loaded_at
            FROM metrika_counters WHERE id = %s AND user_id = %s
            """,
            (counter_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row["enabled"]:
        return {"status": "disabled"}
    state: dict = dict(row["load_state"] or {})
    initial = row["loaded_at"] is None
    title = row["name"] or str(row["counter_id"])

    def save(loaded: bool = False) -> None:
        with pool.connection() as conn:
            conn.execute(
                """
                UPDATE metrika_counters SET load_state = %s,
                    loaded_at = CASE WHEN %s THEN NOW() ELSE loaded_at END
                WHERE id = %s
                """,
                (Jsonb(state), loaded, counter_row_id),
            )

    plan = []
    if is_due(state.get("goals"), GOALS_EVERY, initial=initial):
        plan.append("goals")
    if is_due(state.get("stats"), STATS_EVERY, initial=initial):
        plan.append("stats")
    current = ""
    try:
        for index, name in enumerate(plan):
            current = name
            stage = state.setdefault(name, {})
            stage.update(status="running", started_at=_now_iso(), error="")
            save()
            if name == "goals":
                progress(index, len(plan), f"{title} · Цели")
                result = sync_counter_goals(user_id, counter_row_id)
                stage.update(status="ok", at=_now_iso(), count=_int(result.get("goals")))
            else:
                days = stats_days(stage, initial=initial)
                progress(index, len(plan), f"{title} · Визиты и цели за {days} дн.")
                date_to = yesterday_msk()
                date_from = date_to - timedelta(days=days - 1)
                result = sync_counter_stats_range(user_id, counter_row_id, date_from, date_to)
                stage.update(
                    status="ok",
                    at=_now_iso(),
                    count=_int(result.get("days")),
                    date_from=date_from.isoformat(),
                    date_to=date_to.isoformat(),
                )
            save()
    except YandexError as exc:
        state.setdefault(current, {}).update(status="error", error=exc.code)
        save()
        raise
    except Exception:
        state.setdefault(current, {}).update(status="error", error="internal")
        save()
        raise
    loaded = initial and all((state.get(s) or {}).get("status") == "ok" for s in COUNTER_STAGES)
    save(loaded=loaded)
    return {"status": "ok", "mode": "initial" if initial else "refresh", "stages": plan}


# --- обзор для интерфейса ---


def _stage_out(state: dict, names: tuple[str, ...]) -> list[dict]:
    out = []
    for name in names:
        stage = state.get(name) or {}
        out.append(
            {
                "key": name,
                "title": STAGE_TITLES[name],
                "status": stage.get("status") or "pending",
                "at": stage.get("at"),
                "done": _int(stage.get("done")),
                "total": _int(stage.get("total")),
                "count": _int(stage.get("count")),
                "error": stage.get("error") or "",
                "failed": len(stage.get("failed") or []),
                "skipped": stage.get("skipped") or "",
            }
        )
    return out


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, (datetime, date)) else None


def account_load_out(row: dict) -> dict:
    """Состояние загрузки кабинета для карточки и шапки."""
    return {
        "loaded_at": _iso(row.get("loaded_at")),
        "stages": _stage_out(row.get("load_state") or {}, STAGES),
        "units": {
            "left": row.get("units_left"),
            "limit": row.get("units_limit"),
            "at": _iso(row.get("units_at")),
        }
        if row.get("units_limit")
        else None,
    }


def counter_load_out(row: dict) -> dict:
    return {
        "loaded_at": _iso(row.get("loaded_at")),
        "stages": _stage_out(row.get("load_state") or {}, COUNTER_STAGES),
    }


def load_overview(user_id: int, *, account_ids: list[int] | None = None, counter_ids: list[int] | None = None) -> dict:
    """Кабинеты и счётчики с этапами загрузки и активные задачи загрузки (для шапки и пространства)."""
    account_filter = "" if account_ids is None else "AND id = ANY(%(accounts)s)"
    counter_filter = "" if counter_ids is None else "AND id = ANY(%(counters)s)"
    params = {"user": user_id, "accounts": account_ids or [], "counters": counter_ids or []}
    with pool.connection() as conn:
        accounts = conn.execute(
            f"""
            SELECT id, client_login, name, enabled, load_state, loaded_at, units_left, units_limit, units_at
            FROM direct_accounts WHERE user_id = %(user)s {account_filter}
            ORDER BY name, client_login
            """,
            params,
        ).fetchall()
        counters = conn.execute(
            f"""
            SELECT id, counter_id, name, enabled, load_state, loaded_at
            FROM metrika_counters WHERE user_id = %(user)s {counter_filter}
            ORDER BY name, counter_id
            """,
            params,
        ).fetchall()
        jobs = conn.execute(
            """
            SELECT id, kind, status, payload, progress_done, progress_total, progress_text, error_code
            FROM jobs
            WHERE user_id = %s AND status IN ('queued', 'running')
              AND kind IN ('load_account', 'load_counter', 'sync_connection')
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()
    active_accounts = {
        _int((j["payload"] or {}).get("account_id")): j for j in jobs if j["kind"] == "load_account"
    }
    active_counters = {
        _int((j["payload"] or {}).get("counter_row_id")): j for j in jobs if j["kind"] == "load_counter"
    }
    # Счётчик, видимый из нескольких логинов, грузится через одну строку — остальные не ждём.
    canonical = {int(c["id"]) for c in canonical_counters(user_id)}

    def _job(job: dict | None) -> dict | None:
        if job is None:
            return None
        return {
            "id": job["id"],
            "status": job["status"],
            "done": job["progress_done"],
            "total": job["progress_total"],
            "text": job["progress_text"],
            "error_code": job["error_code"],
        }

    return {
        "accounts": [
            {
                "id": a["id"],
                "client_login": a["client_login"],
                "name": a["name"],
                "enabled": a["enabled"],
                **account_load_out(a),
                "job": _job(active_accounts.get(int(a["id"]))),
            }
            for a in accounts
        ],
        "counters": [
            {
                "id": c["id"],
                "counter_id": c["counter_id"],
                "name": c["name"],
                "enabled": c["enabled"],
                "canonical": int(c["id"]) in canonical,
                **counter_load_out(c),
                "job": _job(active_counters.get(int(c["id"]))),
            }
            for c in counters
        ],
        "connections_syncing": sum(1 for j in jobs if j["kind"] == "sync_connection"),
    }
