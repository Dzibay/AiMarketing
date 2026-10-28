"""
Статистика заголовков: какие заголовки приносят конверсии.

API Директа не отдаёт статистику по заголовкам, только по объявлениям. Поэтому точные цифры
даёт объявление с одним заголовком (текстовое или комбинаторное с одним вариантом) — тесты
создают по одному объявлению на заголовок. Комбинаторные объявления с несколькими заголовками
показываем отдельно: их цифры общие на все варианты. Для старых комбинаторных объявлений можно
загрузить таблицу заголовков из Мастера отчётов (CSV или XLSX) — её строки тоже точные.
Лучший заголовок — больше конверсий по ключевой цели сайта, затем выше конверсия из клика.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import re
import zipfile
from xml.etree import ElementTree

from psycopg.types.json import Jsonb

from app.db import pool
from app.yandex import YandexError

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_UNZIPPED = 40 * 1024 * 1024
MAX_ROWS = 5000
MAX_REPORTS = 20
# Достаточно данных, чтобы сравнивать: 3 конверсии или 100 кликов.
ENOUGH_CONVERSIONS = 3
ENOUGH_CLICKS = 100

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


# --- Разбор файла Мастера отчётов --------------------------------------------------------------


def _number(value) -> float:
    text = str(value or "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if text in ("", "-", "--", "—"):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _column(header: list[str], goal_id: int | None) -> dict[str, int]:
    names = [re.sub(r"\s+", " ", str(h or "")).strip().lower() for h in header]
    cols: dict[str, int] = {}

    def first(pred) -> int | None:
        return next((i for i, n in enumerate(names) if n and pred(n)), None)

    cols_raw = {
        "title": first(lambda n: n in ("заголовок", "заголовок 1", "title") or (n.startswith("заголовок") and "2" not in n and "доп" not in n)),
        "impressions": first(lambda n: n.startswith("показ") or n.startswith("impressions")),
        "clicks": first(lambda n: n.startswith("клик") or n.startswith("clicks")),
        "cost": first(lambda n: n.startswith("расход") or n.startswith("cost")),
        "campaign_id": first(lambda n: n in ("№ кампании", "id кампании", "номер кампании", "campaignid", "campaign id")),
    }
    conv = [i for i, n in enumerate(names) if n.startswith("конверсии") or n == "conversions"]
    if goal_id is not None:
        conv = [i for i in conv if str(goal_id) in names[i]] or conv
    cols_raw["conversions"] = conv[0] if conv else None
    for key, idx in cols_raw.items():
        if idx is not None:
            cols[key] = idx
    return cols


def parse_rows(table: list[list[str]], goal_id: int | None = None) -> list[dict]:
    """Таблица (строки ячеек) → [{title, impressions, clicks, cost_kopecks, conversions, campaign_id}].
    Строку заголовков ищем сами: над ней у Мастера отчётов название и период."""
    header_at, cols = None, {}
    for i, row in enumerate(table[:30]):
        found = _column(row, goal_id)
        if "title" in found and ("clicks" in found or "impressions" in found):
            header_at, cols = i, found
            break
    if header_at is None:
        raise YandexError("headline-file", "Не нашли столбцы «Заголовок» и «Клики» — выгрузите отчёт Мастера с группировкой по заголовку")
    if "conversions" not in cols:
        raise YandexError("headline-file", "В отчёте нет столбца «Конверсии» — добавьте его в Мастере отчётов")
    merged: dict[tuple, dict] = {}
    for row in table[header_at + 1 :]:
        def cell(key: str) -> str:
            idx = cols.get(key)
            return str(row[idx]).strip() if idx is not None and idx < len(row) and row[idx] is not None else ""

        title = re.sub(r"\s+", " ", cell("title")).strip()
        if not title or title.lower() in ("итого", "всего", "total", "-", "--"):
            continue
        cid = int(_number(cell("campaign_id"))) or None
        item = merged.setdefault(
            (title, cid),
            {"title": title[:200], "campaign_id": cid, "impressions": 0, "clicks": 0, "cost_kopecks": 0, "conversions": 0},
        )
        item["impressions"] += int(_number(cell("impressions")))
        item["clicks"] += int(_number(cell("clicks")))
        item["cost_kopecks"] += int(round(_number(cell("cost")) * 100))
        item["conversions"] += int(round(_number(cell("conversions"))))
        if len(merged) >= MAX_ROWS:
            break
    rows = [r for r in merged.values() if r["impressions"] or r["clicks"]]
    if not rows:
        raise YandexError("headline-file", "В отчёте нет строк с показами")
    return rows


def _xlsx_table(data: bytes) -> list[list[str]]:
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise YandexError("headline-file", "Файл XLSX повреждён") from exc
    if sum(i.file_size for i in book.infolist()) > MAX_UNZIPPED:
        raise YandexError("headline-file", "Файл слишком большой")
    names = book.namelist()
    strings: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", _NS):
            strings.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    if not sheets:
        raise YandexError("headline-file", "В файле нет листов")
    sheets.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    root = ElementTree.fromstring(book.read(sheets[0]))
    table: list[list[str]] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells: list[str] = []
        for c in row.findall("m:c", _NS):
            ref = c.get("r") or ""
            letters = re.match(r"[A-Z]+", ref)
            if letters:
                idx = 0
                for ch in letters.group(0):
                    idx = idx * 26 + (ord(ch) - 64)
                while len(cells) < idx - 1:
                    cells.append("")
            kind = c.get("t")
            if kind == "s":
                v = c.find("m:v", _NS)
                n = int(v.text) if v is not None and (v.text or "").isdigit() else -1
                value = strings[n] if 0 <= n < len(strings) else ""
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            else:
                v = c.find("m:v", _NS)
                value = v.text if v is not None and v.text else ""
            cells.append(value)
        table.append(cells)
        if len(table) > MAX_ROWS + 50:
            break
    return table


def _csv_table(data: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise YandexError("headline-file", "Не удалось прочитать файл: сохраните его в UTF-8 или Windows-1251")
    sample = "\n".join(text.splitlines()[:40])
    delimiter = max((";", "\t", ","), key=sample.count)
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)][: MAX_ROWS + 50]


def parse_file(filename: str, data_b64: str, goal_id: int | None = None) -> list[dict]:
    raw = str(data_b64 or "")
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise YandexError("headline-file", "Файл повреждён") from exc
    if not data:
        raise YandexError("headline-file", "Файл пустой")
    if len(data) > MAX_FILE_BYTES:
        raise YandexError("headline-file", "Файл больше 5 МБ")
    is_xlsx = data[:2] == b"PK"
    if not is_xlsx and str(filename or "").lower().endswith((".xls",)):
        raise YandexError("headline-file", "Старый формат XLS не поддерживается — выгрузите XLSX или CSV")
    table = _xlsx_table(data) if is_xlsx else _csv_table(data)
    return parse_rows(table, goal_id)


# --- Хранение ---------------------------------------------------------------------------------


def _report_out(row: dict, campaigns: dict[int, str] | None = None) -> dict:
    rows = list(row.get("rows") or [])
    return {
        "id": row["id"],
        "filename": row.get("filename") or "",
        "campaign_row_id": row.get("campaign_row_id"),
        "campaign_name": (campaigns or {}).get(row.get("campaign_row_id")) or "",
        "headlines": len(rows),
        "conversions": sum(int(r.get("conversions") or 0) for r in rows),
        "uploaded_at": row["uploaded_at"].isoformat() if row.get("uploaded_at") else None,
    }


def save_report(user_id: int, workspace_id: int, *, filename: str, data: str, campaign_row_id: int | None = None) -> dict:
    from app.workspaces import require_workspace, require_workspace_campaign

    ws = require_workspace(user_id, workspace_id)
    if campaign_row_id:
        require_workspace_campaign(user_id, workspace_id, int(campaign_row_id))
    rows = parse_file(filename, data, int(ws["goal_id"]) if ws.get("goal_id") else None)
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO headline_reports (user_id, workspace_id, campaign_row_id, filename, rows)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (user_id, workspace_id, campaign_row_id or None, str(filename or "")[:200], Jsonb(rows)),
        ).fetchone()
        # Старые загрузки сверх лимита — убираем.
        conn.execute(
            """
            DELETE FROM headline_reports WHERE workspace_id = %s AND user_id = %s AND id NOT IN (
                SELECT id FROM headline_reports WHERE workspace_id = %s AND user_id = %s
                ORDER BY uploaded_at DESC, id DESC LIMIT %s
            )
            """,
            (workspace_id, user_id, workspace_id, user_id, MAX_REPORTS),
        )
    return _report_out(row)


def list_reports(user_id: int, workspace_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT r.*, c.name AS campaign_name FROM headline_reports r
            LEFT JOIN direct_campaigns c ON c.id = r.campaign_row_id
            WHERE r.workspace_id = %s AND r.user_id = %s
            ORDER BY r.uploaded_at DESC, r.id DESC
            """,
            (workspace_id, user_id),
        ).fetchall()
    return [_report_out(r, {r.get("campaign_row_id"): r.get("campaign_name")}) for r in rows]


def delete_report(user_id: int, workspace_id: int, report_id: int) -> None:
    with pool.connection() as conn:
        cur = conn.execute(
            "DELETE FROM headline_reports WHERE id = %s AND workspace_id = %s AND user_id = %s",
            (report_id, workspace_id, user_id),
        )
    if not cur.rowcount:
        raise YandexError("not-found")


def report_rows(user_id: int, workspace_id: int, campaigns: list[dict]) -> list[dict]:
    """Строки загруженных отчётов, относящиеся к выбранным кампаниям.
    campaigns — [{id, campaign_id}]; строка без кампании и отчёт без привязки — ко всем
    (campaign_row_id = None)."""
    row_ids = {int(c["id"]) for c in campaigns}
    by_direct = {int(c["campaign_id"]): int(c["id"]) for c in campaigns}
    direct_ids = set(by_direct)
    with pool.connection() as conn:
        reports = conn.execute(
            "SELECT id, campaign_row_id, filename, rows FROM headline_reports WHERE workspace_id = %s AND user_id = %s",
            (workspace_id, user_id),
        ).fetchall()
    out = []
    for rep in reports:
        bound = rep.get("campaign_row_id")
        if bound and int(bound) not in row_ids:
            continue
        for r in rep.get("rows") or []:
            cid = r.get("campaign_id")
            if cid and not bound and int(cid) not in direct_ids:
                continue
            row_id = int(bound) if bound else by_direct.get(int(cid)) if cid else None
            out.append({**r, "campaign_row_id": row_id, "report_id": rep["id"], "filename": rep.get("filename") or ""})
    return out


# --- Сводка -----------------------------------------------------------------------------------


def _metrics(item: dict) -> dict:
    clicks, imp, cost, conv = item["clicks"], item["impressions"], item["cost_kopecks"], item["conversions"]
    item["ctr"] = round(clicks / imp * 100, 2) if imp else None
    item["cr"] = round(conv / clicks * 100, 2) if clicks else None
    # Без расхода (в выгрузке нет столбца) CPA неизвестна, а не ноль.
    item["cpa_rub"] = round(cost / conv / 100, 2) if conv and cost else None
    item["cost_rub"] = round(cost / 100, 2)
    item["enough"] = conv >= ENOUGH_CONVERSIONS or clicks >= ENOUGH_CLICKS
    return item


def _key(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def summarize(ads: list[dict], uploaded: list[dict] | None = None) -> dict:
    """
    ads — строки ad_report (с total и titles). Возвращает
    {"headlines": [...точные, лучшие сверху], "mixed": [...объявления с несколькими заголовками]}.
    Отчёт Мастера уже включает все объявления своей кампании, поэтому объявления кампаний,
    покрытых загрузкой (строка без кампании покрывает все), в сводку не идут — иначе двойной счёт.
    """
    exact: dict[str, dict] = {}
    mixed: list[dict] = []
    uploaded = uploaded or []
    covered = {r.get("campaign_row_id") for r in uploaded}

    def add(title: str, stat: dict, source: str, ref: dict) -> None:
        item = exact.setdefault(
            _key(title),
            {"title": title, "sources": [], "ads": [], "impressions": 0, "clicks": 0, "cost_kopecks": 0, "conversions": 0},
        )
        for k in ("impressions", "clicks", "cost_kopecks", "conversions"):
            item[k] += int(stat.get(k) or 0)
        if source not in item["sources"]:
            item["sources"].append(source)
        if ref:
            item["ads"].append(ref)

    for ad in ads:
        titles = [t for t in ad.get("titles") or [ad.get("title")] if t]
        total = ad.get("total") or {}
        if not titles or not (total.get("impressions") or total.get("clicks")):
            continue
        if None in covered or ad.get("campaign_id") in covered:
            continue
        ref = {"ad_row_id": ad.get("ad_row_id"), "ad_id": ad.get("ad_id"), "campaign_row_id": ad.get("campaign_id"), "adgroup_name": ad.get("adgroup_name") or ""}
        if len(titles) == 1:
            add(titles[0], total, "ads", ref)
        else:
            mixed.append(
                _metrics(
                    {
                        **ref,
                        "titles": titles,
                        "impressions": int(total.get("impressions") or 0),
                        "clicks": int(total.get("clicks") or 0),
                        "cost_kopecks": int(total.get("cost_kopecks") or 0),
                        "conversions": int(total.get("conversions") or 0),
                    }
                )
            )
    for r in uploaded:
        add(r["title"], r, "report", {})
    headlines = [_metrics(i) for i in exact.values()]
    headlines.sort(key=lambda h: (-h["conversions"], -(h["cr"] or 0), -h["clicks"], -h["impressions"]))
    for n, h in enumerate(headlines, 1):
        h["rank"] = n
        h["ads"] = h["ads"][:10]
    mixed.sort(key=lambda m: (-m["conversions"], -m["clicks"]))
    return {"headlines": headlines, "mixed": mixed}


def headline_stats(user_id: int, workspace_id: int, campaign_row_ids: list[int] | None = None, days: int = 30, *, limit: int = 40) -> dict:
    """Для ИИ и экрана: заголовки выбранных кампаний — точные (один заголовок в объявлении или отчёт
    Мастера) и комбинаторные объявления с общими цифрами."""
    from app.ad_stats import ad_report
    from app.slices import _campaigns
    from app.workspaces import require_workspace

    ws = require_workspace(user_id, workspace_id)
    campaigns = _campaigns(user_id, workspace_id, campaign_row_ids)
    report = ad_report(user_id, {"workspace_id": workspace_id, "campaign_ids": [c["id"] for c in campaigns], "days": days})
    summary = summarize(report["ads"], report_rows(user_id, workspace_id, campaigns))
    return {
        "goal_id": ws.get("goal_id"),
        "goal_name": ws.get("goal_name") or "",
        "period": report["period"],
        "campaigns": [{"id": c["id"], "name": c["name"]} for c in campaigns],
        "headlines_total": len(summary["headlines"]),
        "headlines": summary["headlines"][:limit],
        "mixed": summary["mixed"][:limit],
        "errors": report.get("errors") or [],
        "note": (
            "headlines — точная статистика заголовка: объявления с одним заголовком и строки из отчёта Мастера. "
            "mixed — комбинаторные объявления: цифры общие на все их заголовки, победителя внутри них не видно. "
            "sources=report — цифры из загруженного отчёта Мастера за период его выгрузки (кампания из него "
            "считается только по отчёту). "
            f"enough=false — мало данных (меньше {ENOUGH_CONVERSIONS} конверсий и {ENOUGH_CLICKS} кликов)."
        ),
    }
