"""Варианты кампании: до 5 копий одной кампании с разными параметрами для теста.

Вариант — копия со структурой, целями и оплатой оригинала (duplicate_campaign); меняется только то,
что в нём задано: регионы, расписание, ценности ключевых целей, недельный бюджет, тексты объявлений и
ключевые фразы. Оригинал не трогаем. Тексты правим в скопированных объявлениях, а не создаём новые:
так у варианта остаются быстрые ссылки, уточнения и картинки оригинала.

В кампании с несколькими группами фразы и объявления задаются по группам (groups): у каждой группы
оригинала свои фразы и тексты, ненужную группу можно не копировать, а для теста — добавить новую.

Тест заголовков — ads.per_title: по объявлению на каждый заголовок с теми же текстами, ссылкой и
расширениями. Директ не отдаёт статистику заголовков внутри комбинаторного объявления, а у объявления
с одним заголовком его цифры и есть цифры заголовка. Копия и изменённые группы называются по гипотезе
и дате, чтобы тест было видно в кабинете.
"""

from __future__ import annotations

import logging

from app import actions, direct_api, experiments, geo
from app.ads import MAX_TEXTS, MAX_TITLES, TEXT_LEN, TITLE2_LEN, TITLE_LEN
from app.clock import today_msk
from app.yandex import YandexError

log = logging.getLogger(__name__)

MAX_VARIANTS = 5
MAX_PHRASES = 200
MAX_GROUP_ITEMS = 20
# Заголовков в тесте per_title: по объявлению на каждый, в группе Директа до 50 объявлений.
MAX_PER_TITLE = 10
LABEL_LEN = 40
# create_keywords и bulk_keyword_action берут до 100 фраз за раз.
KEYWORD_BATCH = 100
AD_MODES = ("replace", "add")
KEYWORD_MODES = ("add", "replace")
TEXT_AD_TYPES = ("TEXT_AD", "RESPONSIVE_AD")
AUTOTARGETING = "---autotargeting"
# Что вариант может поменять; без хотя бы одного из них вариант — просто копия.
CHANGE_KEYS = ("region_ids", "schedule", "priority_goals", "weekly_budget_rub", "ads", "keywords", "groups")


def _text(value, limit: int) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def _strings(value, who: str, key: str, limit: int, length: int) -> list[str]:
    """Заголовки или тексты: длиннее предела Директа не обрезаем молча — модель должна переписать."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise YandexError("args", f"{who}: {key} — список строк")
    out: list[str] = []
    for item in value:
        text = _text(item, 10_000)
        if not text or text in out:
            continue
        if len(text) > length:
            raise YandexError("args", f"{who}: «{text[:40]}…» длиннее {length} символов — сократи")
        out.append(text)
    if len(out) > limit:
        raise YandexError("args", f"{who}: {key} — не больше {limit}")
    return out


def _flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "да")
    return bool(value)


def _ads(raw, who: str) -> dict:
    if not isinstance(raw, dict):
        raise YandexError("args", f"{who}: ads — объект {{titles, texts, href, mode, per_title}}")
    per_title = _flag(raw.get("per_title"))
    mode = str(raw.get("mode") or ("add" if per_title else "replace")).strip().lower()
    if mode not in AD_MODES:
        raise YandexError("args", f"{who}: ads.mode — одно из {list(AD_MODES)}")
    if per_title and mode != "add":
        raise YandexError("args", f"{who}: per_title создаёт новые объявления — только с mode=add")
    limit = MAX_PER_TITLE if per_title else MAX_TITLES
    titles = _strings(raw.get("titles") or raw.get("title"), who, "titles", limit, TITLE_LEN)
    texts = _strings(raw.get("texts") or raw.get("text"), who, "texts", MAX_TEXTS, TEXT_LEN)
    title2 = _text(raw.get("title2"), 1000)
    if len(title2) > TITLE2_LEN:
        raise YandexError("args", f"{who}: title2 длиннее {TITLE2_LEN} символов — сократи")
    if mode == "add" and not (titles and texts):
        raise YandexError("args", f"{who}: для нового объявления (mode=add) нужны titles и texts")
    if not titles and not texts:
        raise YandexError("args", f"{who}: в ads нет ни titles, ни texts")
    out = {"mode": mode, "titles": titles, "texts": texts, "title2": title2, "href": _text(raw.get("href"), 1024)}
    if per_title:
        out["per_title"] = True
        # Старые объявления копии с несколькими заголовками размывают тест — по умолчанию убираем.
        out["keep_old"] = _flag(raw.get("keep_old"))
    return out


def _keywords(raw, who: str) -> dict:
    if isinstance(raw, list):
        raw = {"phrases": raw}
    if not isinstance(raw, dict):
        raise YandexError("args", f"{who}: keywords — объект {{phrases, mode}}")
    mode = str(raw.get("mode") or "add").strip().lower()
    if mode not in KEYWORD_MODES:
        raise YandexError("args", f"{who}: keywords.mode — одно из {list(KEYWORD_MODES)}")
    phrases = raw.get("phrases")
    if isinstance(phrases, str):
        phrases = phrases.splitlines()
    phrases = list(dict.fromkeys(p for p in (_text(x, 4096) for x in phrases or []) if p))
    if not phrases:
        raise YandexError("args", f"{who}: в keywords нет фраз")
    if len(phrases) > MAX_PHRASES:
        raise YandexError("args", f"{who}: не больше {MAX_PHRASES} фраз")
    bid = experiments._number(raw.get("bid_rub"), "bid_rub")
    if bid is not None and bid <= 0:
        raise YandexError("args", f"{who}: bid_rub больше нуля")
    return {"mode": mode, "phrases": phrases, "bid_rub": bid}


def _group_item(item, who: str) -> dict:
    """Группа оригинала (group_id): свои фразы и объявления или remove; новая группа (name): фразы и объявление."""
    if not isinstance(item, dict):
        raise YandexError("args", f"{who} — объект {{group_id, keywords, ads, remove}} или {{name, keywords, ads}}")
    keywords, ads = item.get("keywords"), item.get("ads")
    if item.get("group_id") in (None, ""):
        name = _text(item.get("name"), 255)
        if not name:
            raise YandexError("args", f"{who}: group_id группы оригинала (из get_campaign) или name новой группы")
        if item.get("remove"):
            raise YandexError("args", f"{who}: remove — только для группы оригинала с group_id")
        if keywords in (None, {}, []) or ads in (None, {}):
            raise YandexError("args", f"{who}: новой группе «{name}» нужны keywords и ads (titles и texts)")
        spec = _keywords(keywords, who)
        spec["mode"] = "add"
        ad = _ads({**ads, "mode": "add"} if isinstance(ads, dict) else ads, who)
        return {"new": True, "name": name, "keywords": spec, "ads": ad}
    group_id = int(experiments._number(item.get("group_id"), "group_id"))
    rename = _text(item.get("rename"), 255)
    if item.get("remove"):
        if keywords not in (None, {}, []) or ads not in (None, {}) or rename:
            raise YandexError("args", f"{who}: группу {group_id} убираем (remove) — keywords, ads и rename ей не нужны")
        return {"group_id": group_id, "remove": True}
    out: dict = {"group_id": group_id}
    if keywords not in (None, {}, []):
        out["keywords"] = _keywords(keywords, who)
    if ads not in (None, {}):
        out["ads"] = _ads(ads, who)
    if rename:
        out["rename"] = rename
    if len(out) == 1:
        raise YandexError(
            "args", f"{who}: группа {group_id} ничего не меняет — задай keywords, ads, rename или remove: true"
        )
    return out


def _groups(raw, who: str) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise YandexError("args", f"{who}: groups — список изменений по группам")
    if len(raw) > MAX_GROUP_ITEMS:
        raise YandexError("args", f"{who}: groups — не больше {MAX_GROUP_ITEMS}")
    out: list[dict] = []
    ids: set[int] = set()
    names: set[str] = set()
    for i, item in enumerate(raw, 1):
        g = _group_item(item, f"{who}, группа {i}")
        if g.get("new"):
            key = g["name"].casefold()
            if key in names:
                raise YandexError("args", f"{who}: две новые группы «{g['name']}» — у групп разные названия")
            names.add(key)
        else:
            if g["group_id"] in ids:
                raise YandexError("args", f"{who}: группа {g['group_id']} указана дважды — объедини изменения")
            ids.add(g["group_id"])
        out.append(g)
    return out


def short_label(text: str, limit: int = LABEL_LEN) -> str:
    """Короткое имя гипотезы для названий: по границе слова, без хвостовой пунктуации."""
    text = _text(text, 1000)
    if len(text) > limit:
        cut = text[: limit + 1]
        words = cut.split(" ")[:-1] if " " in cut else [text[:limit]]
        # «…за 5 минут и без» — хвостовые предлоги и союзы отбрасываем.
        while len(words) > 1 and words[-1].casefold() in _TAIL_WORDS:
            words.pop()
        text = " ".join(words)
    return text.rstrip(" ,.;:—-")


_TAIL_WORDS = {"и", "а", "в", "во", "на", "с", "со", "за", "без", "для", "по", "от", "к", "о", "об", "из", "до", "или", "не"}


def default_name(label: str, n: int, source_name: str, today=None) -> str:
    """Название копии по гипотезе и дате: «Вариант скорости · 28.10»."""
    day = (today or today_msk()).strftime("%d.%m")
    base = label or f"{source_name} — вариант {n}"
    return f"{base[: 255 - 8]} · {day}"


def group_name(source_name: str, label: str) -> str:
    """Название изменённой группы копии: «{группа оригинала} — {гипотеза}»."""
    suffix = f" — {label}"
    if source_name.endswith(suffix):
        return source_name[:255]
    return f"{source_name[: 255 - len(suffix)]}{suffix}"


def _variant(item: dict, n: int, source_name: str) -> dict:
    who = f"Вариант {n}"
    idea = _text(item.get("idea"), 300)
    label = short_label(item.get("label") or "") or short_label(idea)
    name = _text(item.get("name"), 255) or default_name(label, n, source_name)
    v: dict = {"n": n, "name": name, "idea": idea}
    if label:
        v["label"] = label
    if item.get("plan_step") not in (None, ""):
        v["plan_step"] = int(experiments._number(item.get("plan_step"), "plan_step"))
    if item.get("region_ids") not in (None, "", []):
        raw = item["region_ids"]
        regions = geo.parse_region_ids(raw.replace(",", " ").split() if isinstance(raw, str) else raw)
        if not any(r > 0 for r in regions):
            raise YandexError("args", f"{who}: region_ids — хотя бы один регион показа (id из find_regions, без минуса)")
        v["region_ids"] = regions
    if item.get("schedule") not in (None, ""):
        schedule = str(item["schedule"]).strip().lower()
        if schedule not in actions.SCHEDULE_PRESETS:
            raise YandexError("args", f"{who}: schedule — одно из {list(actions.SCHEDULE_PRESETS)}")
        v["schedule"] = schedule
    if item.get("priority_goals") not in (None, []):
        goals = item["priority_goals"]
        if not isinstance(goals, list):
            raise YandexError("args", f"{who}: priority_goals — список {{goal_id, value_rub}}")
        try:
            parsed = actions._priority_goals(goals)
        except YandexError as exc:
            raise YandexError("args", f"{who}: {exc.message or exc.code}") from exc
        if not parsed:
            raise YandexError("args", f"{who}: в priority_goals нет целей с goal_id")
        v["priority_goals"] = [{"goal_id": g["GoalId"], "value_rub": g["Value"] / 1_000_000} for g in parsed]
    weekly = experiments._number(item.get("weekly_budget_rub"), "weekly_budget_rub")
    if weekly is not None:
        if weekly < experiments.MIN_WEEKLY_RUB:
            raise YandexError("args", f"{who}: недельный бюджет в Директе — не меньше {experiments.MIN_WEEKLY_RUB} ₽")
        v["weekly_budget_rub"] = round(weekly, 2)
    if item.get("ads") not in (None, {}):
        v["ads"] = _ads(item["ads"], who)
    if item.get("keywords") not in (None, {}, []):
        v["keywords"] = _keywords(item["keywords"], who)
    if item.get("groups") not in (None, []):
        if "ads" in v or "keywords" in v:
            raise YandexError(
                "args",
                f"{who}: фразы и объявления задай либо на всю кампанию (ads, keywords), либо по группам (groups)",
            )
        v["groups"] = _groups(item["groups"], who)
    if not any(k in v for k in CHANGE_KEYS):
        raise YandexError(
            "args",
            f"{who} ничего не меняет — задай хотя бы одно: {', '.join(CHANGE_KEYS)}; просто копия — duplicate_campaign",
        )
    return v


def normalize_variants(raw, source_name: str) -> list[dict]:
    """Варианты из аргументов ИИ. Ошибки — текстом, по которому модель поправит вызов."""
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise YandexError("args", f"variants — список вариантов, от 1 до {MAX_VARIANTS}")
    if len(raw) > MAX_VARIANTS:
        raise YandexError("args", f"Не больше {MAX_VARIANTS} вариантов за раз — оставь самые сильные гипотезы")
    out: list[dict] = []
    names: set[str] = set()
    for n, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise YandexError("args", f"Вариант {n} — объект")
        v = _variant(item, n, source_name)
        key = v["name"].casefold()
        if key in names:
            raise YandexError("args", f"Вариант {n}: название «{v['name']}» уже занято — у вариантов разные названия")
        names.add(key)
        out.append(v)
    return out


def _group_list(groups: list[dict]) -> str:
    return ", ".join(f"{g['id']} «{g['name']}»" for g in sorted(groups, key=lambda g: int(g["id"])))


def _has_text_ads(ads: list[dict], group_row_id: int) -> bool:
    return any(
        int(a["adgroup_row_id"]) == group_row_id and a["type"] in TEXT_AD_TYPES and a["state"] != "ARCHIVED" for a in ads
    )


def check_source(user_id: int, source: dict, variants: list[dict]) -> None:
    """Проверки по кампании-источнику до копий: группы, регионы, стратегия под бюджет. Группам варианта
    дописывает adgroup_id и source_name оригинала — по ним create_variant найдёт группы в копии."""
    from app.adgroups import list_adgroups
    from app.ads import list_ads

    groups = list_adgroups(user_id, int(source["id"]))
    by_id = {int(g["id"]): g for g in groups}
    ads: list[dict] = []
    if any(g.get("new") or g.get("ads", {}).get("mode") == "replace" for v in variants for g in v.get("groups") or []):
        ads = list_ads(user_id, campaign_row_id=int(source["id"]))
    for v in variants:
        who = f"Вариант {v['n']}"
        if not groups and any(k in v for k in ("ads", "keywords", "region_ids", "groups")):
            raise YandexError("args", f"{who}: в «{source['name']}» нет групп — объявления, фразы и регионы живут в группах")
        if v.get("keywords", {}).get("mode") == "replace" and len(groups) > 1:
            raise YandexError(
                "args",
                f"{who}: в «{source['name']}» групп: {len(groups)} — заменить фразы сразу во всей кампании нельзя. "
                f"Задай фразы каждой группе в groups (group_id: {_group_list(groups)}) или keywords.mode=add",
            )
        items = v.get("groups") or []
        for g in items:
            if g.get("new"):
                if not g["ads"]["href"] and not any(a.get("href") for a in ads):
                    raise YandexError("args", f"{who}: у объявлений оригинала нет ссылки — передай ads.href новой группе")
                continue
            src = by_id.get(g["group_id"])
            if not src:
                raise YandexError("args", f"{who}: в «{source['name']}» нет группы {g['group_id']} — есть {_group_list(groups)}")
            g["adgroup_id"] = int(src["adgroup_id"])
            g["source_name"] = src["name"]
            if g.get("ads", {}).get("mode") == "replace" and not _has_text_ads(ads, g["group_id"]):
                raise YandexError(
                    "args", f"{who}: в группе «{src['name']}» нет текстовых и комбинаторных объявлений — нужен ads.mode=add"
                )
        if items:
            removed = {g["group_id"] for g in items if g.get("remove")}
            if len(removed) == len(groups) and not any(g.get("new") for g in items):
                raise YandexError("args", f"{who}: убраны все группы — оставь хотя бы одну или добавь новую")
            kept = {g["name"].casefold() for g in groups if int(g["id"]) not in removed}
            for g in items:
                if g.get("new") and g["name"].casefold() in kept:
                    raise YandexError("args", f"{who}: группа «{g['name']}» уже есть в копии — дай новой другое название")
    regions = sorted({r for v in variants for r in v.get("region_ids") or []})
    unknown = geo.unknown_regions(user_id, regions) if regions else []
    if unknown:
        raise YandexError("args", f"Неизвестные регионы {unknown} — найди id через find_regions")
    budgets = [v for v in variants if "weekly_budget_rub" in v]
    if budgets:
        settings = actions.get_campaign_settings(user_id, int(source["id"]))
        for v in budgets:
            experiments.limit_plan({**settings, "name": source["name"]}, v["weekly_budget_rub"], force=True)


def first_group(groups: list[dict]) -> dict:
    """Группа, куда встают новые фразы варианта: первая по порядку создания."""
    return min(groups, key=lambda g: int(g["id"]))


# --- Сборка варианта ---------------------------------------------------------------------------


def merge_goals(current: list[dict], changes: list[dict]) -> list[dict]:
    """Цели варианта поверх целей оригинала: ценность заданных меняется, новые добавляются, прочие остаются.
    Целиком список не заменяем: оплата за конверсии по нескольким целям требует от двух целей."""
    out = [dict(g) for g in current]
    for goal in changes:
        same = next((g for g in out if int(g["goal_id"]) == int(goal["goal_id"])), None)
        if same:
            same["value_rub"] = goal["value_rub"]
        else:
            out.append(dict(goal))
    return out


def _settings(user_id: int, cid: int, v: dict) -> dict:
    changes: dict = {}
    if "schedule" in v:
        changes["schedule"] = v["schedule"]
    settings = actions.get_campaign_settings(user_id, cid) if "priority_goals" in v or "weekly_budget_rub" in v else {}
    if "priority_goals" in v:
        changes["priority_goals"] = merge_goals(settings.get("priority_goals") or [], v["priority_goals"])
    if "weekly_budget_rub" in v:
        changes.update(experiments.limit_plan(settings, v["weekly_budget_rub"], force=True)["changes"])
    if not changes:
        return {"changed": []}
    return actions.update_campaign_settings(user_id, cid, changes)


def _regions(user_id: int, cid: int, region_ids: list[int], workspace_id: int | None) -> dict:
    result = geo.set_campaign_regions(user_id, cid, region_ids, workspace_id=workspace_id)
    if result["errors"] or not result["updated"]:
        raise YandexError("regions", "; ".join(result["errors"][:3]) or "регионы не заменены")
    return result


def _live_ads(user_id: int, cid: int) -> list[dict]:
    from app.ads import list_ads

    return [a for a in list_ads(user_id, campaign_row_id=cid) if a["state"] != "ARCHIVED"]


def _first_href(ads: list[dict], group_row_id: int | None = None) -> str:
    for ad in sorted(ads, key=lambda a: int(a["id"])):
        if ad.get("href") and (group_row_id is None or int(ad["adgroup_row_id"]) == group_row_id):
            return ad["href"]
    return ""


def _ads_in(user_id: int, groups: list[dict], ads: list[dict], spec: dict, fallback_href: str = "") -> dict:
    """replace — новые тексты в текстовых и комбинаторных объявлениях этих групп; add — новое объявление
    в каждой группе (ссылка — из spec, первого объявления группы, кампании или fallback_href)."""
    from app.ads import create_text_ad, update_text_ad

    ids = {int(g["id"]) for g in groups}
    errors: list[str] = []
    done = 0
    if spec["mode"] == "replace":
        text_ads = [a for a in ads if int(a["adgroup_row_id"]) in ids and a["type"] in TEXT_AD_TYPES]
        if not text_ads:
            raise YandexError("ads", "нет текстовых и комбинаторных объявлений — нужен ads.mode=add")
        for ad in text_ads:
            try:
                update_text_ad(
                    user_id,
                    int(ad["id"]),
                    titles=spec["titles"] or None,
                    texts=spec["texts"] or None,
                    title2=spec["title2"] or None,
                    href=spec["href"] or None,
                )
                done += 1
            except YandexError as exc:
                errors.append(f"«{ad['title'] or ad['ad_id']}»: {exc.message or exc.code}")
    else:
        per_title = bool(spec.get("per_title"))
        removed = 0
        for group in groups:
            href = spec["href"] or _first_href(ads, int(group["id"])) or _first_href(ads) or fallback_href
            if not href:
                errors.append(f"группа «{group['name']}»: нет ссылки — передай ads.href")
                continue
            mine = [a for a in ads if int(a["adgroup_row_id"]) == int(group["id"]) and a["type"] in TEXT_AD_TYPES]
            # Расширения — как у объявлений группы, в новой группе — как у объявлений копии.
            extras = _group_extras(user_id, group, mine or [a for a in ads if a["type"] in TEXT_AD_TYPES])
            failed = False
            for titles in [[t] for t in spec["titles"]] if per_title else [spec["titles"]]:
                try:
                    create_text_ad(
                        user_id,
                        adgroup_row_id=int(group["id"]),
                        href=href,
                        title2=spec["title2"],
                        titles=titles,
                        texts=spec["texts"],
                        extras=extras,
                    )
                    done += 1
                except YandexError as exc:
                    failed = True
                    errors.append(f"группа «{group['name']}», «{titles[0]}»: {exc.message or exc.code}")
            # Старые объявления убираем, только когда встали все заголовки: иначе тест неполный.
            if per_title and not spec.get("keep_old") and mine and not failed:
                removed += _drop_ads(user_id, mine)
        if errors:
            raise YandexError("ads", f"готово {done}, ошибки: " + "; ".join(errors[:3]))
        return {"ads": done, "removed_ads": removed} if per_title else {"ads": done}
    if errors:
        raise YandexError("ads", f"готово {done}, ошибки: " + "; ".join(errors[:3]))
    return {"ads": done}


def _group_extras(user_id: int, group: dict, ads: list[dict]) -> dict | None:
    """Быстрые ссылки, уточнения и картинки первого объявления группы — для новых объявлений теста."""
    from app import ad_extras
    from app.ads import _adgroup_row, get_connection_access_token

    if not ads:
        return None
    first = min(ads, key=lambda a: int(a["id"]))
    row = _adgroup_row(user_id, int(group["id"]))
    try:
        token = get_connection_access_token(row["connection_id"], user_id)
        refs = ad_extras._ad_refs(token, row["client_login"], int(first["ad_id"]))
    except YandexError as exc:
        log.warning("Расширения объявления %s не прочитаны: %s", first["ad_id"], exc.code)
        return None
    extras = {k: refs[k] for k in ("sitelink_set_id", "callout_ids", "image_hashes") if refs.get(k)}
    return extras or None


def _drop_ads(user_id: int, ads: list[dict]) -> int:
    """Скопированные объявления-черновики копии, которые заменил тест заголовков."""
    from app.ads import bulk_ad_action

    result = bulk_ad_action(user_id, ad_row_ids=[int(a["id"]) for a in ads], action="delete")
    return int(result.get("applied") or 0)


def _apply_ads(user_id: int, cid: int, spec: dict) -> dict:
    """Объявления на всю кампанию: те же тексты во всех группах копии."""
    from app.adgroups import list_adgroups

    return _ads_in(user_id, list_adgroups(user_id, cid), _live_ads(user_id, cid), spec)


def _keywords_in(user_id: int, group: dict, spec: dict, existing: list[dict]) -> dict:
    """Фразы в группу. existing — фразы, с которыми сверяем дубли; replace удаляет из них те, которых нет
    в новом списке (поэтому для replace existing — фразы только этой группы). Автотаргетинг не трогаем."""
    from app.keywords import bulk_keyword_action, create_keywords

    existing = [k for k in existing if k["keyword"] != AUTOTARGETING]
    wanted = {p.casefold() for p in spec["phrases"]}
    have = {k["keyword"].casefold() for k in existing}
    removed = 0
    if spec["mode"] == "replace":
        old = [int(k["id"]) for k in existing if k["keyword"].casefold() not in wanted]
        for chunk in direct_api.chunks(old, KEYWORD_BATCH):
            removed += int(bulk_keyword_action(user_id, keyword_row_ids=chunk, action="delete")["applied"])
    new = [p for p in spec["phrases"] if p.casefold() not in have]
    added = 0
    errors = 0
    for chunk in direct_api.chunks(new, KEYWORD_BATCH):
        result = create_keywords(user_id, adgroup_row_id=int(group["id"]), phrases=chunk, bid_rub=spec["bid_rub"])
        added += int(result["created"])
        errors += int(result["errors"])
    if errors:
        raise YandexError("keywords", f"добавлено {added}, не прошли {errors}")
    return {"added": added, "removed": removed, "group": group["name"]}


def _apply_keywords(user_id: int, cid: int, spec: dict) -> dict:
    """Фразы на всю кампанию: add — в первую группу копии без дублей по кампании; replace — только в
    кампании с одной группой."""
    from app.adgroups import list_adgroups
    from app.keywords import list_keywords

    groups = list_adgroups(user_id, cid)
    if not groups:
        raise YandexError("keywords", "в копии нет групп")
    if spec["mode"] == "replace" and len(groups) > 1:
        raise YandexError("keywords", "заменить фразы во всей кампании можно только при одной группе — задай фразы по группам")
    return _keywords_in(user_id, first_group(groups), spec, list_keywords(user_id, campaign_row_id=cid))


def _copied_group(copy: dict, groups: list[dict], g: dict) -> dict:
    """Группа копии, в которую скопирована группа оригинала g."""
    target = copy.get("group_map", {}).get(int(g["adgroup_id"]))
    row = next((x for x in groups if int(x["adgroup_id"]) == int(target)), None) if target else None
    if not row:
        raise YandexError("groups", "группа не скопировалась")
    return row


def _rename_group(user_id: int, row: dict, name: str) -> str:
    from app.adgroups import update_adgroup

    if not name or name == row["name"]:
        return row["name"]
    update_adgroup(user_id, int(row["id"]), name=name)
    return name


def _change_group(user_id: int, cid: int, row: dict, g: dict, label: str = "") -> dict:
    from app.keywords import list_keywords

    out: dict = {"group": row["name"]}
    if "ads" in g:
        ads = [a for a in _live_ads(user_id, cid) if int(a["adgroup_row_id"]) == int(row["id"])]
        out.update(_ads_in(user_id, [row], ads, g["ads"]))
    if "keywords" in g:
        mine = [k for k in list_keywords(user_id, campaign_row_id=cid) if int(k["adgroup_row_id"]) == int(row["id"])]
        out["keywords"] = _keywords_in(user_id, row, g["keywords"], mine)
    # Изменённая группа копии — по гипотезе, чтобы в кабинете было видно, что в ней тестируем.
    name = g.get("rename") or (group_name(g.get("source_name") or row["name"], label) if label else "")
    out["group"] = _rename_group(user_id, row, name)
    return out


def _new_group(user_id: int, cid: int, g: dict, region_ids: list[int], href: str) -> dict:
    from app.adgroups import create_adgroup

    row = create_adgroup(user_id, campaign_row_id=cid, name=g["name"], region_ids=region_ids or None)
    keywords = _keywords_in(user_id, row, g["keywords"], [])
    ads = _ads_in(user_id, [row], _live_ads(user_id, cid), g["ads"], href)
    return {"group": row["name"], "keywords": keywords, **ads}


def _apply_groups(out: dict, user_id: int, source_row_id: int, cid: int, copy: dict, v: dict) -> None:
    """Изменения по группам: правки в скопированных группах и новые группы. Каждая группа — свой шаг."""
    from app.adgroups import list_adgroups

    items = [g for g in v["groups"] if not g.get("remove")]
    groups = list_adgroups(user_id, cid)
    for g in items:
        if g.get("new"):
            continue
        label = f"группа «{g['source_name']}»"
        _step(out, label, lambda g=g: _change_group(user_id, cid, _copied_group(copy, groups, g), g, v.get("label", "")))
    new = [g for g in items if g.get("new")]
    if not new:
        return
    base = groups or list_adgroups(user_id, source_row_id)
    regions = v.get("region_ids") or (first_group(base)["region_ids"] if base else [])
    href = _first_href(_live_ads(user_id, cid)) or _first_href(_live_ads(user_id, source_row_id))
    for g in new:
        _step(out, f"новая группа «{g['name']}»", lambda g=g: _new_group(user_id, cid, g, regions, href))


def _rename_touched(user_id: int, cid: int, v: dict) -> dict:
    """Объявления на всю кампанию меняют все группы копии, фразы — первую: их и называем по гипотезе."""
    from app.adgroups import list_adgroups

    groups = list_adgroups(user_id, cid)
    touched = groups if "ads" in v else [first_group(groups)] if groups else []
    return {"groups": [_rename_group(user_id, g, group_name(g["name"], v.get("label", ""))) for g in touched]}


def _step(out: dict, label: str, fn):
    """Шаг сборки варианта: ошибка не прерывает остальные шаги, а попадает в errors."""
    try:
        result = fn()
    except YandexError as exc:
        out["errors"].append(f"{label}: {exc.message or exc.code}")
        return None
    except Exception:
        log.exception("Вариант «%s», шаг «%s»", out["name"], label)
        out["errors"].append(f"{label}: внутренняя ошибка")
        return None
    out["done"].append(label)
    return result


def create_variant(user_id: int, source_row_id: int, v: dict, *, workspace_id: int | None, moderate: bool) -> dict:
    """Копия → изменения варианта → модерация. С ошибкой на любом шаге копия остаётся черновиком:
    на модерацию её не отправляем, чтобы не тестировать не то, что согласовали."""
    removed = {int(g["adgroup_id"]) for g in v.get("groups") or [] if g.get("remove")}
    copy = actions.duplicate_campaign(
        user_id, source_row_id, name=v["name"], workspace_id=workspace_id, exclude_adgroup_ids=removed or None
    )
    new = copy["campaign"]
    cid = int(new["id"])
    out: dict = {
        "n": v["n"],
        "name": new["name"],
        "campaign_row_id": cid,
        "groups": copy["groups"],
        "keywords": copy["keywords"],
        "ads": copy["ads"],
        "warnings": copy["warnings"],
        "done": [],
        "errors": [],
        "moderated": False,
        "ready": False,
    }
    if copy["status"] != "ok":
        out["errors"].append("копия собрана не полностью")
    if any(k in v for k in ("schedule", "priority_goals", "weekly_budget_rub")):
        _step(out, "настройки", lambda: _settings(user_id, cid, v))
    if "region_ids" in v:
        _step(out, "регионы", lambda: _regions(user_id, cid, v["region_ids"], workspace_id))
    if "ads" in v:
        _step(out, "объявления", lambda: _apply_ads(user_id, cid, v["ads"]))
    if "keywords" in v:
        out["keywords_result"] = _step(out, "ключевые фразы", lambda: _apply_keywords(user_id, cid, v["keywords"]))
    if v.get("label") and ("ads" in v or "keywords" in v):
        _step(out, "названия групп", lambda: _rename_touched(user_id, cid, v))
    if "groups" in v:
        _apply_groups(out, user_id, source_row_id, cid, copy, v)
    if out["errors"]:
        out["note"] = "Вариант собран не полностью — остался черновиком, на модерацию не отправлен"
        return out
    out["ready"] = True
    if not moderate:
        out["note"] = "Черновик: отправьте на модерацию, когда проверите"
        return out
    result = _step(out, "модерация", lambda: actions.moderate_campaign(user_id, cid))
    out["moderated"] = bool(result and result.get("sent"))
    return out


def create_variants(
    user_id: int, source_row_id: int, variants: list[dict], *, workspace_id: int | None, moderate: bool = False, tick=None
) -> list[dict]:
    """Варианты по очереди; сбой одного не останавливает остальные. tick — после каждого варианта."""
    out: list[dict] = []
    for v in variants:
        try:
            out.append(create_variant(user_id, source_row_id, v, workspace_id=workspace_id, moderate=moderate))
        except YandexError as exc:
            out.append({"n": v["n"], "name": v["name"], "error": exc.message or exc.code, "ready": False})
        except Exception:
            log.exception("Вариант «%s» кампании %s", v["name"], source_row_id)
            out.append({"n": v["n"], "name": v["name"], "error": "внутренняя ошибка", "ready": False})
        if tick:
            tick()
    return out
