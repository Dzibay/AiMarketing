"""Одни расширения многим объявлениям одним действием и ход без «продолжай»."""

import json

from app import ad_extras, ads, ai_agent, undo
from app.journal import list_action_logs
from tests.test_db_ai import _call, _pending, _secret, _start, _text, _turn  # noqa: F401 — _secret: автофикстура
from tests.test_db_ai_links import USE, _links_seed, _open


class Direct:
    """Директ в памяти: расширения объявлений, наборы быстрых ссылок и уточнения кабинета."""

    def __init__(self, ad_ids):
        self.ads = {i: {"set": None, "callouts": [], "image": None} for i in ad_ids}
        self.sets: dict[int, list[dict]] = {}
        self.callouts: dict[int, str] = {}
        self.log: list[tuple[str, str]] = []
        self.updates: list[list[dict]] = []

    def count(self, service, method):
        return self.log.count((service, method))

    def __call__(self, token, login, service, method, params, **kw):
        self.log.append((service, method))
        ids = (params.get("SelectionCriteria") or {}).get("Ids") or []
        if (service, method) == ("ads", "get"):
            return {"Ads": [
                {"Id": i, "Type": "TEXT_AD", "TextAd": {
                    "SitelinkSetId": self.ads[i]["set"],
                    "AdExtensions": [{"AdExtensionId": c, "Type": "CALLOUT"} for c in self.ads[i]["callouts"]],
                    "AdImageHash": self.ads[i]["image"],
                }}
                for i in ids if i in self.ads
            ]}
        if (service, method) == ("sitelinks", "get"):
            return {"SitelinksSets": [{"Id": i, "Sitelinks": self.sets[i]} for i in ids if i in self.sets]}
        if (service, method) == ("sitelinks", "add"):
            new = 500 + len(self.sets) + 1
            self.sets[new] = params["SitelinksSets"][0]["Sitelinks"]
            return {"AddResults": [{"Id": new}]}
        if (service, method) == ("adextensions", "add"):
            out = []
            for item in params["AdExtensions"]:
                text = item["Callout"]["CalloutText"]
                found = next((i for i, t in self.callouts.items() if t == text), None)
                if found is None:
                    found = 900 + len(self.callouts) + 1
                    self.callouts[found] = text
                out.append({"Id": found})
            return {"AddResults": out}
        if (service, method) == ("adextensions", "get"):
            return {"AdExtensions": [{"Id": i, "Callout": {"CalloutText": self.callouts[i]}} for i in ids if i in self.callouts]}
        if (service, method) == ("ads", "update"):
            self.updates.append(params["Ads"])
            for ad in params["Ads"]:
                body, now = ad["TextAd"], self.ads[ad["Id"]]
                if "SitelinkSetId" in body:
                    now["set"] = body["SitelinkSetId"]
                for op in (body.get("CalloutSetting") or {}).get("AdExtensions") or []:
                    if op["Operation"] == "ADD":
                        now["callouts"].append(op["AdExtensionId"])
                    else:
                        now["callouts"].remove(op["AdExtensionId"])
            return {"UpdateResults": [{"Id": ad["Id"]} for ad in params["Ads"]]}
        raise AssertionError(f"неожиданный вызов Директа: {service}.{method}")


def _direct(monkeypatch, ad_ids=(701, 702, 801, 802)):
    direct = Direct(ad_ids)
    monkeypatch.setattr(ads, "get_connection_access_token", lambda conn_id, user_id: "tok")
    monkeypatch.setattr(ad_extras.direct_api, "call", direct)
    return direct


def _extras(href="https://shop.ru/dostavka"):
    return {"sitelinks": [{"title": "Доставка", "href": href}], "callouts": ["Гарантия 5 лет"]}


def _rows(card):
    return {c["what"]: (c["before"], c["after"]) for c in card["changes"]}


def _stored(db, ad_row_ids):
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT sitelink_set_id, sitelink_hrefs FROM direct_ads WHERE id = ANY(%s) ORDER BY id", (ad_row_ids,)
        ).fetchall()
    return [(r["sitelink_set_id"], r["sitelink_hrefs"]) for r in rows]


def test_campaign_extras_are_read_in_one_call(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    direct = _direct(monkeypatch)
    direct.sets[501] = [{"Title": "Доставка", "Href": "https://shop.ru/dostavka?ref=Yandex"}]
    direct.callouts.update({901: "Гарантия 5 лет", 902: "Замер бесплатно"})
    direct.ads[701].update(set=501, callouts=[902, 901])

    fake, _out = _start(
        user, chat, "Какие расширения стоят в Поиске?", monkeypatch,
        _call("get_campaign_extras", {"campaign_row_id": s["search"]}), _text("Вот что стоит."),
    )
    got = json.loads(fake.calls[1][-1]["content"])
    assert got["campaign"] == "Поиск" and got["ads_total"] == 2
    first, second = s["search_ads"]
    assert [(i["ads"], i["ad_row_ids"], i["adgroups"], [l["title"] for l in i["links"]]) for i in got["sitelinks"]] == [
        (1, [first], ["Окна"], ["Доставка"]),
        (1, [second], ["Окна"], []),
    ]
    assert [(i["texts"], i["ad_row_ids"]) for i in got["callouts"]] == [
        (["Замер бесплатно", "Гарантия 5 лет"], [first]),
        ([], [second]),
    ]
    assert got["images"] == [{"count": 0, "ads": 2, "ad_row_ids": [first, second], "adgroups": ["Окна"]}]
    # Один запрос объявлений на всю кампанию, а не по запросу на объявление.
    assert direct.count("ads", "get") == 1 and direct.count("sitelinks", "get") == 1

    # Группы другой кампании — ошибка, а не чужие объявления в ответе.
    fake, _out = _start(
        user, chat, "А в этой группе?", monkeypatch,
        _call("get_campaign_extras", {"campaign_row_id": s["search"], "adgroup_row_ids": [s["network_group"]]}, "c2"),
        _text("Не та группа."),
    )
    assert "из другой кампании" in json.loads(fake.calls[1][-1]["content"])["error"]


def test_one_action_sets_extras_for_the_whole_campaign(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    direct = _direct(monkeypatch)
    ask = {"campaign_row_id": s["search"], "extras": _extras(), "reason": "В ТЗ — доставка и гарантия"}
    _fake, out = _start(user, chat, "Поставь всем объявлениям Поиска ссылку на доставку", monkeypatch, _call("set_ads_extras", ask))

    # Одна карточка на всю кампанию: в ней — сами объявления, ссылка уже с ref проекта.
    card = _pending(user, chat, "set_ads_extras")
    assert out["status"] == "waiting" and card["label"] == "Расширения объявлений"
    assert card["args"]["ad_row_ids"] == s["search_ads"] and "campaign_row_id" not in card["args"]
    assert card["args"]["extras"]["sitelinks"][0]["href"] == "https://shop.ru/dostavka?ref=Yandex"
    assert card["summary"] == "Расширения (быстрые ссылки, уточнения) — 2 объявлениям кампании «Поиск»"
    rows = _rows(card)
    assert rows["Объявления"] == ("—", "2 объявления в 1 группе кампании «Поиск»")
    assert rows["Быстрые ссылки"][0] == "нет" and "Доставка" in rows["Быстрые ссылки"][1]
    assert "Гарантия 5 лет" in rows["Уточнения"][1]
    assert [(i["id"], i["text"], i["section"]) for i in card["items"]] == [
        ("ad_row_ids.0", "Объявление 1", "Окна"),
        ("ad_row_ids.1", "Объявление 2", "Окна"),
    ]
    assert not direct.log  # до согласования в Директ не ходим

    ai_agent.decide_action(user, card["id"], approve=True)
    fake, _out = _turn(user, chat, monkeypatch, _text("Поставил."))
    done = json.loads(fake.calls[0][-1]["content"])
    assert done["ok"] and done["result"] == {"updated": 2, "unchanged": 0, "status": "ok"}
    # Набор создан один раз и встал обоим объявлениям одним вызовом; соседнюю кампанию не тронули.
    assert direct.count("sitelinks", "add") == 1 and direct.count("ads", "update") == 1
    [batch] = direct.updates
    assert [(a["Id"], a["TextAd"]["SitelinkSetId"]) for a in batch] == [(701, 501), (702, 501)]
    assert all(a["TextAd"]["CalloutSetting"] == {"AdExtensions": [{"AdExtensionId": 901, "Operation": "ADD"}]} for a in batch)
    assert direct.ads[801] == {"set": None, "callouts": [], "image": None}
    assert _stored(db, s["search_ads"]) == [(501, ["https://shop.ru/dostavka?ref=Yandex"])] * 2
    assert _stored(db, s["network_ads"]) == [(None, None)] * 2

    # В журнале — одна запись на все объявления, откат возвращает прежнее одним шагом.
    [entry] = [e for e in list_action_logs(user) if e["action"] == "ads"]
    assert entry["title"] == "Расширения объявлений: «Объявление 1», «Объявление 2»"
    assert entry["undo"]["label"] == "Вернуть прежние расширения"
    preview = undo.undo_preview(user, entry["id"])
    assert preview["available"] and len(preview["steps"]) == 1 and "Вернуть прежние" in str(preview["steps"][0])

    # Повтор того же — ничего не меняет: ни нового набора, ни правки объявлений, ни модерации.
    _start(user, chat, "Поставь ещё раз", monkeypatch, _call("set_ads_extras", ask, "c2"))
    again = _pending(user, chat, "set_ads_extras")
    assert _rows(again)["Быстрые ссылки"][0] == "у всех"
    ai_agent.decide_action(user, again["id"], approve=True)
    fake, _out = _turn(user, chat, monkeypatch, _text("Уже стоит."))
    assert json.loads(fake.calls[0][-1]["content"])["result"] == {"updated": 0, "unchanged": 2, "status": "ok"}
    assert direct.count("sitelinks", "add") == 1 and direct.count("ads", "update") == 1

    # Откат из журнала снимает поставленное.
    undo.run_undo(user, entry["id"])
    assert direct.ads[701] == {"set": None, "callouts": [], "image": None} and direct.ads[702]["set"] is None
    assert _stored(db, s["search_ads"]) == [(None, [])] * 2


def test_groups_and_unchecked_ads(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    direct = _direct(monkeypatch)
    ask = {"adgroup_row_ids": [s["network_group"]], "extras": {"callouts": ["Гарантия 5 лет"]}}
    _start(user, chat, "Уточнение в группу Окна РСЯ", monkeypatch, _call("set_ads_extras", ask))
    card = _pending(user, chat, "set_ads_extras")
    assert card["args"]["ad_row_ids"] == s["network_ads"] and "adgroup_row_ids" not in card["args"]
    assert card["summary"] == "Расширения (уточнения) — 2 объявлениям кампании «РСЯ»"

    # Сняли галочку со второго объявления — правка уходит только первому.
    ai_agent.decide_action(user, card["id"], approve=True, exclude=["ad_row_ids.1"])
    fake, _out = _turn(user, chat, monkeypatch, _text("Поставил одному."))
    done = json.loads(fake.calls[0][-1]["content"])
    assert done["result"]["updated"] == 1 and done["removed_by_user"] == ["Окна: Объявление 2"]
    assert [[a["Id"] for a in batch] for batch in direct.updates] == [[801]]
    assert direct.ads[802]["callouts"] == [] and direct.count("sitelinks", "add") == 0


def test_scope_must_be_one_campaign(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    direct = _direct(monkeypatch)
    extras = {"callouts": ["Гарантия 5 лет"]}
    wrong = [
        ({"campaign_row_id": s["search"], "ad_row_ids": s["search_ads"], "extras": extras}, "Назови объявления одним способом"),
        ({"extras": extras}, "Назови объявления одним способом"),
        ({"adgroup_row_ids": [s["search_group"], s["network_group"]], "extras": extras}, "Группы из разных кампаний"),
        ({"ad_row_ids": [s["search_ads"][0], s["network_ads"][0]], "extras": extras}, "Объявления из разных кампаний"),
        ({"campaign_row_id": s["foreign"], "extras": extras}, ""),
        ({"campaign_row_id": s["search"]}, "Укажи, что поставить"),
    ]
    for n, (args, error) in enumerate(wrong):
        fake, out = _start(user, chat, "Поставь уточнение", monkeypatch, _call("set_ads_extras", args, f"c{n}"), _text("Не вышло."))
        told = json.loads(fake.calls[1][-1]["content"])
        assert told["ok"] is False and error in told["error"], (n, told)
        assert out["status"] == "idle"
    assert not _open(user, chat, "set_ads_extras") and not direct.log


def test_sitelink_with_another_ref_asks_first(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    _direct(monkeypatch)
    ask = {"campaign_row_id": s["search"], "extras": _extras("https://shop.ru/dostavka?ref=Telegram&utm_source=tg")}
    _fake, out = _start(user, chat, "Поставь ссылку на доставку", monkeypatch, _call("set_ads_extras", ask))

    # Чужой ref в быстрой ссылке — сначала вопрос, само действие ещё не предложено.
    assert out["status"] == "waiting" and not _open(user, chat, "set_ads_extras")
    question = _pending(user, chat, "ask_questions")
    assert question["label"] == "Выбор ref в ссылке" and "кампании «Поиск»" in question["args"]["questions"][0]["question"]
    ai_agent.answer_questions(user, question["id"], answers=[{"n": 1, "selected": [USE]}])

    _turn(user, chat, monkeypatch, _call("set_ads_extras", ask, "c2"))
    card = _pending(user, chat, "set_ads_extras")
    assert card["args"]["extras"]["sitelinks"][0]["href"] == "https://shop.ru/dostavka?ref=Telegram"

    # Ссылка на чужой сайт в быстрых ссылках допустима и остаётся как есть.
    ai_agent.decide_action(user, card["id"], approve=False)
    other = {"campaign_row_id": s["search"], "extras": _extras("https://t.me/shop")}
    _turn(user, chat, monkeypatch, _call("set_ads_extras", other, "c3"))
    assert _pending(user, chat, "set_ads_extras")["args"]["extras"]["sitelinks"][0]["href"] == "https://t.me/shop"


# --- Ход без «продолжай» ---------------------------------------------------------------------------


def _last(user, chat):
    return [m for m in ai_agent.get_chat(user, chat)["messages"] if m["role"] == "assistant"][-1]


def test_long_turn_runs_to_the_end(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    _direct(monkeypatch)
    # Двенадцать шагов подряд — прежний потолок в восемь ход не обрывает.
    steps = [_call("get_campaign_extras", {"campaign_row_id": s["search"]}, f"c{n}") for n in range(12)]
    fake, out = _start(user, chat, "Проверь расширения", monkeypatch, *steps, _text("Готово: всё проверил."))
    assert out["status"] == "idle" and len(fake.calls) == 13 and not fake.replies
    assert _last(user, chat)["content"] == "Готово: всё проверил."


def test_step_ceiling_still_stops_a_runaway_turn(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    _direct(monkeypatch)
    monkeypatch.setattr(ai_agent, "MAX_STEPS", 3)
    steps = [_call("get_campaign_extras", {"campaign_row_id": s["search"]}, f"c{n}") for n in range(5)]
    fake, out = _start(user, chat, "Проверь расширения", monkeypatch, *steps)
    assert out["status"] == "idle" and len(fake.calls) == 3
    assert _last(user, chat)["content"].startswith("Сделал 3 шагов подряд и остановился")


def test_same_error_three_times_stops_the_turn(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    direct = _direct(monkeypatch)
    # Одно и то же неудачное действие с теми же аргументами: третий раз — стоп, а не сорок шагов по кругу.
    bad = {"campaign_row_id": s["search"]}
    steps = [_call("set_ads_extras", bad, f"c{n}") for n in range(6)]
    fake, out = _start(user, chat, "Поставь расширения", monkeypatch, *steps)
    assert out["status"] == "idle" and len(fake.calls) == 3 and len(fake.replies) == 3
    text = _last(user, chat)["content"]
    assert text.startswith("Остановился: «Расширения объявлений» не проходит несколько раз подряд — Укажи, что поставить")
    assert text.endswith("Напишите, как поступить, или уточните задачу.") and not direct.log


def test_errors_in_a_row_stop_the_turn_but_a_success_resets(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    _direct(monkeypatch)

    def bad(n):
        return _call("set_ads_extras", {"campaign_row_id": s["search"], "reason": f"попытка {n}"}, f"b{n}")

    ok = _call("get_campaign_extras", {"campaign_row_id": s["search"]}, "ok")
    # Разные неудачные вызовы: четыре подряд, удачный шаг обнуляет счёт, затем пять подряд — стоп.
    steps = [bad(n) for n in range(4)] + [ok] + [bad(n) for n in range(4, 12)]
    fake, out = _start(user, chat, "Поставь расширения", monkeypatch, *steps)
    assert out["status"] == "idle" and len(fake.calls) == 10
    assert _last(user, chat)["content"].startswith("Остановился: «Расширения объявлений» не проходит")
