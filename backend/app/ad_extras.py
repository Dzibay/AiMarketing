"""Быстрые ссылки, уточнения и картинки объявлений.

В Директе это отдельные объекты кабинета: набор быстрых ссылок (sitelinks), уточнения
(adextensions, тип Callout) и картинки (adimages). Объявление хранит их id и хэши. Формы
работают с текстом и ссылками, а здесь они превращаются в id перед ads.add и ads.update.
"""

from __future__ import annotations

import base64
import binascii
import logging

from app import direct_api
from app.yandex import YandexError

log = logging.getLogger("app.ad_extras")

# Пределы Директа: до 8 быстрых ссылок, заголовок до 30 символов, описание до 60;
# заголовки ссылок 1–4 и 5–8 вместе — не длиннее 66 символов каждая четвёрка.
MAX_SITELINKS = 8
SITELINK_TITLE_LEN = 30
SITELINK_DESC_LEN = 60
SITELINK_GROUP_LEN = 66
CALLOUT_LEN = 25
MAX_CALLOUTS = 10
# У текстово-графического одна картинка, у комбинаторного ЕПК — до 5.
MAX_IMAGES = {"TEXT_AD": 1, "RESPONSIVE_AD": 5}
# nginx пропускает тело до 8 МБ, а base64 раздувает файл на треть.
IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8")


def _bad(message: str) -> YandexError:
    return YandexError("extras", message)


def _href(value) -> str:
    href = str(value or "").strip()[:1024]
    if href and not href.startswith(("http://", "https://")):
        href = "https://" + href
    return href


def clean_sitelinks(raw) -> list[dict]:
    """Быстрые ссылки формы → [{title, href, description}]; ошибка с понятным текстом, а не обрезка."""
    links: list[dict] = []
    for item in raw or []:
        item = item or {}
        title = str(item.get("title") or "").strip()
        href = _href(item.get("href"))
        description = str(item.get("description") or "").strip()
        if not title and not href and not description:
            continue
        if not title or not href:
            raise _bad("У быстрой ссылки нужны и заголовок, и адрес")
        if len(title) > SITELINK_TITLE_LEN:
            raise _bad(f"Заголовок быстрой ссылки «{title}» длиннее {SITELINK_TITLE_LEN} символов")
        if len(description) > SITELINK_DESC_LEN:
            raise _bad(f"Описание быстрой ссылки «{title}» длиннее {SITELINK_DESC_LEN} символов")
        if any(link["title"].lower() == title.lower() for link in links):
            raise _bad(f"Быстрая ссылка «{title}» повторяется")
        links.append({"title": title, "href": href, "description": description})
    if len(links) > MAX_SITELINKS:
        raise _bad(f"Быстрых ссылок не больше {MAX_SITELINKS}")
    for start in (0, 4):
        group = links[start : start + 4]
        if sum(len(link["title"]) for link in group) > SITELINK_GROUP_LEN:
            raise _bad(
                f"Заголовки быстрых ссылок {start + 1}–{start + len(group)} вместе длиннее {SITELINK_GROUP_LEN} символов"
            )
    return links


def clean_callouts(raw) -> list[str]:
    out: list[str] = []
    for value in raw or []:
        text = str(value or "").strip()
        if not text or text in out:
            continue
        if len(text) > CALLOUT_LEN:
            raise _bad(f"Уточнение «{text}» длиннее {CALLOUT_LEN} символов")
        out.append(text)
    if len(out) > MAX_CALLOUTS:
        raise _bad(f"Уточнений не больше {MAX_CALLOUTS}")
    return out


def clean_images(raw, ad_type: str) -> list[str]:
    out: list[str] = []
    for value in raw or []:
        value = str(value or "").strip()
        if value and value not in out:
            out.append(value)
    limit = MAX_IMAGES.get(ad_type, 1)
    if len(out) > limit:
        raise _bad("В текстовом объявлении одна картинка" if limit == 1 else f"Картинок не больше {limit}")
    return out


def clean_extras(raw: dict | None, ad_type: str) -> dict:
    """Только те ключи, что пришли: отсутствующий ключ — «не менять», пустой список — «убрать»."""
    raw = raw or {}
    out: dict = {}
    if raw.get("sitelinks") is not None:
        out["sitelinks"] = clean_sitelinks(raw["sitelinks"])
    if raw.get("callouts") is not None:
        out["callouts"] = clean_callouts(raw["callouts"])
    if raw.get("image_hashes") is not None:
        out["image_hashes"] = clean_images(raw["image_hashes"], ad_type)
    # Форма отката: уже готовые id из Директа.
    if "sitelink_set_id" in raw:
        out["sitelink_set_id"] = int(raw["sitelink_set_id"] or 0) or None
    if raw.get("callout_ids") is not None:
        out["callout_ids"] = [int(i) for i in raw["callout_ids"] if i]
    return out


# --- Объекты кабинета ---------------------------------------------------------------------------


def create_sitelink_set(token, client_login: str, links: list[dict]) -> int:
    payload = [
        {"Title": link["title"], "Href": link["href"], **({"Description": link["description"]} if link["description"] else {})}
        for link in links
    ]
    result = direct_api.call(token, client_login, "sitelinks", "add", {"SitelinksSets": [{"Sitelinks": payload}]})
    errors = direct_api.item_errors(result, "AddResults")
    set_id = ((result.get("AddResults") or [{}])[0]).get("Id")
    if errors or not set_id:
        raise YandexError("direct", f"Быстрые ссылки: {errors[0][1] if errors else 'Директ не принял набор'}")
    return int(set_id)


def create_callouts(token, client_login: str, texts: list[str]) -> list[int]:
    """Повтор существующего уточнения Директ не создаёт заново, а возвращает его id."""
    if not texts:
        return []
    result = direct_api.call(
        token, client_login, "adextensions", "add", {"AdExtensions": [{"Callout": {"CalloutText": t}} for t in texts]}
    )
    errors = dict(direct_api.item_errors(result, "AddResults"))
    if errors:
        index, message = next(iter(errors.items()))
        raise YandexError("direct", f"Уточнение «{texts[index]}»: {message}")
    return [int(r["Id"]) for r in result.get("AddResults") or [] if r.get("Id")]


def resolve(token, client_login: str, extras: dict) -> dict:
    """Текст формы → id Директа: {sitelink_set_id?, callout_ids?, image_hashes?}."""
    out: dict = {}
    if "sitelinks" in extras:
        out["sitelink_set_id"] = create_sitelink_set(token, client_login, extras["sitelinks"]) if extras["sitelinks"] else None
    elif "sitelink_set_id" in extras:
        out["sitelink_set_id"] = extras["sitelink_set_id"]
    if "callouts" in extras:
        out["callout_ids"] = create_callouts(token, client_login, extras["callouts"])
    elif "callout_ids" in extras:
        out["callout_ids"] = list(extras["callout_ids"])
    if "image_hashes" in extras:
        out["image_hashes"] = list(extras["image_hashes"])
    return out


def add_fields(resolved: dict, ad_type: str) -> dict:
    """Поля для ads.add (TextAd или ResponsiveAd)."""
    fields: dict = {}
    if resolved.get("sitelink_set_id"):
        fields["SitelinkSetId"] = resolved["sitelink_set_id"]
    if resolved.get("callout_ids"):
        fields["AdExtensionIds"] = resolved["callout_ids"]
    images = resolved.get("image_hashes") or []
    if images:
        if ad_type == "RESPONSIVE_AD":
            fields["AdImageHashes"] = images
        else:
            fields["AdImageHash"] = images[0]
    return fields


def update_fields(resolved: dict, current: dict, ad_type: str) -> dict:
    """Поля для ads.update: None снимает ссылки и картинки, уточнения — через CalloutSetting."""
    fields: dict = {}
    if "sitelink_set_id" in resolved and resolved["sitelink_set_id"] != current.get("sitelink_set_id"):
        fields["SitelinkSetId"] = resolved["sitelink_set_id"]
    if "callout_ids" in resolved:
        new, old = resolved["callout_ids"], current.get("callout_ids") or []
        ops = [{"AdExtensionId": i, "Operation": "REMOVE"} for i in old if i not in new]
        ops += [{"AdExtensionId": i, "Operation": "ADD"} for i in new if i not in old]
        if ops:
            fields["CalloutSetting"] = {"AdExtensions": ops}
    if "image_hashes" in resolved and resolved["image_hashes"] != (current.get("image_hashes") or []):
        images = resolved["image_hashes"]
        if ad_type == "RESPONSIVE_AD":
            # В update массив только через Items; None снимает все картинки.
            fields["AdImageHashes"] = {"Items": images} if images else None
        else:
            fields["AdImageHash"] = images[0] if images else None
    return fields


# --- Чтение ------------------------------------------------------------------------------------


def _ad_refs(token, client_login: str, ad_id: int) -> dict:
    """Какие расширения сейчас у объявления в Директе."""
    result = direct_api.call(
        token,
        client_login,
        "ads",
        "get",
        {
            "SelectionCriteria": {"Ids": [int(ad_id)]},
            "FieldNames": ["Id", "Type"],
            "TextAdFieldNames": ["SitelinkSetId", "AdExtensions", "AdImageHash"],
            "ResponsiveAdFieldNames": ["SitelinkSetId", "AdExtensions", "AdImages"],
        },
        version="v501",
    )
    ads = result.get("Ads") or []
    if not ads:
        raise YandexError("not-found")
    ad = ads[0]
    body = ad.get("ResponsiveAd") or ad.get("TextAd") or {}
    if ad.get("ResponsiveAd"):
        ad_type = "RESPONSIVE_AD"
        images = [str(i["ImageHash"]) for i in (body.get("AdImages") or {}).get("Items") or [] if i.get("ImageHash")]
    else:
        ad_type = str(ad.get("Type") or "TEXT_AD")
        images = [str(body["AdImageHash"])] if body.get("AdImageHash") else []
    return {
        "type": ad_type,
        "sitelink_set_id": int(body.get("SitelinkSetId") or 0) or None,
        "callout_ids": [
            int(e["AdExtensionId"])
            for e in body.get("AdExtensions") or []
            if e.get("AdExtensionId") and e.get("Type") in (None, "CALLOUT")
        ],
        "image_hashes": images,
    }


def sitelink_sets(token, client_login: str, set_ids) -> dict[int, list[dict]]:
    """Наборы быстрых ссылок по id: {id: [{title, href, description}]}."""
    out: dict[int, list[dict]] = {}
    ids = sorted({int(i) for i in set_ids if i})
    for part in direct_api.chunks(ids, 1000):
        result = direct_api.call(
            token, client_login, "sitelinks", "get", {"SelectionCriteria": {"Ids": part}, "FieldNames": ["Id", "Sitelinks"]}
        )
        for item in result.get("SitelinksSets") or []:
            out[int(item["Id"])] = [
                {"title": link.get("Title") or "", "href": link.get("Href") or "", "description": link.get("Description") or ""}
                for link in item.get("Sitelinks") or []
            ]
    return out


def attach_sitelink_hrefs(token, client_login: str, ads: list[dict]) -> list[dict]:
    """Адреса быстрых ссылок к объявлениям из parse_ads — для проверки ссылок по шаблону.
    Не прочитались — sitelink_hrefs нет, и в базе остаются прежние адреса того же набора."""
    try:
        sets = sitelink_sets(token, client_login, [ad.get("sitelink_set_id") for ad in ads])
    except YandexError as exc:
        if exc.code in ("units", "token-expired", "unauthorized", "direct-denied", "oauth-unavailable"):
            raise
        log.warning("sitelinks.get: %s", exc.code)
        return ads
    for ad in ads:
        set_id = ad.get("sitelink_set_id")
        if not set_id:
            ad["sitelink_hrefs"] = []
        elif set_id in sets:
            ad["sitelink_hrefs"] = [link["href"] for link in sets[set_id] if link["href"]]
    return ads


def image_previews(token, client_login: str, hashes: list[str]) -> dict[str, str]:
    if not hashes:
        return {}
    found = direct_api.get_all(
        token,
        client_login,
        "adimages",
        {"SelectionCriteria": {"AdImageHashes": list(hashes)}, "FieldNames": ["AdImageHash", "PreviewUrl"]},
        "AdImages",
    )
    return {str(i["AdImageHash"]): str(i.get("PreviewUrl") or "") for i in found if i.get("AdImageHash")}


def read_extras(token, client_login: str, ad_id: int) -> dict:
    """Расширения объявления для формы: тексты, ссылки и превью картинок, плюс id для отката."""
    refs = _ad_refs(token, client_login, ad_id)
    sitelinks: list[dict] = []
    if refs["sitelink_set_id"]:
        sitelinks = sitelink_sets(token, client_login, [refs["sitelink_set_id"]]).get(refs["sitelink_set_id"], [])
    callouts: list[str] = []
    if refs["callout_ids"]:
        exts = direct_api.call(
            token,
            client_login,
            "adextensions",
            "get",
            {"SelectionCriteria": {"Ids": refs["callout_ids"]}, "FieldNames": ["Id"], "CalloutFieldNames": ["CalloutText"]},
        ).get("AdExtensions") or []
        texts = {int(e["Id"]): (e.get("Callout") or {}).get("CalloutText") for e in exts}
        callouts = [texts[i] for i in refs["callout_ids"] if texts.get(i)]
    previews = image_previews(token, client_login, refs["image_hashes"])
    return {
        **refs,
        "sitelinks": sitelinks,
        "callouts": callouts,
        "images": [{"hash": h, "preview_url": previews.get(h, "")} for h in refs["image_hashes"]],
        "max_images": MAX_IMAGES.get(refs["type"], 1),
    }


# --- Загрузка картинки -------------------------------------------------------------------------


def decode_image(data_b64: str) -> bytes:
    text = str(data_b64 or "")
    if text.startswith("data:"):
        text = text.split(",", 1)[-1]
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _bad("Файл картинки повреждён") from exc
    if not data:
        raise _bad("Пустой файл картинки")
    if len(data) > IMAGE_MAX_BYTES:
        raise _bad("Картинка больше 5 МБ")
    if not data.startswith(IMAGE_TYPES):
        raise _bad("Нужна картинка JPG, PNG или GIF")
    return data


def upload_image(token, client_login: str, *, name: str, data_b64: str) -> dict:
    """Картинка → библиотека кабинета Директа. Мелкие и неподходящие по пропорциям Директ отклонит сам."""
    data = decode_image(data_b64)
    result = direct_api.call(
        token,
        client_login,
        "adimages",
        "add",
        {"AdImages": [{"ImageData": base64.b64encode(data).decode("ascii"), "Name": (name or "image")[:255]}]},
    )
    errors = direct_api.item_errors(result, "AddResults")
    image_hash = ((result.get("AddResults") or [{}])[0]).get("AdImageHash")
    if errors or not image_hash:
        raise YandexError("direct", errors[0][1] if errors else "Директ не принял картинку")
    preview = ""
    try:
        preview = image_previews(token, client_login, [image_hash]).get(image_hash, "")
    except YandexError:
        log.warning("adimages.get preview failed")
    return {"hash": str(image_hash), "preview_url": preview}
