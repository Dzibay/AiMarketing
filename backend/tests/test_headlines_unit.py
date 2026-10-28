"""Статистика заголовков без БД: разбор отчёта Мастера (CSV, XLSX) и сводка без двойного счёта."""

import base64
import io
import zipfile

import pytest

from app import headlines
from app.yandex import YandexError

HEADER = ["Заголовок", "Показы", "Клики", "Расход (руб.)", "Конверсии (5001)", "Конверсии (5002)", "№ кампании"]


def test_parse_rows_finds_header_and_merges_duplicates():
    table = [
        ["Отчёт по заголовкам"],
        ["Период: 01.10.2026 — 27.10.2026"],
        HEADER,
        ["Подключение за 5 минут", "1 000", "50", "1 234,50", "3", "1", "111"],
        ["Подключение  за 5 минут", "100", "5", "10", "1", "0", "111"],
        ["Без звонков", "200", "0", "0", "-", "", ""],
        ["Нет показов", "0", "0", "0", "0", "0", ""],
        ["Итого", "1300", "55", "1244,5", "4", "1", ""],
    ]
    rows = headlines.parse_rows(table, goal_id=5002)
    assert rows == [
        {"title": "Подключение за 5 минут", "campaign_id": 111, "impressions": 1100, "clicks": 55,
         "cost_kopecks": 124450, "conversions": 1},
        {"title": "Без звонков", "campaign_id": None, "impressions": 200, "clicks": 0, "cost_kopecks": 0,
         "conversions": 0},
    ]
    assert headlines.parse_rows(table)[0]["conversions"] == 4  # без цели — первая колонка конверсий


@pytest.mark.parametrize(
    "table, text",
    [
        ([["Объявление", "Клики"]],
         "Не нашли столбцы «Заголовок» и «Клики» — выгрузите отчёт Мастера с группировкой по заголовку"),
        ([["Заголовок", "Клики"], ["А", "1"]], "В отчёте нет столбца «Конверсии» — добавьте его в Мастере отчётов"),
        ([["Заголовок", "Клики", "Конверсии"], ["А", "0", "0"]], "В отчёте нет строк с показами"),
    ],
)
def test_parse_rows_errors(table, text):
    with pytest.raises(YandexError) as exc:
        headlines.parse_rows(table)
    assert exc.value.message == text


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_parse_file_csv_in_cp1251():
    text = "Заголовок;Показы;Клики;Конверсии\r\nБыстро;100;10;2\r\n"
    rows = headlines.parse_file("report.csv", "data:text/csv;base64," + _b64(text.encode("cp1251")))
    assert [(r["title"], r["clicks"], r["conversions"]) for r in rows] == [("Быстро", 10, 2)]


def test_parse_file_xlsx():
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    shared = (f"<sst {ns}><si><t>Заголовок</t></si><si><t>Клики</t></si><si><t>Конверсии</t></si>"
              "<si><t>Быстро</t></si></sst>")
    sheet = (
        f"<worksheet {ns}><sheetData>"
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Отчёт</t></is></c></row>'
        '<row r="2"><c r="B2" t="s"><v>0</v></c><c r="C2" t="s"><v>1</v></c><c r="D2" t="s"><v>2</v></c></row>'
        '<row r="3"><c r="B3" t="s"><v>3</v></c><c r="C3"><v>12</v></c><c r="D3"><v>3</v></c></row>'
        "</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    rows = headlines.parse_file("report.xlsx", _b64(buf.getvalue()))
    assert [(r["title"], r["clicks"], r["conversions"]) for r in rows] == [("Быстро", 12, 3)]


def test_parse_file_rejects_old_xls_and_garbage():
    with pytest.raises(YandexError) as exc:
        headlines.parse_file("report.xls", _b64(b"\xd0\xcf\x11\xe0"))
    assert exc.value.message == "Старый формат XLS не поддерживается — выгрузите XLSX или CSV"
    with pytest.raises(YandexError) as exc:
        headlines.parse_file("report.csv", "не base64")
    assert exc.value.message == "Файл повреждён"


def _ad(row_id, campaign, titles, clicks, conversions, impressions=1000):
    return {"ad_row_id": row_id, "ad_id": row_id * 10, "campaign_id": campaign, "adgroup_name": "Группа",
            "titles": titles, "total": {"impressions": impressions, "clicks": clicks, "cost_kopecks": clicks * 1000,
                                        "conversions": conversions}}


def test_summarize_ranks_by_conversions_and_splits_mixed():
    ads = [
        _ad(1, 20, ["Быстро"], 50, 2),
        _ad(2, 20, ["Дёшево"], 120, 5),
        _ad(3, 21, ["быстро "], 50, 2),  # тот же заголовок в другой кампании — складываем
        _ad(4, 20, ["А", "Б"], 300, 9),
        _ad(5, 20, ["Без показов"], 0, 0, impressions=0),
    ]
    out = headlines.summarize(ads)
    assert [(h["rank"], h["title"], h["conversions"], h["clicks"]) for h in out["headlines"]] == [
        (1, "Дёшево", 5, 120), (2, "Быстро", 4, 100)
    ]
    best = out["headlines"][0]
    assert (best["cr"], best["cpa_rub"], best["enough"], best["sources"]) == (4.17, 240.0, True, ["ads"])
    assert [m["titles"] for m in out["mixed"]] == [["А", "Б"]]


def test_summarize_does_not_double_count_uploaded_campaigns():
    ads = [_ad(1, 20, ["Быстро"], 50, 2), _ad(2, 21, ["Быстро"], 10, 1)]
    uploaded = [{"title": "Быстро", "campaign_row_id": 20, "impressions": 900, "clicks": 45, "cost_kopecks": 100,
                 "conversions": 2}]
    h = headlines.summarize(ads, uploaded)["headlines"][0]
    assert (h["clicks"], h["conversions"], h["sources"]) == (55, 3, ["ads", "report"])  # 21 — из объявлений, 20 — из отчёта
    everything = [{**uploaded[0], "campaign_row_id": None}]  # отчёт без кампании покрывает всё
    h = headlines.summarize(ads, everything)["headlines"][0]
    assert (h["clicks"], h["sources"]) == (45, ["report"])
