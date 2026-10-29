import contextlib
import base64

import pytest

from app import ad_extras
from app.ad_extras import add_fields, clean_callouts, clean_extras, clean_sitelinks, decode_image, update_fields
from app.yandex import YandexError


@contextlib.contextmanager
def _raises(text):
    with pytest.raises(YandexError) as info:
        yield
    assert text in (info.value.message or ""), info.value.message


def test_clean_sitelinks():
    got = clean_sitelinks(
        [
            {"title": " Каталог ", "href": "site.ru/catalog", "description": "Все цены"},
            {"title": "", "href": "", "description": ""},
            {"title": "Доставка", "href": "https://site.ru/d"},
        ]
    )
    assert got == [
        {"title": "Каталог", "href": "https://site.ru/catalog", "description": "Все цены"},
        {"title": "Доставка", "href": "https://site.ru/d", "description": ""},
    ]
    with _raises("и заголовок, и адрес"):
        clean_sitelinks([{"title": "Без адреса"}])
    with _raises("длиннее 30"):
        clean_sitelinks([{"title": "x" * 31, "href": "a.ru"}])
    with _raises("повторяется"):
        clean_sitelinks([{"title": "А", "href": "a.ru"}, {"title": "а", "href": "a.ru/2"}])
    with _raises("не больше 8"):
        clean_sitelinks([{"title": f"С{i}", "href": "a.ru"} for i in range(9)])
    # Предела «1–4 вместе до 66 символов» Директ больше не держит: четвёрка по 21 символу проходит.
    ok = [{"title": f"{i}" + "я" * 20, "href": "a.ru"} for i in range(8)]
    assert len(clean_sitelinks(ok)) == 8


def test_clean_callouts_and_extras():
    assert clean_callouts([" Доставка ", "Доставка", "", "Гарантия"]) == ["Доставка", "Гарантия"]
    with _raises("длиннее 25"):
        clean_callouts(["x" * 26])
    assert clean_extras(None, "TEXT_AD") == {}
    assert clean_extras({"callouts": []}, "TEXT_AD") == {"callouts": []}
    with _raises("одна картинка"):
        clean_extras({"image_hashes": ["a", "b"]}, "TEXT_AD")
    assert clean_extras({"image_hashes": ["a", "b", "a"]}, "RESPONSIVE_AD") == {"image_hashes": ["a", "b"]}
    # Форма отката — id как есть.
    assert clean_extras({"sitelink_set_id": None, "callout_ids": [3, 4]}, "TEXT_AD") == {
        "sitelink_set_id": None,
        "callout_ids": [3, 4],
    }


def test_add_and_update_fields():
    resolved = {"sitelink_set_id": 10, "callout_ids": [1, 2], "image_hashes": ["h1", "h2"]}
    assert add_fields(resolved, "TEXT_AD") == {"SitelinkSetId": 10, "AdExtensionIds": [1, 2], "AdImageHash": "h1"}
    assert add_fields(resolved, "RESPONSIVE_AD")["AdImageHashes"] == ["h1", "h2"]
    assert add_fields({"sitelink_set_id": None, "callout_ids": []}, "TEXT_AD") == {}

    current = {"sitelink_set_id": 9, "callout_ids": [1, 5], "image_hashes": ["h0"]}
    got = update_fields(resolved, current, "RESPONSIVE_AD")
    assert got["SitelinkSetId"] == 10
    assert got["CalloutSetting"] == {
        "AdExtensions": [{"AdExtensionId": 5, "Operation": "REMOVE"}, {"AdExtensionId": 2, "Operation": "ADD"}]
    }
    assert got["AdImageHashes"] == {"Items": ["h1", "h2"]}
    # Снять всё.
    got = update_fields({"sitelink_set_id": None, "callout_ids": [], "image_hashes": []}, current, "TEXT_AD")
    assert got == {
        "SitelinkSetId": None,
        "CalloutSetting": {
            "AdExtensions": [{"AdExtensionId": 1, "Operation": "REMOVE"}, {"AdExtensionId": 5, "Operation": "REMOVE"}]
        },
        "AdImageHash": None,
    }
    assert update_fields({"image_hashes": []}, {"image_hashes": ["h"]}, "RESPONSIVE_AD") == {"AdImageHashes": None}
    # Ничего не поменялось — пусто.
    assert update_fields(current, current, "TEXT_AD") == {}


def test_resolve_creates_objects(monkeypatch):
    calls = []

    def fake_call(token, login, service, method, params, **kw):
        calls.append((service, method, params))
        if service == "sitelinks":
            return {"AddResults": [{"Id": 77}]}
        return {"AddResults": [{"Id": 100 + i} for i, _ in enumerate(params["AdExtensions"])]}

    monkeypatch.setattr(ad_extras.direct_api, "call", fake_call)
    got = ad_extras.resolve(
        "t",
        "c",
        {
            "sitelinks": [{"title": "Каталог", "href": "https://a.ru", "description": ""}],
            "callouts": ["Доставка", "Гарантия"],
            "image_hashes": ["h"],
        },
    )
    assert got == {"sitelink_set_id": 77, "callout_ids": [100, 101], "image_hashes": ["h"]}
    assert calls[0][2] == {"SitelinksSets": [{"Sitelinks": [{"Title": "Каталог", "Href": "https://a.ru"}]}]}
    # Пустые списки — снять, без вызовов Директа.
    calls.clear()
    assert ad_extras.resolve("t", "c", {"sitelinks": [], "callouts": []}) == {"sitelink_set_id": None, "callout_ids": []}
    assert not calls


def test_decode_image():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 10
    assert decode_image(base64.b64encode(png).decode()) == png
    assert decode_image("data:image/png;base64," + base64.b64encode(png).decode()) == png
    with _raises("JPG, PNG или GIF"):
        decode_image(base64.b64encode(b"hello").decode())
    with _raises("повреждён"):
        decode_image("не base64!")
