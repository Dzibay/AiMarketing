"""Календарь по Москве: сервер живёт в UTC, а сутки Директа и утро маркетолога — московские."""

from datetime import date, datetime, timedelta, timezone

# Перехода на летнее время нет с 2014 года, фиксированного смещения достаточно (и tzdata не нужна).
MSK = timezone(timedelta(hours=3), "MSK")


def now_msk() -> datetime:
    return datetime.now(MSK)


def today_msk() -> date:
    return now_msk().date()


def yesterday_msk() -> date:
    return today_msk() - timedelta(days=1)
