"""Чистка запросов, бриф и описание отката — чистые функции без базы."""

import pytest

from app import query_cleaning as qc
from app.brief import _check_public, brief_filled, brief_prompt, extract_page, normalize_brief
from app.negatives import inverse, targets_of
from app.undo import describe_step
from app.yandex import YandexError

# --- Основы слов и совпадения -------------------------------------------------------------------


def test_stem_ignores_case_yo_and_endings():
    assert qc.stem("Ёлки") == qc.stem("елка") == "елк"
    assert qc.stem("кухни") == qc.stem("кухня") == qc.stem("кухню")
    assert qc.stem("сады") == "сад"
    # Короткие и латинские слова не режем.
    assert qc.stem("ikea") == "ikea"
    assert qc.stem("дом") == "дом"


def test_stems_drop_stop_words_but_keep_lone_ones():
    assert qc.stems("кухня для дома") == [qc.stem("кухня"), qc.stem("дома")]
    assert qc.stems("как") == ["как"]


def test_blocks_any_form_and_word_order():
    assert qc.blocks("бесплатно", "Кухни БЕСПЛАТНО москва")
    assert qc.blocks("кухня москва", "москва кухни")
    assert qc.blocks("своими руками", "кухня своими руками")
    assert not qc.blocks("своими руками", "руками кухня")
    assert not qc.blocks("для дома", "кухня для офиса")
    assert not qc.blocks("", "кухня")


def test_keyword_text_strips_operators_and_minus_words():
    assert qc.keyword_text('"купить кухню" -бесплатно -своими') == "купить кухню"
    assert qc.keyword_text("[кухня москва] +на заказ") == "кухня москва на заказ"
    assert qc.keyword_text("!купить кухню") == "купить кухню"
    assert qc.keyword_text("кухня-трансформер") == "кухня-трансформер"


def test_clean_negative():
    assert qc.clean_negative("-бесплатно, +своими руками.") == "бесплатно своими руками"
    assert qc.clean_negative("  @#$ ") == ""
    assert qc.clean_negative("!купить") == "!купить"


def test_valid_negative_takes_only_query_words():
    assert qc._valid_negative("бесплатно", "кухни бесплатно") == "бесплатно"
    assert qc._valid_negative("бесплатные", "кухня бесплатно") == "бесплатные"
    # Слова не из запроса — берём сам запрос.
    assert qc._valid_negative("дешево", "кухни бесплатно") == "кухни бесплатно"
    long = " ".join(f"слово{n}" for n in range(10))
    assert len(qc._valid_negative("", long).split()) == qc.MAX_WORDS


# --- Правила разметки ---------------------------------------------------------------------------


def _negatives(campaigns=None, adgroups=None, group_campaign=None):
    def prep(d):
        return {k: [(p, qc.stems(p)) for p in v] for k, v in (d or {}).items()}

    return {"campaigns": prep(campaigns), "adgroups": prep(adgroups), "group_campaign": group_campaign or {}}


ROW = {"norm": "кухни своими руками бесплатно", "campaign_row_ids": [1], "adgroup_row_ids": [10]}


def test_rule_label_direct_beats_memory_and_brief():
    memory = [("своими руками", qc.stems("своими руками"), "adgroup", "шкаф своими руками")]
    brief = qc._brief_rules({"not_ours": ["бесплатно"]})
    negs = _negatives(campaigns={1: ["бесплатно"]}, adgroups={10: []}, group_campaign={10: 1})
    hit = qc.rule_label(ROW, memory, brief, negs)
    assert hit["source"] == "direct" and hit["level"] == "campaign" and hit["negative_phrase"] == "бесплатно"

    negs = _negatives(campaigns={1: []}, adgroups={10: ["руками"]}, group_campaign={10: 1})
    hit = qc.rule_label(ROW, memory, brief, negs)
    assert hit["source"] == "direct" and hit["level"] == "adgroup"


def test_rule_label_memory_then_brief():
    negs = _negatives(campaigns={1: []}, adgroups={10: []}, group_campaign={10: 1})
    memory = [("своими руками", qc.stems("своими руками"), "adgroup", "шкаф своими руками")]
    brief = qc._brief_rules({"not_ours": ["бесплатно"]})
    hit = qc.rule_label(ROW, memory, brief, negs)
    assert hit["source"] == "memory" and hit["level"] == "adgroup"
    assert "шкаф своими руками" in hit["reason"]

    hit = qc.rule_label(ROW, [], brief, negs)
    assert hit == {
        "verdict": "negative",
        "negative_phrase": "бесплатно",
        "level": "campaign",
        "source": "brief",
        "reason": "В брифе «не наше»: бесплатно",
    }
    assert qc.rule_label({**ROW, "norm": "кухни на заказ"}, memory, brief, negs) is None


def test_blocked_only_when_cut_everywhere():
    row = {"norm": "кухни бесплатно", "campaign_row_ids": [1], "adgroup_row_ids": [10, 11]}
    negs = _negatives(adgroups={10: ["бесплатно"], 11: []}, group_campaign={10: 1, 11: 1})
    assert qc.blocked_by(row, negs, qc.stems(row["norm"])) is None
    negs = _negatives(adgroups={10: ["бесплатно"], 11: ["бесплатно"]}, group_campaign={10: 1, 11: 1})
    assert qc.blocked_by(row, negs, qc.stems(row["norm"])) == "бесплатно"

    row = {"norm": "кухни бесплатно", "campaign_row_ids": [1, 2], "adgroup_row_ids": []}
    assert qc.blocked_info(row, _negatives(campaigns={1: ["бесплатно"], 2: []}), qc.stems(row["norm"])) is None
    assert qc.blocked_info(row, _negatives(campaigns={1: ["бесплатно"], 2: ["бесплатно"]}), qc.stems(row["norm"])) == (
        "бесплатно",
        "campaign",
    )


def test_brief_rules_skip_bad_phrases():
    rules = qc._brief_rules({"not_ours": ["-бесплатно", "", " ".join(["слово"] * 9)]})
    assert [r[0] for r in rules] == ["бесплатно"]


def test_find_conflicts_by_level():
    def kw(kid, text, group, campaign=1):
        return {"id": kid, "keyword": text, "adgroup_row_id": group, "adgroup_name": "", "campaign_row_id": campaign, "stems": qc.stems(text)}

    a, b = kw(1, "кухни бесплатно", 10), kw(2, "бесплатная доставка кухни", 11)
    index = ({10: [a], 11: [b]}, {1: [a, b]})
    row = {"campaign_row_ids": [1], "adgroup_row_ids": [10]}
    assert [k["id"] for k in qc.find_conflicts("бесплатно", "adgroup", row, index)] == [1]
    assert [k["id"] for k in qc.find_conflicts("бесплатно", "campaign", row, index)] == [1, 2]
    assert qc.find_conflicts("вакансии", "campaign", row, index) == []


# --- ИИ-разметка --------------------------------------------------------------------------------


def test_ai_labels_validates_reply(monkeypatch):
    batch = [
        {"norm": "кухни бесплатно", "query": "кухни бесплатно", "matched": ["кухни"], "clicks": 3, "cost_kopecks": 1500, "conversions": 0},
        {"norm": "кухни на заказ", "query": "кухни на заказ", "matched": [], "clicks": 1, "cost_kopecks": 100, "conversions": 1},
        {"norm": "кухни дешево", "query": "кухни дешево", "matched": [], "clicks": 0, "cost_kopecks": 0, "conversions": 0},
    ]
    seen = {}

    def fake(cfg, messages):
        seen["messages"] = messages
        return {
            "items": [
                {"id": 1, "verdict": "negative", "negative": "-бесплатно!", "reason": " не   покупают ", "level": "campaign"},
                {"id": 2, "verdict": "TARGET", "level": "nowhere"},
                {"id": 2, "verdict": "negative", "negative": "заказ"},  # повтор id
                {"id": 9, "verdict": "target"},  # нет такого
                {"id": 3, "verdict": "maybe"},
                "мусор",
            ]
        }

    monkeypatch.setattr(qc.ai, "complete_json", fake)
    got = qc.ai_labels({}, "system", batch)
    assert [(g["query_norm"], g["verdict"]) for g in got] == [("кухни бесплатно", "negative"), ("кухни на заказ", "target")]
    assert got[0]["negative_phrase"] == "бесплатно!"
    assert got[0]["reason"] == "не покупают"
    assert got[1]["level"] == "campaign" and got[1]["negative_phrase"] == ""
    assert '"q": "кухни бесплатно"' in seen["messages"][1]["content"]


# --- Применение ---------------------------------------------------------------------------------


def test_clean_items_and_needs_direct():
    items = qc._clean_items(
        [
            {"query_norm": "a", "verdict": "negative", "level": "zzz"},
            {"query_norm": "a", "verdict": "target"},
            {"query_norm": "b", "verdict": "maybe"},
            {"query_norm": " ", "verdict": "target"},
            {"query_norm": "c", "verdict": "target", "add_keyword": True, "adgroup_row_id": "7"},
        ]
    )
    assert [(i["query_norm"], i["verdict"], i["level"]) for i in items] == [("a", "negative", "adgroup"), ("c", "target", "adgroup")]
    assert items[1]["adgroup_row_id"] == 7
    with pytest.raises(YandexError):
        qc._clean_items([{"query_norm": "x", "verdict": None}])
    assert qc.needs_direct([{"verdict": "negative"}])
    assert qc.needs_direct([{"verdict": "target", "add_keyword": True}])
    assert not qc.needs_direct([{"verdict": "target"}])


def test_apply_title():
    assert qc._apply_title(2, 1, 0) == "Чистка запросов: в минус 2, целевых 1"
    assert qc._apply_title(0, 0, 0) == "Чистка запросов: без изменений"


def test_examples_prompt():
    text = qc.examples_prompt(
        {
            "negative": [{"query": "кухни бесплатно", "negative_phrase": "бесплатно"}, {"query": "вакансии", "negative_phrase": ""}],
            "target": [{"query": "кухни на заказ"}],
        }
    )
    assert text.splitlines() == [
        "- «кухни бесплатно» → минус «бесплатно»",
        "- «вакансии» → минус «вакансии»",
        "- «кухни на заказ» → целевой",
    ]


# --- Бриф ---------------------------------------------------------------------------------------


def test_normalize_brief():
    b = normalize_brief({"product": "  Кухни  ", "not_ours": "бесплатно, своими руками\nБесплатно;вакансии", "x": 1})
    assert b == {"product": "Кухни", "geo": "", "not_ours": ["бесплатно", "своими руками", "вакансии"], "notes": ""}
    assert normalize_brief(None) == {"product": "", "geo": "", "not_ours": [], "notes": ""}
    assert len(normalize_brief({"not_ours": [f"слово {n}" for n in range(150)]})["not_ours"]) == 100
    assert not brief_filled({})
    assert brief_filled({"geo": "Москва"})


def test_brief_prompt():
    text = brief_prompt(
        {"name": "Кухни", "site": "kitchen.ru", "brief": {"product": "Кухни на заказ", "not_ours": ["б/у"]}, "kpi_target": 1500}
    )
    assert "Что продаём: Кухни на заказ" in text
    assert "Точно не наше: б/у" in text
    assert "цена конверсии не выше 1500 ₽" in text
    assert "Бриф не заполнен" in brief_prompt({"name": "Кухни"})


def test_extract_page_skips_scripts():
    html = """<html><head><title>Кухни Мастер</title>
    <meta name="description" content="Кухни на заказ   в Москве">
    <script>var spam = "не текст";</script><style>.a{}</style></head>
    <body><h1>Кухни <b>из массива</b></h1><p>Доставка по области.</p></body></html>"""
    page = extract_page(html)
    assert page["title"] == "Кухни Мастер"
    assert page["description"] == "Кухни на заказ в Москве"
    assert page["headings"] == ["Кухни из массива"]
    assert "Доставка по области." in page["text"]
    assert "spam" not in page["text"]
    assert extract_page("<p>обрыв <b")["text"].startswith("обрыв")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "http://localhost/",
        "http://shop.local/",
        "http://user:pw@example.com/",
        "http://example.com:8080/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://[::1]/",
    ],
)
def test_check_public_blocks_internal(url):
    with pytest.raises(YandexError):
        _check_public(url)


def test_check_public_resolves_every_address(monkeypatch):
    from app import brief

    monkeypatch.setattr(brief.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    _check_public("https://example.com/")
    monkeypatch.setattr(
        brief.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("192.168.1.5", 443))],
    )
    with pytest.raises(YandexError):
        _check_public("https://example.com/")


# --- Откат --------------------------------------------------------------------------------------


def test_inverse_and_targets():
    result = {
        "campaigns": {1: {"added": ["бесплатно"], "removed": ["старое"], "name": "К"}},
        "adgroups": {10: {"added": [], "removed": []}, 11: {"added": ["б/у"], "removed": []}},
    }
    assert inverse(result) == {
        "campaigns": {"1": {"add": ["старое"], "remove": ["бесплатно"]}},
        "adgroups": {"11": {"add": [], "remove": ["б/у"]}},
    }
    assert targets_of(result) == ["campaign:1", "adgroup:10", "adgroup:11"]
    assert inverse({"campaigns": {}, "adgroups": {}}) == {}


def test_describe_step_without_db():
    step = {"op": "negatives", "args": {"campaigns": {"1": {"add": ["x"], "remove": ["a", "b"]}}, "adgroups": {"2": {"remove": ["a"]}}}}
    assert describe_step(0, step) == "Минус-фразы в 1 кампании и 1 группе: убрать «a», «b»; вернуть «x»"
    many = {"op": "negatives", "args": {"campaigns": {str(i): {"remove": ["a"]} for i in range(11)}}}
    assert describe_step(0, many) == "Минус-фразы в 11 кампаниях: убрать «a»"
    assert describe_step(0, {"op": "query_labels_restore", "args": {"labels": [{}, {}]}}) == "Вернуть разметку запросов: 2"
    items = [{"keyword": f"фраза {n}"} for n in range(7)]
    assert describe_step(0, {"op": "keywords_recreate", "args": {"items": items}}).endswith("и ещё 2")
    assert describe_step(0, {"op": "campaign_update", "args": {"name": "Старое"}}) == "Вернуть название кампании: «Старое»"
    assert describe_step(0, {"op": "mystery"}) == "mystery"


def test_effective_level_falls_back_to_campaign_without_groups():
    assert qc.effective_level("adgroup", {"adgroup_row_ids": [5]}) == "adgroup"
    assert qc.effective_level("adgroup", {"adgroup_row_ids": []}) == "campaign"
    assert qc.effective_level("campaign", {"adgroup_row_ids": [5]}) == "campaign"
    assert qc.effective_level("bogus", {"adgroup_row_ids": [5]}) == "adgroup"
    assert qc.effective_level(None, None) == "campaign"
