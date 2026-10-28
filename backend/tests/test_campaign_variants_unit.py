"""Варианты кампании без БД и Директа: разбор вариантов, сборка копий, рейтинг, инструмент ИИ."""

import math
from datetime import date
from types import SimpleNamespace

import pytest

from app import actions, adgroups, ads, ai_agent, experiments, geo, keywords, variants
from app.workspaces import normalize_ai_access
from app.yandex import YandexError

TODAY = date(2026, 10, 28)


# --- Разбор вариантов ---------------------------------------------------------------------------


def test_normalize_defaults_and_cleanup(monkeypatch):
    monkeypatch.setattr(variants, "today_msk", lambda: TODAY)
    out = variants.normalize_variants(
        [
            {"idea": "  Казань  ", "region_ids": "43, -11", "schedule": "WORKHOURS"},
            {
                "name": " Окна — выгода ",
                "priority_goals": [{"goal_id": "5001", "value_rub": 500}],
                "weekly_budget_rub": "1500",
                "ads": {"titles": [" Окна  от завода ", "Окна от завода", ""], "text": "Замер бесплатно"},
                "keywords": ["окна пвх", "окна пвх", " окна  на заказ "],
            },
        ],
        "Окна Москва",
    )
    assert out[0] == {"n": 1, "name": "Казань · 28.10", "idea": "Казань", "label": "Казань",
                      "region_ids": [43, -11], "schedule": "workhours"}
    assert out[1] == {
        "n": 2,
        "name": "Окна — выгода",
        "idea": "",
        "priority_goals": [{"goal_id": 5001, "value_rub": 500.0}],
        "weekly_budget_rub": 1500.0,
        "ads": {"mode": "replace", "titles": ["Окна от завода"], "texts": ["Замер бесплатно"], "title2": "", "href": ""},
        "keywords": {"mode": "add", "phrases": ["окна пвх", "окна на заказ"], "bid_rub": None},
    }


@pytest.mark.parametrize(
    "raw, text",
    [
        ([], "variants — список вариантов, от 1 до 5"),
        ([{"schedule": "always"}] * 6, "Не больше 5 вариантов за раз — оставь самые сильные гипотезы"),
        ([{"idea": "просто копия"}], "Вариант 1 ничего не меняет — задай хотя бы одно: region_ids, schedule, "
                                     "priority_goals, weekly_budget_rub, ads, keywords, groups; просто копия — "
                                     "duplicate_campaign"),
        ([{"name": "А", "schedule": "always"}, {"name": "а", "schedule": "daytime"}],
         "Вариант 2: название «а» уже занято — у вариантов разные названия"),
        ([{"schedule": "nights"}], "Вариант 1: schedule — одно из ['always', 'workdays', 'workhours', 'daytime']"),
        ([{"region_ids": [-1]}], "Вариант 1: region_ids — хотя бы один регион показа (id из find_regions, без минуса)"),
        ([{"weekly_budget_rub": 200}], "Вариант 1: недельный бюджет в Директе — не меньше 300 ₽"),
        ([{"ads": {"titles": ["x" * 57]}}], f"Вариант 1: «{'x' * 40}…» длиннее 56 символов — сократи"),
        ([{"ads": {"texts": ["a", "b", "c", "d"]}}], "Вариант 1: texts — не больше 3"),
        ([{"ads": {"mode": "add", "titles": ["Окна"]}}], "Вариант 1: для нового объявления (mode=add) нужны titles и texts"),
        ([{"ads": {"title2": "x" * 31, "titles": ["Окна"]}}], "Вариант 1: title2 длиннее 30 символов — сократи"),
        ([{"keywords": {"phrases": [], "mode": "add"}}], "Вариант 1: в keywords нет фраз"),
        ([{"keywords": {"phrases": ["a"], "mode": "swap"}}], "Вариант 1: keywords.mode — одно из ['add', 'replace']"),
        ([{"priority_goals": [{"goal_id": 1, "value_rub": 0.1}]}], None),
    ],
)
def test_normalize_errors(raw, text):
    with pytest.raises(YandexError) as exc:
        variants.normalize_variants(raw, "Окна")
    assert exc.value.code == "args"
    if text:
        assert exc.value.message == text
    else:
        assert exc.value.message.startswith("Вариант 1: ")


def test_limit_plan_force_sets_the_variant_budget():
    settings = {"name": "Окна", "type": "UNIFIED_CAMPAIGN", "daily_budget_rub": None,
                "strategy": {"search": "WB_MAXIMUM_CONVERSION_RATE", "network": "SERVING_OFF", "manual": False,
                             "weekly_budget_rub": 2000.0}}
    assert experiments.limit_plan(settings, 3000)["changes"] == {}
    forced = experiments.limit_plan(settings, 3000, force=True)
    assert forced["changes"]["strategy"]["weekly_budget_rub"] == 3000
    manual = {"name": "Окна", "type": "TEXT_CAMPAIGN", "daily_budget_rub": 400.0,
              "strategy": {"search": "HIGHEST_POSITION", "network": "SERVING_OFF", "manual": True}}
    assert experiments.limit_plan(manual, 3500)["changes"] == {}
    assert experiments.limit_plan(manual, 3500, force=True)["changes"] == {"daily_budget_rub": 500}


def test_merge_goals_keeps_the_rest_of_the_list():
    current = [{"goal_id": 1, "value_rub": 1500.0}, {"goal_id": 2, "value_rub": 400.0}]
    assert variants.merge_goals(current, [{"goal_id": 1, "value_rub": 1000.0}, {"goal_id": 3, "value_rub": 50.0}]) == [
        {"goal_id": 1, "value_rub": 1000.0}, {"goal_id": 2, "value_rub": 400.0}, {"goal_id": 3, "value_rub": 50.0}
    ]
    assert current[0]["value_rub"] == 1500.0  # список оригинала не меняется


# --- Сборка варианта -----------------------------------------------------------------------------


@pytest.fixture
def direct(monkeypatch):
    """Директ без сети: копия кампании 7 — кампания 70 с двумя объявлениями в одной группе."""
    fake = SimpleNamespace(calls=[], fail=set(), groups=[{"id": 700, "name": "Группа"}], status="ok", group_map={},
                           extras=None)

    def call(name, result=None):
        def fn(*args, **kwargs):
            fake.calls.append((name, args[1:], kwargs))
            if name in fake.fail:
                raise YandexError("api", f"{name} упал")
            return result() if callable(result) else result
        return fn

    def duplicate(user_id, cid, *, name=None, workspace_id=None, exclude_adgroup_ids=None, **kw):
        fake.calls.append(("duplicate", (cid,), {"name": name, "exclude": exclude_adgroup_ids}))
        return {"campaign": {"id": 70, "name": name}, "groups": 1, "group_map": fake.group_map, "keywords": 2,
                "ads": 2, "warnings": [], "status": fake.status}

    monkeypatch.setattr(actions, "duplicate_campaign", duplicate)
    monkeypatch.setattr(actions, "get_campaign_settings", call("settings", lambda: {
        "name": "Окна", "type": "UNIFIED_CAMPAIGN", "daily_budget_rub": None,
        "strategy": {"search": "WB_MAXIMUM_CONVERSION_RATE", "network": "SERVING_OFF", "manual": False,
                     "weekly_budget_rub": 5000.0}}))
    monkeypatch.setattr(actions, "update_campaign_settings", call("update_settings", {"changed": ["strategy"]}))
    monkeypatch.setattr(actions, "moderate_campaign", call("moderate", {"sent": 1}))
    monkeypatch.setattr(geo, "set_campaign_regions", call("regions", {"groups": 1, "updated": 1, "errors": []}))
    monkeypatch.setattr(adgroups, "list_adgroups", lambda user_id, cid: fake.groups)
    monkeypatch.setattr(ads, "list_ads", lambda user_id, campaign_row_id=None: [
        {"id": 1, "ad_id": 11, "type": "RESPONSIVE_AD", "state": "ON", "title": "Окна", "href": "https://a.ru",
         "adgroup_row_id": 700},
        {"id": 2, "ad_id": 12, "type": "TEXT_AD", "state": "ARCHIVED", "title": "Старое", "href": "https://b.ru",
         "adgroup_row_id": 700},
        {"id": 3, "ad_id": 13, "type": "IMAGE_AD", "state": "ON", "title": "", "href": "https://a.ru",
         "adgroup_row_id": 700},
    ])
    monkeypatch.setattr(ads, "update_text_ad", call("update_ad", {}))
    monkeypatch.setattr(ads, "create_text_ad", call("create_ad", {}))
    monkeypatch.setattr(keywords, "list_keywords", lambda user_id, campaign_row_id=None: [
        {"id": 5, "keyword": "окна пвх", "adgroup_row_id": 700},
        {"id": 6, "keyword": "окна дешево", "adgroup_row_id": 700},
        {"id": 7, "keyword": "---autotargeting", "adgroup_row_id": 700},
    ])
    monkeypatch.setattr(keywords, "bulk_keyword_action", call("delete_keywords", lambda: {"applied": 1}))
    monkeypatch.setattr(keywords, "create_keywords", call("create_keywords", lambda: {"created": 1, "errors": 0}))
    monkeypatch.setattr(adgroups, "update_adgroup", call("rename_group", {}))
    monkeypatch.setattr(variants, "_group_extras", lambda *a: fake.extras)
    monkeypatch.setattr(variants, "_drop_ads", lambda user_id, ads: fake.calls.append(("drop_ads", tuple(a["id"] for a in ads), {})) or len(ads))
    return fake


def _names(fake):
    return [c[0] for c in fake.calls]


FULL = {
    "region_ids": "43",
    "schedule": "workdays",
    "weekly_budget_rub": 1500,
    "ads": {"titles": ["Окна от завода"], "texts": ["Замер бесплатно"]},
    "keywords": {"mode": "replace", "phrases": ["окна пвх", "окна на заказ"]},
}


def test_create_variant_applies_everything_then_moderates(direct):
    v = variants.normalize_variants([FULL], "Окна")[0]
    out = variants.create_variant(1, 7, v, workspace_id=2, moderate=True)
    assert _names(direct) == ["duplicate", "settings", "update_settings", "regions", "update_ad", "delete_keywords",
                              "create_keywords", "moderate"]
    settings = direct.calls[2][1][1]
    assert settings["schedule"] == "workdays"
    assert settings["strategy"]["weekly_budget_rub"] == 1500  # меньше нынешних 5 000 ₽ — force
    assert direct.calls[4][1] == (1,)  # только живое текстовое/комбинаторное объявление
    assert direct.calls[5][2]["keyword_row_ids"] == [6]  # «окна дешево» убрали, «окна пвх» и автотаргетинг — нет
    assert direct.calls[6][2]["phrases"] == ["окна на заказ"]
    assert (out["ready"], out["moderated"], out["errors"]) == (True, True, [])
    assert out["done"] == ["настройки", "регионы", "объявления", "ключевые фразы", "модерация"]


def test_create_variant_with_error_stays_a_draft(direct):
    direct.fail = {"update_ad"}
    v = variants.normalize_variants([FULL], "Окна")[0]
    out = variants.create_variant(1, 7, v, workspace_id=2, moderate=True)
    assert "moderate" not in _names(direct)
    assert "create_keywords" in _names(direct)  # остальные шаги всё равно сделаны
    assert out["ready"] is False
    assert out["errors"] == ["объявления: готово 0, ошибки: «Окна»: update_ad упал"]
    assert out["note"] == "Вариант собран не полностью — остался черновиком, на модерацию не отправлен"


def test_create_variant_partial_copy_and_no_moderation(direct):
    v = variants.normalize_variants([{"schedule": "always"}], "Окна")[0]
    draft = variants.create_variant(1, 7, v, workspace_id=2, moderate=False)
    assert (draft["ready"], draft["moderated"], draft["note"]) == (
        True, False, "Черновик: отправьте на модерацию, когда проверите"
    )
    direct.status = "partial"
    partial = variants.create_variant(1, 7, v, workspace_id=2, moderate=True)
    assert partial["errors"] == ["копия собрана не полностью"]
    assert _names(direct).count("moderate") == 0


def test_create_variant_adds_ads_to_every_group(direct):
    direct.groups = [{"id": 700, "name": "Группа"}, {"id": 701, "name": "Вторая"}]
    v = variants.normalize_variants([{"ads": {"mode": "add", "titles": ["Окна"], "texts": ["Замер"]}}], "Окна")[0]
    variants.create_variant(1, 7, v, workspace_id=2, moderate=False)
    created = [c[2] for c in direct.calls if c[0] == "create_ad"]
    assert [(c["adgroup_row_id"], c["href"]) for c in created] == [(700, "https://a.ru"), (701, "https://a.ru")]


def test_create_variants_keeps_going_after_a_failure(direct, monkeypatch):
    ticks = []
    items = variants.normalize_variants([{"name": "А", "schedule": "always"}, {"name": "Б", "schedule": "daytime"}], "Окна")
    real = actions.duplicate_campaign

    def flaky(user_id, cid, *, name=None, **kw):
        if name == "А":
            raise YandexError("api", "лимит баллов")
        return real(user_id, cid, name=name, **kw)

    monkeypatch.setattr(actions, "duplicate_campaign", flaky)
    out = variants.create_variants(1, 7, items, workspace_id=2, moderate=False, tick=lambda: ticks.append(1))
    assert out[0] == {"n": 1, "name": "А", "error": "лимит баллов", "ready": False}
    assert out[1]["ready"] is True
    assert len(ticks) == 2


def test_check_source(direct, monkeypatch):
    monkeypatch.setattr(geo, "unknown_regions", lambda user_id, ids: [i for i in ids if i == 999])
    source = {"id": 7, "name": "Окна"}
    variants.check_source(1, source, variants.normalize_variants([FULL], "Окна"))
    with pytest.raises(YandexError) as exc:
        variants.check_source(1, source, variants.normalize_variants([{"region_ids": [999]}], "Окна"))
    assert exc.value.message == "Неизвестные регионы [999] — найди id через find_regions"
    direct.groups = [{"id": 700, "name": "Группа"}, {"id": 701, "name": "Вторая"}]
    with pytest.raises(YandexError) as exc:
        variants.check_source(1, source, variants.normalize_variants([FULL], "Окна"))
    assert exc.value.message == (
        "Вариант 1: в «Окна» групп: 2 — заменить фразы сразу во всей кампании нельзя. Задай фразы каждой группе "
        "в groups (group_id: 700 «Группа», 701 «Вторая») или keywords.mode=add"
    )


# --- Изменения по группам ------------------------------------------------------------------------

GROUPS = {
    "groups": [
        {"group_id": 700, "keywords": {"mode": "replace", "phrases": ["окна пвх", "окна на заказ"]},
         "ads": {"titles": ["Окна под ключ"]}},
        {"group_id": "701", "ads": {"mode": "add", "titles": ["Двери"], "texts": ["Монтаж за день"]}},
        {"group_id": 702, "remove": True},
        {"name": "Балконы", "keywords": ["остекление балконов"], "ads": {"titles": ["Балконы"], "texts": ["Под ключ"]}},
    ]
}


def test_normalize_groups():
    v = variants.normalize_variants([GROUPS], "Окна")[0]
    assert v["groups"][1] == {"group_id": 701, "ads": {"mode": "add", "titles": ["Двери"], "texts": ["Монтаж за день"],
                                                       "title2": "", "href": ""}}
    assert v["groups"][2] == {"group_id": 702, "remove": True}
    assert v["groups"][3]["new"] is True
    assert v["groups"][3]["keywords"]["mode"] == "add" and v["groups"][3]["ads"]["mode"] == "add"


@pytest.mark.parametrize(
    "raw, text",
    [
        ({"groups": [{"group_id": 700}]}, "Вариант 1, группа 1: группа 700 ничего не меняет — задай keywords, ads, rename или remove: true"),
        ({"groups": [{"group_id": 700, "remove": True, "keywords": ["a"]}]},
         "Вариант 1, группа 1: группу 700 убираем (remove) — keywords, ads и rename ей не нужны"),
        ({"groups": [{"name": "Новая", "keywords": ["a"]}]},
         "Вариант 1, группа 1: новой группе «Новая» нужны keywords и ads (titles и texts)"),
        ({"groups": [{"name": "Новая", "keywords": ["a"], "ads": {"titles": ["Т"]}}]},
         "Вариант 1, группа 1: для нового объявления (mode=add) нужны titles и texts"),
        ({"groups": [{"remove": True}]},
         "Вариант 1, группа 1: group_id группы оригинала (из get_campaign) или name новой группы"),
        ({"groups": [{"group_id": 700, "remove": True}, {"group_id": 700, "keywords": ["a"]}]},
         "Вариант 1: группа 700 указана дважды — объедини изменения"),
        ({"keywords": ["a"], "groups": [{"group_id": 700, "remove": True}]},
         "Вариант 1: фразы и объявления задай либо на всю кампанию (ads, keywords), либо по группам (groups)"),
        ({"groups": {"group_id": 700}}, "Вариант 1: groups — список изменений по группам"),
    ],
)
def test_normalize_group_errors(raw, text):
    with pytest.raises(YandexError) as exc:
        variants.normalize_variants([raw], "Окна")
    assert exc.value.message == text


@pytest.fixture
def grouped(direct, monkeypatch):
    """Оригинал 7: группы «Окна», «Двери», «Слабая»; копия 70 — без «Слабой»."""
    source = [{"id": 700, "adgroup_id": 9700, "name": "Окна", "region_ids": [213]},
              {"id": 701, "adgroup_id": 9701, "name": "Двери", "region_ids": [213]},
              {"id": 702, "adgroup_id": 9702, "name": "Слабая", "region_ids": [213]}]
    copy = [{"id": 800, "adgroup_id": 9800, "name": "Окна", "region_ids": [2]},
            {"id": 801, "adgroup_id": 9801, "name": "Двери", "region_ids": [2]}]
    direct.group_map = {9700: 9800, 9701: 9801}
    monkeypatch.setattr(adgroups, "list_adgroups", lambda user_id, cid: source if cid == 7 else copy)
    ads_by = {
        7: [{"id": 1, "ad_id": 11, "type": "RESPONSIVE_AD", "state": "ON", "title": "Окна", "href": "https://a.ru",
             "adgroup_row_id": 700}],
        70: [{"id": 22, "ad_id": 122, "type": "TEXT_AD", "state": "ON", "title": "Двери", "href": "https://b.ru",
              "adgroup_row_id": 801},
             {"id": 21, "ad_id": 121, "type": "RESPONSIVE_AD", "state": "ON", "title": "Окна", "href": "https://a.ru",
              "adgroup_row_id": 800}],
    }
    monkeypatch.setattr(ads, "list_ads", lambda user_id, campaign_row_id=None: ads_by[campaign_row_id])
    monkeypatch.setattr(keywords, "list_keywords", lambda user_id, campaign_row_id=None: [
        {"id": 31, "keyword": "окна пвх", "adgroup_row_id": 800},
        {"id": 32, "keyword": "двери", "adgroup_row_id": 801},
        {"id": 33, "keyword": "окна дешево", "adgroup_row_id": 800},
        {"id": 34, "keyword": "---autotargeting", "adgroup_row_id": 800},
    ])

    def create_adgroup(user_id, *, campaign_row_id, name, region_ids=None):
        direct.calls.append(("create_group", (campaign_row_id,), {"name": name, "region_ids": region_ids}))
        return {"id": 802, "adgroup_id": 9802, "name": name, "region_ids": region_ids}

    monkeypatch.setattr(adgroups, "create_adgroup", create_adgroup)
    monkeypatch.setattr(geo, "unknown_regions", lambda user_id, ids: [])
    return SimpleNamespace(source=source, copy=copy, ads=ads_by)


def test_check_source_with_groups(grouped):
    source = {"id": 7, "name": "Окна"}
    v = variants.normalize_variants([GROUPS], "Окна")
    variants.check_source(1, source, v)
    assert [(g.get("adgroup_id"), g.get("source_name")) for g in v[0]["groups"]] == [
        (9700, "Окна"), (9701, "Двери"), (9702, "Слабая"), (None, None)
    ]
    cases = [
        ({"groups": [{"group_id": 799, "remove": True}]},
         "Вариант 1: в «Окна» нет группы 799 — есть 700 «Окна», 701 «Двери», 702 «Слабая»"),
        ({"groups": [{"group_id": 701, "ads": {"titles": ["Двери"]}}]},
         "Вариант 1: в группе «Двери» нет текстовых и комбинаторных объявлений — нужен ads.mode=add"),
        ({"groups": [{"group_id": i, "remove": True} for i in (700, 701, 702)]},
         "Вариант 1: убраны все группы — оставь хотя бы одну или добавь новую"),
        ({"groups": [{"name": "окна", "keywords": ["a"], "ads": {"titles": ["Т"], "texts": ["Т"]}}]},
         "Вариант 1: группа «окна» уже есть в копии — дай новой другое название"),
    ]
    for raw, text in cases:
        with pytest.raises(YandexError) as exc:
            variants.check_source(1, source, variants.normalize_variants([raw], "Окна"))
        assert exc.value.message == text
    grouped.ads[7][0]["href"] = ""
    with pytest.raises(YandexError) as exc:
        variants.check_source(1, source, variants.normalize_variants([{"groups": GROUPS["groups"][3:]}], "Окна"))
    assert exc.value.message == "Вариант 1: у объявлений оригинала нет ссылки — передай ads.href новой группе"


def test_create_variant_by_groups(grouped, direct):
    v = variants.normalize_variants([GROUPS], "Окна")
    variants.check_source(1, {"id": 7, "name": "Окна"}, v)
    out = variants.create_variant(1, 7, v[0], workspace_id=2, moderate=False)
    assert direct.calls[0][2]["exclude"] == {9702}
    calls = direct.calls[1:]
    assert calls[0] == ("update_ad", (21,), {"titles": ["Окна под ключ"], "texts": None, "title2": None, "href": None})
    assert calls[1][0] == "delete_keywords" and calls[1][2]["keyword_row_ids"] == [33]  # только своя группа
    assert calls[2][0] == "create_keywords" and calls[2][2]["adgroup_row_id"] == 800
    assert calls[2][2]["phrases"] == ["окна на заказ"]
    assert calls[3][0] == "create_ad" and (calls[3][2]["adgroup_row_id"], calls[3][2]["href"]) == (801, "https://b.ru")
    assert calls[4] == ("create_group", (70,), {"name": "Балконы", "region_ids": [2]})
    assert calls[5][0] == "create_keywords" and calls[5][2]["adgroup_row_id"] == 802
    assert calls[6][0] == "create_ad" and (calls[6][2]["adgroup_row_id"], calls[6][2]["href"]) == (802, "https://a.ru")
    assert out["done"] == ["группа «Окна»", "группа «Двери»", "новая группа «Балконы»"]
    assert (out["ready"], out["errors"]) == (True, [])


def test_create_variant_group_not_copied_stays_a_draft(grouped, direct):
    direct.group_map = {9700: 9800}
    v = variants.normalize_variants([{"groups": GROUPS["groups"][:2]}], "Окна")
    variants.check_source(1, {"id": 7, "name": "Окна"}, v)
    out = variants.create_variant(1, 7, v[0], workspace_id=2, moderate=True)
    assert out["errors"] == ["группа «Двери»: группа не скопировалась"]
    assert out["ready"] is False and "moderate" not in _names(direct)


def test_plan_and_diff_for_groups(grouped, tool):
    args = {"campaign_row_id": 7, "variants": [{"name": "Окна — группы", **GROUPS}], "moderate": False}
    plan = ai_agent._p_create_variants(FakeContext(), args)
    assert plan.categories == ["campaigns", "ads", "keywords"]
    rows = ai_agent._d_create_variants(FakeContext(), args)
    row = next(r for r in rows if r["what"] == "«Окна — группы»: группы")
    assert row["before"] == "«Окна», «Двери», «Слабая»"
    assert row["after"] == (
        "«Окна»: фразы только 2 фразы: окна пвх, окна на заказ; тексты объявлений группы — заголовки: Окна под ключ / "
        "«Двери»: новое объявление — заголовки: Двери; тексты: Монтаж за день / без «Слабая» / "
        "новая группа «Балконы»: фразы +1 фраза: остекление балконов; новое объявление — заголовки: Балконы; "
        "тексты: Под ключ"
    )


# --- Рейтинг вариантов в эксперименте -----------------------------------------------------------


def _item(cid, conversions=0, cost=0, clicks=0, met=None):
    return {"id": cid, "name": f"В{cid}", "conversions": conversions, "cost_kopecks": cost, "clicks": clicks,
            "cr_pct": round(100 * conversions / clicks, 2) if clicks else None,
            "cpa_kopecks": round(cost / conversions) if conversions else None, "met": met}


def test_ranking_by_criterion():
    items = [_item(1, 3, 90000, 50, met=False), _item(2, 1, 0, 40, met=True), _item(3, 5, 200000, 20, met=False),
             {"id": 4, "name": "[4]", "missing": True}]
    ids = lambda criterion: [r["id"] for r in experiments.ranking({"criterion": criterion}, items)]  # noqa: E731
    assert ids("zero_cost") == [2, 3, 1]  # выполнен критерий — выше всех
    assert ids("conversions") == [3, 1, 2]
    assert ids("cr") == [1, 2, 3]  # 6 % и 2,5 % на 30+ кликах; у третьей мало кликов
    assert ids("custom") == [3, 1, 2]
    assert experiments.ranking({"criterion": "conversions"}, items)[0] == {"place": 1, "id": 3, "name": "В3", "met": False}


# --- Инструмент ИИ ------------------------------------------------------------------------------

CAMPAIGNS = {7: {"id": 7, "name": "Окна", "state": "ON", "status": "ACCEPTED"}}


class FakeContext:
    def __init__(self):
        self.user_id = 1
        self.ws_id = 2
        self.ws = {"name": "Окна", "counter_id": 555, "accounts": [], "goals": [{"goal_id": 5001, "name": "Заявка"}]}
        self.access = normalize_ai_access(None)
        self.action = {"id": 30, "chat_id": 5}

    def campaigns(self, ids):
        foreign = [i for i in ids if i not in CAMPAIGNS]
        if foreign:
            raise YandexError("scope", f"Кампании {foreign} не входят в это пространство")
        return [CAMPAIGNS[i] for i in ids]

    def campaign(self, cid):
        return self.campaigns([cid])[0]


EXPERIMENT = {"title": "Тексты", "hypothesis": "Выгода в заголовке даст больше заявок", "weekly_budget_rub": 2000,
              "criterion": "conversions", "target": 3}
ARGS = {
    "campaign_row_id": 7,
    "variants": [{"name": "Окна — выгода", "ads": {"titles": ["Окна со скидкой 30%"], "texts": ["Замер бесплатно"]}},
                 {"name": "Окна — Казань", "region_ids": [43], "weekly_budget_rub": 1000}],
    "experiment": EXPERIMENT,
}


@pytest.fixture
def tool(direct, monkeypatch):
    fake = SimpleNamespace(created=[], limits=[], touched=[])
    monkeypatch.setattr(geo, "unknown_regions", lambda user_id, ids: [])
    monkeypatch.setattr(geo, "load_regions", lambda user_id: {})
    monkeypatch.setattr(geo, "campaign_regions", lambda user_id, cid: [213])
    monkeypatch.setattr(geo, "region_label", lambda ids, regions, limit=None: ", ".join(map(str, ids)))
    monkeypatch.setattr(ai_agent, "_touch", lambda chat_id: fake.touched.append(chat_id))

    def limits(user_id, rows, weekly):
        fake.limits.append(([r["id"] for r in rows], weekly))
        return [{"campaign_row_id": r["id"]} for r in rows]

    def create(user_id, ws_id, d, *, chat_id=None, action_id=None):
        fake.created.append((d, chat_id, action_id))
        return {"id": 9, "title": d["title"], "started_on": TODAY, "days": d["days"]}

    monkeypatch.setattr(experiments, "apply_limits", limits)
    monkeypatch.setattr(experiments, "create_experiment", create)
    return fake


def test_plan_always_asks(tool):
    plan = ai_agent._p_create_variants(FakeContext(), ARGS)
    assert plan.summary.startswith("Варианты кампании: 2 варианта «Окна»: «Окна — выгода», «Окна — Казань» → черновиками → ")
    assert "эксперимент «Тексты», лимит 2 000 ₽/нед. на вариант, 14 дней" in plan.summary
    assert (plan.categories, plan.change_pct) == (["campaigns", "ads", "budget"], math.inf)
    generous = normalize_ai_access({"categories": {"campaigns": True, "ads": True, "budget": True}, "max_change_pct": 500})
    assert not ai_agent._allowed(generous, plan.categories, plan.change_pct)
    simple = ai_agent._p_create_variants(FakeContext(), {"campaign_row_id": 7, "variants": [{"schedule": "always"}],
                                                         "moderate": False})
    assert (simple.categories, simple.change_pct) == (["campaigns"], None)
    assert "→ черновиками;" in simple.summary


def test_plan_refuses_budget_over_the_experiment_limit(tool):
    args = {**ARGS, "variants": [{"weekly_budget_rub": 2500}]}
    with pytest.raises(YandexError) as exc:
        ai_agent._p_create_variants(FakeContext(), args)
    assert exc.value.message == "Вариант 1: бюджет 2 500 ₽/нед. больше лимита эксперимента 2 000 ₽/нед."


def test_execute_starts_experiment_on_ready_variants(tool, direct):
    direct.fail = {"regions"}
    out = ai_agent._x_create_variants(FakeContext(), ARGS)
    assert [v["ready"] for v in out["variants"]] == [True, False]
    assert tool.limits == [([70], 2000.0)]
    d, chat_id, action_id = tool.created[0]
    assert (d["campaign_row_ids"], d["source_campaign_row_id"], d["kind"], chat_id, action_id) == ([70], 7, "variants", 5, 30)
    assert out["experiment"]["note"].endswith("Варианты с ошибками в эксперимент не вошли.")
    assert tool.touched == [5, 5]


def test_execute_without_ready_variants_skips_experiment(tool, direct):
    direct.fail = {"update_ad", "regions"}
    out = ai_agent._x_create_variants(FakeContext(), ARGS)
    assert out["experiment"]["status"] == "skipped"
    assert tool.created == [] and tool.limits == []


def test_diff_rows(tool):
    rows = ai_agent._d_create_variants(FakeContext(), ARGS)
    whats = [r["what"] for r in rows]
    assert whats == ["Новые кампании", "«Окна — выгода»: объявления и фразы", "«Окна — Казань»: настройки",
                     "Оригинал «Окна»", "Лимит вариантов", "Эксперимент"]
    assert rows[1]["after"] == "во всех объявлениях копии — заголовки: Окна со скидкой 30%; тексты: Замер бесплатно"
    assert rows[2]["before"].startswith("регионы: 213; бюджет: ")
    assert rows[2]["after"].startswith(f"регионы: 43; бюджет: {experiments._rub(1000)}/нед.")
    assert rows[3]["after"] == "без изменений"


# --- Тест заголовков: по объявлению на заголовок, названия по гипотезе ---------------------------


def test_labels_and_names():
    assert variants.short_label("Скорость подключения за 5 минут и без звонков в поддержку") == \
        "Скорость подключения за 5 минут"
    assert variants.short_label("  Выгода.  ") == "Выгода"
    assert variants.default_name("Скорость", 1, "Окна", today=TODAY) == "Скорость · 28.10"
    assert variants.default_name("", 2, "Окна", today=TODAY) == "Окна — вариант 2 · 28.10"
    assert variants.group_name("Окна", "выгода") == "Окна — выгода"
    assert variants.group_name("Окна — выгода", "выгода") == "Окна — выгода"


def test_normalize_per_title():
    v = variants.normalize_variants(
        [{"label": "Скорость", "ads": {"per_title": "yes", "titles": ["А", "Б", "В"], "texts": ["Т"]}}], "Окна"
    )[0]
    assert v["label"] == "Скорость"
    assert v["ads"] == {"mode": "add", "titles": ["А", "Б", "В"], "texts": ["Т"], "title2": "", "href": "",
                        "per_title": True, "keep_old": False}
    with pytest.raises(YandexError) as exc:
        variants.normalize_variants([{"ads": {"per_title": True, "mode": "replace", "titles": ["А"]}}], "Окна")
    assert exc.value.message == "Вариант 1: per_title создаёт новые объявления — только с mode=add"
    group = variants.normalize_variants([{"groups": [{"group_id": 700, "rename": "Окна — тест"}]}], "Окна")[0]
    assert group["groups"][0]["rename"] == "Окна — тест"  # одно переименование — тоже изменение


def test_create_variant_per_title_replaces_copied_ads(direct):
    direct.extras = {"sitelink_set_id": 5, "callout_ids": [1, 2]}
    v = variants.normalize_variants(
        [{"label": "Скорость", "ads": {"per_title": True, "titles": ["А", "Б"], "texts": ["Т"]}}], "Окна"
    )[0]
    out = variants.create_variant(1, 7, v, workspace_id=2, moderate=False)
    created = [c[2] for c in direct.calls if c[0] == "create_ad"]
    assert [(c["titles"], c["extras"]) for c in created] == [(["А"], direct.extras), (["Б"], direct.extras)]
    assert ("drop_ads", (1,), {}) in direct.calls  # только текстовое объявление копии, оригинал не трогаем
    assert ("rename_group", (700,), {"name": "Группа — Скорость"}) in direct.calls
    assert out["done"] == ["объявления", "названия групп"]
    assert "moderate" not in _names(direct)


def test_create_variant_per_title_keeps_old_ads_when_a_title_fails(direct):
    direct.fail = {"create_ad"}
    v = variants.normalize_variants([{"ads": {"per_title": True, "titles": ["А"], "texts": ["Т"]}}], "Окна")[0]
    out = variants.create_variant(1, 7, v, workspace_id=2, moderate=False)
    assert "drop_ads" not in _names(direct)
    assert out["ready"] is False


def test_tool_is_registered():
    assert "create_campaign_variants" in ai_agent.WRITE_TOOLS
    assert "create_campaign_variants" in ai_agent._DIFFS
    assert "create_campaign_variants" in ai_agent.TOOL_LABELS
    assert any(t["function"]["name"] == "create_campaign_variants" for t in ai_agent.TOOLS)
