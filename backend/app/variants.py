"""Варианты кампании: до 5 копий одной кампании с разными параметрами для теста.

Вариант — копия со структурой, целями и оплатой оригинала (duplicate_campaign); меняется только то,
что в нём задано: регионы, расписание, ценности ключевых целей, недельный бюджет, тексты объявлений и
ключевые фразы. Оригинал не трогаем. Тексты правим в скопированных объявлениях, а не создаём новые:
так у варианта остаются быстрые ссылки, уточнения и картинки оригинала.
"""

from __future__ import annotations

import logging

from app import actions, direct_api, experiments, geo
from app.ads import MAX_TEXTS, MAX_TITLES, TEXT_LEN, TITLE2_LEN, TITLE_LEN
from app.yandex import YandexError

log = logging.getLogger(__name__)

MAX_VARIANTS = 5
MAX_PHRASES = 200
# create_keywords и bulk_keyword_action берут до 100 фраз за раз.
KEYWORD_BATCH = 100
AD_MODES = ("replace", "add")
KEYWORD_MODES = ("add", "replace")
TEXT_AD_TYPES = ("TEXT_AD", "RESPONSIVE_AD")
AUTOTARGETING = "---autotargeting"
# Что вариант может поменять; без хотя бы одного из них вариант — просто копия.
CHANGE_KEYS = ("region_ids", "schedule", "priority_goals", "weekly_budget_rub", "ads", "keywords")


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


def _ads(raw, who: str) -> dict:
    if not isinstance(raw, dict):
        raise YandexError("args", f"{who}: ads — объект {{titles, texts, href, mode}}")
    mode = str(raw.get("mode") or "replace").strip().lower()
    if mode not in AD_MODES:
        raise YandexError("args", f"{who}: ads.mode — одно из {list(AD_MODES)}")
    titles = _strings(raw.get("titles") or raw.get("title"), who, "titles", MAX_TITLES, TITLE_LEN)
    texts = _strings(raw.get("texts") or raw.get("text"), who, "texts", MAX_TEXTS, TEXT_LEN)
    title2 = _text(raw.get("title2"), 1000)
    if len(title2) > TITLE2_LEN:
        raise YandexError("args", f"{who}: title2 длиннее {TITLE2_LEN} символов — сократи")
    if mode == "add" and not (titles and texts):
        raise YandexError("args", f"{who}: для нового объявления (mode=add) нужны titles и texts")
    if not titles and not texts:
        raise YandexError("args", f"{who}: в ads нет ни titles, ни texts")
    return {"mode": mode, "titles": titles, "texts": texts, "title2": title2, "href": _text(raw.get("href"), 1024)}


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


def _variant(item: dict, n: int, source_name: str) -> dict:
    who = f"Вариант {n}"
    name = _text(item.get("name"), 255) or f"{source_name} — вариант {n}"[:255]
    v: dict = {"n": n, "name": name, "idea": _text(item.get("idea"), 300)}
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


def check_source(user_id: int, source: dict, variants: list[dict]) -> None:
    """Проверки по кампании-источнику до копий: группы, регионы, стратегия под бюджет."""
    from app.adgroups import list_adgroups

    groups = list_adgroups(user_id, int(source["id"]))
    for v in variants:
        who = f"Вариант {v['n']}"
        if not groups and ("ads" in v or "keywords" in v or "region_ids" in v):
            raise YandexError("args", f"{who}: в «{source['name']}» нет групп — объявления, фразы и регионы живут в группах")
        if v.get("keywords", {}).get("mode") == "replace" and len(groups) > 1:
            raise YandexError(
                "args",
                f"{who}: в «{source['name']}» {len(groups)} групп — заменить фразы можно только в кампании с одной "
                "группой; используй keywords.mode=add (фразы встанут в первую группу)",
            )
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


def _apply_ads(user_id: int, cid: int, spec: dict) -> dict:
    """replace — новые тексты во всех текстовых и комбинаторных объявлениях копии; add — новое
    объявление в каждой группе (ссылка — из spec или первого объявления группы)."""
    from app.adgroups import list_adgroups
    from app.ads import create_text_ad, list_ads, update_text_ad

    ads = [a for a in list_ads(user_id, campaign_row_id=cid) if a["state"] != "ARCHIVED"]
    errors: list[str] = []
    done = 0
    if spec["mode"] == "replace":
        text_ads = [a for a in ads if a["type"] in TEXT_AD_TYPES]
        if not text_ads:
            raise YandexError("ads", "в копии нет текстовых и комбинаторных объявлений — нужен ads.mode=add")
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
        hrefs: dict[int, str] = {}
        for ad in sorted(ads, key=lambda a: int(a["id"])):
            if ad["href"]:
                hrefs.setdefault(int(ad["adgroup_row_id"]), ad["href"])
        for group in list_adgroups(user_id, cid):
            href = spec["href"] or hrefs.get(int(group["id"])) or next(iter(hrefs.values()), "")
            if not href:
                errors.append(f"группа «{group['name']}»: нет ссылки — передай ads.href")
                continue
            try:
                create_text_ad(
                    user_id,
                    adgroup_row_id=int(group["id"]),
                    href=href,
                    title2=spec["title2"],
                    titles=spec["titles"],
                    texts=spec["texts"],
                )
                done += 1
            except YandexError as exc:
                errors.append(f"группа «{group['name']}»: {exc.message or exc.code}")
    if errors:
        raise YandexError("ads", f"готово {done}, ошибки: " + "; ".join(errors[:3]))
    return {"ads": done}


def _apply_keywords(user_id: int, cid: int, spec: dict) -> dict:
    """add — фразы в первую группу копии; replace (одна группа) — удалить фразы оригинала, которых нет в
    новом списке, и добавить недостающие. Автотаргетинг не трогаем."""
    from app.adgroups import list_adgroups
    from app.keywords import bulk_keyword_action, create_keywords, list_keywords

    groups = list_adgroups(user_id, cid)
    if not groups:
        raise YandexError("keywords", "в копии нет групп")
    target = first_group(groups)
    existing = [k for k in list_keywords(user_id, campaign_row_id=cid) if k["keyword"] != AUTOTARGETING]
    wanted = {p.casefold() for p in spec["phrases"]}
    have = {k["keyword"].casefold() for k in existing}
    removed = 0
    if spec["mode"] == "replace":
        if len(groups) > 1:
            raise YandexError("keywords", "заменить фразы можно только в кампании с одной группой")
        old = [int(k["id"]) for k in existing if k["keyword"].casefold() not in wanted]
        for chunk in direct_api.chunks(old, KEYWORD_BATCH):
            removed += int(bulk_keyword_action(user_id, keyword_row_ids=chunk, action="delete")["applied"])
    new = [p for p in spec["phrases"] if p.casefold() not in have]
    added = 0
    errors = 0
    for chunk in direct_api.chunks(new, KEYWORD_BATCH):
        result = create_keywords(user_id, adgroup_row_id=int(target["id"]), phrases=chunk, bid_rub=spec["bid_rub"])
        added += int(result["created"])
        errors += int(result["errors"])
    if errors:
        raise YandexError("keywords", f"добавлено {added}, не прошли {errors}")
    return {"added": added, "removed": removed, "group": target["name"]}


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
    copy = actions.duplicate_campaign(user_id, source_row_id, name=v["name"], workspace_id=workspace_id)
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
    user_id: int, source_row_id: int, variants: list[dict], *, workspace_id: int | None, moderate: bool = True, tick=None
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
