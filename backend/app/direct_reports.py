"""Сервис Reports API Директа: отчёт по всему кабинету с ожиданием офлайн-построения."""

from __future__ import annotations

import csv
import io
import logging
import time
import uuid
from datetime import date

import httpx

from app import direct_api
from app.yandex import YandexError, direct_api_error

log = logging.getLogger("app.direct_reports")

# Большой кабинет за месяц Директ строит офлайн минутами: ждём, сколько он просит в retryIn.
DEADLINE_SECONDS = 15 * 60
RETRY_MIN_SECONDS = 2
RETRY_MAX_SECONDS = 60
# Очередь офлайн-отчётов клиента переполнена — это пройдёт, повтор уместен.
QUEUE_FULL_CODES = frozenset({9000})


def _retry_in(response: httpx.Response) -> int:
    try:
        seconds = int(response.headers.get("retryIn") or 5)
    except ValueError:
        seconds = 5
    return max(RETRY_MIN_SECONDS, min(seconds, RETRY_MAX_SECONDS))


def fetch_report(
    token,
    client_login: str,
    *,
    report_type: str,
    field_names: list[str],
    date_from: date,
    date_to: date,
    filters: list[dict] | None = None,
    deadline_seconds: float = DEADLINE_SECONDS,
) -> list[dict]:
    """Строки TSV-отчёта (ключи — FieldNames). Ошибка Директа — YandexError."""
    headers = {
        **direct_api.headers(token, client_login),
        "processingMode": "auto",
        "returnMoneyInMicros": "false",
        "skipReportHeader": "true",
        "skipColumnHeader": "false",
        "skipReportSummary": "true",
    }
    criteria: dict = {"DateFrom": date_from.isoformat(), "DateTo": date_to.isoformat()}
    if filters:
        criteria["Filter"] = filters
    body = {
        "params": {
            "SelectionCriteria": criteria,
            "FieldNames": field_names,
            # С тем же именем и параметрами Директ отдаёт отчёт из кэша — а данные за последние
            # дни ещё дозаписываются (конверсии), поэтому имя всегда новое.
            "ReportName": f"aim_{report_type[:30]}_{uuid.uuid4().hex}",
            "ReportType": report_type,
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
            "IncludeDiscount": "NO",
        }
    }
    url = direct_api.v5_url(token, "reports")
    deadline = time.monotonic() + deadline_seconds
    while True:
        try:
            response = direct_api.post_json(url, headers=headers, body=body, timeout=180.0, attempts=3)
        except httpx.HTTPError as exc:
            log.warning("отчёт %s %s: %s", report_type, client_login, exc)
            raise YandexError("direct") from exc
        if response.status_code == 200:
            break
        if response.status_code in (201, 202) or response.status_code in direct_api.TRANSIENT_STATUS:
            wait = _retry_in(response)
            if time.monotonic() + wait > deadline:
                raise YandexError("direct", "Директ не успел построить отчёт — повторим позже")
            time.sleep(wait)
            continue
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}
        err = (payload or {}).get("error") or {}
        log.warning("отчёт %s %s HTTP %s %s", report_type, client_login, response.status_code, err or response.text[:300])
        if str(err.get("error_code") or "").isdigit() and int(err["error_code"]) in QUEUE_FULL_CODES:
            raise YandexError("direct", str(err.get("error_detail") or err.get("error_string") or "") or None)
        raise direct_api_error(payload, response.status_code)
    text = response.text.strip()
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))
