"""ИИ-ассистент без БД и модели: перезапуск копией, системные правила, запомненные ответы."""

from types import SimpleNamespace

import pytest

from app import actions, ai_agent
from app.workspaces import SCHEME_FACT, fact_key, normalize_ai_access, normalize_ai_facts
from app.yandex import YandexError

SOURCE = {"id": 7, "name": "Поиск Москва", "state": "ON", "status": "ACCEPTED", "account_id": 3}


class FakeContext:
    """Context без БД: пространство из словаря и одна кампания в нём."""

    def __init__(self, ws: dict | None = None):
        self.user_id = 1
        self.ws_id = 2
        self.ws = {"name": "Окна", "counter_id": 555, "accounts": [], **(ws or {})}
        self.access = normalize_ai_access(self.ws.get("ai_access"))
        self.chat_id = None

    def campaign(self, campaign_row_id: int) -> dict:
        if campaign_row_id != SOURCE["id"]:
            raise YandexError("scope", f"Кампании [{campaign_row_id}] не входят в это пространство")
        return SOURCE


@pytest.fixture
def direct(monkeypatch):
    """Копия, модерация и пауза без Директа; calls — что ушло бы в Директ."""
    fake = SimpleNamespace(
        calls=[],
        copy_status="ok",
        moderation={"drafts": 2, "sent": 2, "errors": [], "status": "ok"},
        pause_error=None,
    )

    def duplicate(user_id, row_id, *, name=None, workspace_id=None, **kw):
        fake.calls.append(("copy", row_id, name, workspace_id))
        return {"campaign": {"id": 70, "name": name or f"{SOURCE['name']} (копия)"}, "groups": 1, "keywords": 5,
                "autotargeting": 0, "ads": 2, "warnings": [], "status": fake.copy_status}

    def moderate(user_id, row_id):
        fake.calls.append(("moderate", row_id))
        if isinstance(fake.moderation, Exception):
            raise fake.moderation
        return fake.moderation

    def bulk(user_id, *, campaign_ids, action, workspace_id=None, **kw):
        fake.calls.append((action, campaign_ids, workspace_id))
        if fake.pause_error:
            raise fake.pause_error
        return {"status": "ok"}

    monkeypatch.setattr(actions, "duplicate_campaign", duplicate)
    monkeypatch.setattr(actions, "moderate_campaign", moderate)
    monkeypatch.setattr(actions, "bulk_campaign_action", bulk)
    return fake


@pytest.mark.parametrize(
    "value, expected",
    [(None, True), ("", True), (True, True), (1, True), ("true", True), ("да", True),
     (False, False), (0, False), ("false", False), ("0", False), ("no", False), (" Нет ", False)],
)
def test_bool_arg(value, expected):
    assert ai_agent._bool({"flag": value}, "flag", True) is expected


def test_bool_arg_missing_uses_default():
    assert ai_agent._bool({}, "flag", False) is False


def test_restart_plan_lists_steps_and_categories():
    ctx = FakeContext()
    plan = ai_agent._p_restart(ctx, {"campaign_row_id": 7})
    assert plan.summary == (
        "Перезапуск «Поиск Москва» копией: копия «Поиск Москва (копия)» в том же кабинете → на модерацию"
        " → оригинал на паузу"
    )
    assert plan.categories == ["campaigns", "pause_resume"]

    keep = ai_agent._p_restart(ctx, {"campaign_row_id": 7, "name": "Поиск Москва 2", "pause_source": False})
    assert "копия «Поиск Москва 2»" in keep.summary
    assert keep.summary.endswith("на модерацию, оригинал работает")
    assert keep.categories == ["campaigns"]  # без паузы оригинала — категория «пауза» не нужна


def test_restart_foreign_campaign_is_refused(direct):
    with pytest.raises(YandexError):
        ai_agent._x_restart(FakeContext(), {"campaign_row_id": 8})
    assert direct.calls == []


def test_restart_pauses_source_after_copy_went_to_moderation(direct):
    out = ai_agent._x_restart(FakeContext(), {"campaign_row_id": 7})
    assert direct.calls == [("copy", 7, None, 2), ("moderate", 70), ("pause", [7], 2)]
    assert out["source_paused"] is True
    assert out["copy"] == {"campaign_row_id": 70, "name": "Поиск Москва (копия)", "status": "ok",
                           "groups": 1, "keywords": 5, "ads": 2}
    assert "note" not in out


@pytest.mark.parametrize("broken", ["partial_copy", "nothing_sent", "moderation_error"])
def test_restart_keeps_source_running_when_copy_is_not_ready(direct, broken):
    """Без готовой копии оригинал не останавливаем — иначе кампания осталась бы совсем без показов."""
    if broken == "partial_copy":
        direct.copy_status = "partial"
    elif broken == "nothing_sent":
        direct.moderation = {"drafts": 0, "sent": 0, "errors": [], "status": "ok"}
    else:
        direct.moderation = YandexError("direct", "Объявления не отправлены")
    out = ai_agent._x_restart(FakeContext(), {"campaign_row_id": 7})
    assert [call[0] for call in direct.calls] == ["copy", "moderate"]
    assert out["source_paused"] is False
    assert out["note"].startswith("Оригинал не остановлен")
    if broken == "moderation_error":
        assert out["moderation"] == {"status": "error", "error": "Объявления не отправлены"}


def test_restart_without_pause_leaves_source_alone(direct):
    out = ai_agent._x_restart(FakeContext(), {"campaign_row_id": 7, "name": " Поиск 2 ", "pause_source": "false"})
    assert direct.calls == [("copy", 7, "Поиск 2", 2), ("moderate", 70)]
    assert out["source_paused"] is False
    assert out["note"] == "Оригинал работает — остановите его, когда копия пройдёт модерацию"


def test_restart_reports_failed_pause(direct):
    direct.pause_error = YandexError("direct", "Кампания в архиве")
    out = ai_agent._x_restart(FakeContext(), {"campaign_row_id": 7})
    assert out["source_paused"] is False
    assert out["note"] == "Оригинал не остановлен: Кампания в архиве"


def test_every_tool_has_schema_and_label():
    schemas = {tool["function"]["name"] for tool in ai_agent.TOOLS}
    assert schemas == set(ai_agent.READ_TOOLS) | set(ai_agent.WRITE_TOOLS) | set(ai_agent.ASK_TOOLS)
    assert set(ai_agent.WRITE_TOOLS) | set(ai_agent.ASK_TOOLS) <= set(ai_agent.TOOL_LABELS)


@pytest.fixture
def no_memory(monkeypatch):
    monkeypatch.setattr(ai_agent, "memory_examples", lambda ws_id, limit: {})
    monkeypatch.setattr(ai_agent, "_running_experiments", lambda ctx: [])


def test_prompt_offers_scheme_hypothesis_until_marketer_confirms(no_memory):
    prompt = ai_agent._system_prompt(FakeContext())
    assert f"key {SCHEME_FACT}" in prompt
    assert "restart_campaign_copy" in prompt
    assert "Что маркетолог уже ответил" not in prompt

    fact = {"key": SCHEME_FACT, "question": "Как работает схема 0 ₽?", "answer": "Платим только за заказ"}
    prompt = ai_agent._system_prompt(FakeContext({"ai_facts": [fact, {"key": "", "answer": "без ключа"}]}))
    assert f"key {SCHEME_FACT}" not in prompt
    assert "Что маркетолог уже ответил (учитывай, не переспрашивай):\n- Как работает схема 0 ₽?: Платим только за заказ" in prompt
    assert "без ключа" not in prompt


def test_prompt_access_policy(no_memory):
    assert "Без согласования разрешено: ничего." in ai_agent._system_prompt(FakeContext())
    full = ai_agent._system_prompt(FakeContext({"ai_access": {"full_access": True}}))
    assert "Полный доступ: все действия выполняются сразу" in full


def test_fact_key_and_normalize_facts():
    assert fact_key("  Zero Cost  scheme ") == "zero_cost_scheme"
    assert len(fact_key("x" * 100)) == 60
    raw = [
        {"key": "Схема", "question": " Как? ", "answer": " Так ", "at": "2026-10-27T10:00:00+03:00"},
        {"key": "пусто", "answer": "  "},
        {"key": "", "answer": "без ключа"},
        "мусор",
    ]
    assert normalize_ai_facts(raw) == [
        {"key": "схема", "question": "Как?", "answer": "Так", "at": "2026-10-27T10:00:00+03:00"}
    ]
    assert normalize_ai_facts(None) == []
    assert normalize_ai_facts({"key": "x", "answer": "y"}) == []


# --- get_campaign: компактно и частями ---------------------------------------------------------


def _group(gid, name, cost=0, negatives=()):
    return {"id": gid, "name": name, "status": "ACCEPTED", "type": "TEXT_AD_GROUP", "region_ids": [213],
            "negative_keywords": list(negatives), "period_impressions": 100, "period_clicks": 10,
            "period_cost_kopecks": cost, "period_conversions": 1, "stats_from": "2026-10-21", "stats_to": "2026-10-27"}


def _ad(aid, gid, titles, texts, state="ON", **kw):
    return {"id": aid, "adgroup_row_id": gid, "type": "RESPONSIVE_AD", "state": state, "status": "ACCEPTED",
            "titles": titles, "texts": texts, "title2": "", "href": "https://okna.ru", "sitelink_set_id": None,
            "image_hashes": [], **kw}


def _kw(kid, gid, phrase, cost=0):
    return {"id": kid, "adgroup_row_id": gid, "keyword": phrase, "state": "ON", "status": "ACCEPTED", "bid_rub": 12.5,
            "period_impressions": 50, "period_clicks": 5, "period_cost_kopecks": cost, "period_conversions": 0,
            "period_cpa_rub": None, "stats_from": "2026-10-21", "stats_to": "2026-10-27"}


@pytest.fixture
def campaign_data(monkeypatch):
    """Структура кампании 7 без БД: заполни data.groups, data.ads, data.keywords, data.negatives."""
    from app import adgroups, ads, direct_sync, keywords

    data = SimpleNamespace(groups=[], ads=[], keywords=[], negatives=[])
    monkeypatch.setattr(adgroups, "list_adgroups", lambda user_id, cid: [dict(g) for g in data.groups])
    monkeypatch.setattr(ads, "list_ads", lambda user_id, campaign_row_id=None: [dict(a) for a in data.ads])
    monkeypatch.setattr(keywords, "list_keywords", lambda user_id, campaign_row_id=None: [dict(k) for k in data.keywords])
    monkeypatch.setattr(
        direct_sync, "get_campaign",
        lambda user_id, cid: {"id": cid, "name": SOURCE["name"], "strategy": "WB_MAXIMUM_CLICKS",
                              "daily_budget_kopecks": 150000, "negative_keywords": list(data.negatives)},
    )
    return data


def test_campaign_is_compact(campaign_data):
    campaign_data.groups = [_group(12, "Двери"), _group(11, "Окна", cost=50000, negatives=[f"минус {i}" for i in range(12)])]
    same = {"titles": ["Окна ПВХ за день"], "texts": ["Замер бесплатно"], "image_hashes": ["h1"], "sitelink_set_id": 5}
    campaign_data.ads = [
        _ad(104, 12, ["Двери в квартиру"], ["Установка за час"]),
        _ad(103, 11, ["Старое"], ["В архиве"], state="ARCHIVED"),
        _ad(102, 12, **same),
        _ad(101, 11, **same),
    ]
    campaign_data.keywords = [_kw(1, 12, "двери"), _kw(2, 11, "окна пвх", cost=30000)]
    campaign_data.negatives = ["бесплатно", "своими руками"]

    out = ai_agent._t_campaign(FakeContext(), {"campaign_row_id": 7})
    assert out["campaign"]["name"] == "Поиск Москва"
    assert out["campaign"]["negative_keywords"] == ["бесплатно", "своими руками"]
    assert out["stats_period"] == "2026-10-21 — 2026-10-27"
    # Группы — по расходу, с числом объявлений и фраз и первыми 10 минус-фразами.
    assert [g["id"] for g in out["adgroups"]] == [11, 12]
    first = out["adgroups"][0]
    assert (first["ads"], first["keywords"], first["negative_keywords_total"], len(first["negative_keywords"])) == (1, 1, 12, 10)
    assert "stats_from" not in first and "account_id" not in first
    # Архивные не показываем, одинаковые — ссылкой на первое такое же.
    assert out["ads_archived"] == 1 and out["ads_duplicates"] == 1 and out["ads_total"] == 3
    assert out["ads"] == [
        {"id": 101, "adgroup_row_id": 11, "state": "ON", "status": "ACCEPTED", "type": "RESPONSIVE_AD",
         "titles": ["Окна ПВХ за день"], "texts": ["Замер бесплатно"], "href": "https://okna.ru", "sitelinks": True,
         "images": 1},
        {"id": 102, "adgroup_row_id": 12, "state": "ON", "status": "ACCEPTED", "same_as": 101},
        {"id": 104, "adgroup_row_id": 12, "state": "ON", "status": "ACCEPTED", "type": "RESPONSIVE_AD",
         "titles": ["Двери в квартиру"], "texts": ["Установка за час"], "href": "https://okna.ru", "sitelinks": False,
         "images": 0},
    ]
    assert [k["keyword"] for k in out["keywords"]] == ["окна пвх", "двери"]
    assert "adgroup_name" not in out["keywords"][0] and "stats_from" not in out["keywords"][0]
    assert "not_all_shown" not in out


def test_campaign_comes_in_parts_that_fit_the_limit(campaign_data):
    """Большая кампания: каждая часть влезает в ответ без обрезки, а вместе части дают всё."""
    import re

    campaign_data.groups = [_group(i, f"Группа {i} " + "г" * 60, cost=i, negatives=["м" * 30] * 12) for i in range(1, 41)]
    campaign_data.ads = [
        _ad(1000 + i, 1 + i % 40, [f"Заголовок {i} {j} " + "з" * 40 for j in range(7)], [f"Текст {i} {j} " + "т" * 70 for j in range(3)])
        for i in range(60)
    ]
    campaign_data.keywords = [_kw(i, 1 + i % 40, f"фраза номер {i} " + "ф" * 30, cost=i) for i in range(300)]
    campaign_data.negatives = [f"минус-фраза {i}" for i in range(400)]

    seen, offset = [], 0
    while True:
        out = ai_agent._t_campaign(FakeContext(), {"campaign_row_id": 7, "ads_offset": offset})
        assert "[ответ обрезан" not in ai_agent._dump({"ok": True, **out})
        seen += [a["id"] for a in out["ads"]]
        note = out["not_all_shown"]
        assert "Об остальном выводов не делай" in note and "минус-фразы кампании" in note
        assert out["campaign"]["negative_keywords_total"] == 400
        found = re.search(r"ads_offset=(\d+)", note)
        if not found:
            break
        offset = int(found.group(1))
    # Порядок — по группам, внутри группы по id.
    assert seen == [a["id"] for a in sorted(campaign_data.ads, key=lambda a: (a["adgroup_row_id"], a["id"]))]
    assert out["ads_total"] == 60 and out["keywords_total"] == 300 and out["adgroups_total"] == 40

    past = ai_agent._t_campaign(FakeContext(), {"campaign_row_id": 7, "keywords_offset": 500})
    assert past["keywords"] == [] and "фразы: всего 300, с 501-й ничего нет" in past["not_all_shown"]


def test_campaign_only_chosen_groups(campaign_data):
    campaign_data.groups = [_group(11, "Окна"), _group(12, "Двери")]
    campaign_data.ads = [_ad(101, 11, ["Окна"], ["Текст"]), _ad(102, 12, ["Двери"], ["Текст"])]
    campaign_data.keywords = [_kw(1, 11, "окна"), _kw(2, 12, "двери")]
    out = ai_agent._t_campaign(FakeContext(), {"campaign_row_id": 7, "adgroup_row_ids": [12]})
    assert [g["id"] for g in out["adgroups"]] == [12]
    assert [a["id"] for a in out["ads"]] == [102] and [k["id"] for k in out["keywords"]] == [2]
    with pytest.raises(YandexError) as exc:
        ai_agent._t_campaign(FakeContext(), {"campaign_row_id": 7, "adgroup_row_ids": [12, 99]})
    assert exc.value.message == "В кампании нет групп [99]"


def test_dump_says_what_was_cut():
    text = ai_agent._dump({"items": ["x" * 50] * 10}, limit=100)
    assert text.startswith('{"items": ["xxx')
    assert text.endswith("… [ответ обрезан: показано 100 из 551 знаков — об остальном выводов не делай, "
                         "запроси по частям или скажи, что видел не всё]")


# --- update_text_ads: несколько объявлений одной карточкой --------------------------------------

ADS = {
    101: {"id": 101, "title": "Окна ПВХ", "type": "RESPONSIVE_AD", "account_id": 3, "adgroup_row_id": 11, "campaign_row_id": 7},
    102: {"id": 102, "title": "Двери", "type": "RESPONSIVE_AD", "account_id": 3, "adgroup_row_id": 12, "campaign_row_id": 7},
}


class AdsContext(FakeContext):
    def ads(self, ids: list[int]) -> list[dict]:
        foreign = [i for i in ids if i not in ADS]
        if foreign:
            raise YandexError("scope", f"Объявления {foreign} не входят в это пространство")
        return [ADS[i] for i in ids]


BULK = {"items": [{"ad_row_id": 101, "titles": ["Окна за день", "Окна ПВХ"]}, {"ad_row_id": 102, "texts": ["Двери за час"]}]}


def test_bulk_ads_plan_and_card(monkeypatch):
    plan = ai_agent._p_update_ads(AdsContext(), BULK)
    assert plan.summary == "Правка 2 объявлений: «Окна ПВХ», «Двери»" and plan.categories == ["ads"]

    monkeypatch.setattr(
        ai_agent, "_d_update_ad",
        lambda ctx, item: [ai_agent._ch(k, "было", " | ".join(item[k])) for k in ("titles", "texts") if k in item],
    )
    same = {"titles": ["Окна и двери"], "texts": ["Под ключ"]}
    rows = ai_agent._d_update_ads(AdsContext(), {"items": [{"ad_row_id": 101, **same}, {"ad_row_id": 102, **same}]})
    assert rows[0] == {"what": "Станут одинаковыми", "before": "—", "after": "«Окна ПВХ», «Двери»"}
    assert rows[1:] == [
        {"what": "«Окна ПВХ»: titles", "before": "было", "after": "Окна и двери"},
        {"what": "«Окна ПВХ»: texts", "before": "было", "after": "Под ключ"},
        {"what": "«Двери»: titles", "before": "было", "after": "Окна и двери"},
        {"what": "«Двери»: texts", "before": "было", "after": "Под ключ"},
    ]


@pytest.mark.parametrize(
    "args, error",
    [
        ({}, "items — непустой список правок {ad_row_id, поля}"),
        ({"items": [{"ad_row_id": 101, "title": "А"}, {"ad_row_id": 101, "text": "Б"}]},
         "Одно объявление указано дважды — собери его правки в одну запись"),
        ({"items": [{"ad_row_id": 101, "title": "А"}, {"ad_row_id": 102, "titles": []}]},
         "Объявление «Двери»: titles не может быть пустым"),
        ({"items": [{"ad_row_id": 101}]},
         "Объявление «Окна ПВХ»: Укажите, что поменять: title, title2, text, href, titles, texts или extras"),
        ({"items": [{"ad_row_id": 101, "title": "А"}] * 31}, "За раз — до 30 объявлений"),
    ],
)
def test_bulk_ads_errors(args, error):
    with pytest.raises(YandexError) as exc:
        ai_agent._p_update_ads(AdsContext(), args)
    assert exc.value.message == error


def test_bulk_ads_say_what_is_already_changed(monkeypatch):
    from app import ads

    calls = []

    def update(user_id, ad_row_id, **fields):
        calls.append((ad_row_id, fields["titles"], fields["texts"]))
        if ad_row_id == 102:
            raise YandexError("direct", "Текст не прошёл проверку")
        return {"id": ad_row_id}

    monkeypatch.setattr(ads, "update_text_ad", update)
    with pytest.raises(YandexError) as exc:
        ai_agent._x_update_ads(AdsContext(), BULK)
    assert exc.value.message == "Изменены: «Окна ПВХ»; не изменены: «Двери» — Текст не прошёл проверку"
    assert calls == [(101, ["Окна за день", "Окна ПВХ"], None), (102, None, ["Двери за час"])]

    monkeypatch.setattr(ads, "update_text_ad", lambda user_id, ad_row_id, **fields: {"id": ad_row_id})
    assert ai_agent._x_update_ads(AdsContext(), BULK) == {"updated": 2, "ads": ["«Окна ПВХ»", "«Двери»"]}


def test_campaign_number_only_for_same_names(monkeypatch):
    assert ai_agent._cname(SOURCE) == "«Поиск Москва»"
    assert ai_agent._cname({"name": "Поиск", "campaign_id": 123, "twin": False}) == "«Поиск»"
    twins = [{"name": "Поиск", "campaign_id": 123, "twin": True}, {"name": "Поиск", "campaign_id": 456, "twin": True}]
    assert ai_agent._cnames(twins) == "«Поиск» №123, «Поиск» №456"
    assert ai_agent._cnames(twins * 2) == "«Поиск» №123, «Поиск» №456, «Поиск» №123 и ещё 1"

    # В карточке — номер, в названии новой копии — нет.
    monkeypatch.setattr(FakeContext, "campaign", lambda self, cid: {**SOURCE, "campaign_id": 123, "twin": True})
    assert ai_agent._p_restart(FakeContext(), {"campaign_row_id": 7}).summary == (
        "Перезапуск «Поиск Москва» №123 копией: копия «Поиск Москва (копия)» в том же кабинете → на модерацию"
        " → оригинал на паузу"
    )


def test_negatives_card_counts_only_new(monkeypatch):
    monkeypatch.setattr(ai_agent, "_campaign_negatives", lambda ctx, cid: ["бесплатно", "Своими руками"])
    ctx = FakeContext()
    args = {"campaign_row_id": 7, "phrases": ["своими  руками", "бу", "Бесплатно", "отзывы", "БУ"]}
    assert ai_agent._p_add_negatives(ctx, args).summary == (
        "Минус-фразы кампании «Поиск Москва»: +2 (2 уже есть): бу, отзывы"
    )
    assert ai_agent._d_add_negatives(ctx, args) == [
        {"what": "Минус-фразы кампании", "before": "2 фразы", "after": "4 фразы"},
        {"what": "Добавятся", "before": "—", "after": "бу, отзывы"},
        {"what": "Уже есть", "before": "своими  руками, Бесплатно", "after": "без изменений"},
    ]
    one = {"campaign_row_id": 7, "phrases": ["бесплатно"]}
    assert ai_agent._p_add_negatives(ctx, one).summary == "Минус-фразы кампании «Поиск Москва»: +0 — фраза уже есть"
    both = {"campaign_row_id": 7, "phrases": ["бесплатно", "своими руками"]}
    assert ai_agent._p_add_negatives(ctx, both).summary == "Минус-фразы кампании «Поиск Москва»: +0 — все 2 уже есть"


class GroupContext(FakeContext):
    def adgroup(self, adgroup_row_id: int) -> dict:
        return {"id": adgroup_row_id, "name": "Окна", "campaign_row_id": 7}


def test_keywords_card_counts_only_new(monkeypatch):
    monkeypatch.setattr(ai_agent, "_group_keywords", lambda ctx, gid: ["окна пвх", "купить окна"])
    args = {"adgroup_row_id": 5, "phrases": ["Окна ПВХ", "окна на заказ"], "bid_rub": 12}
    assert ai_agent._p_add_keywords(GroupContext(), args).summary == (
        "Фразы в группу «Окна»: +1 (1 уже есть): окна на заказ (ставка 12 ₽)"
    )
    args = {"adgroup_row_id": 5, "phrases": ["Окна ПВХ"], "bid_rub": 12}
    assert ai_agent._p_add_keywords(GroupContext(), args).summary == "Фразы в группу «Окна»: +0 — фраза уже есть"
