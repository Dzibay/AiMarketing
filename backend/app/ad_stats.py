"""Статистика объявлений пространства и переписывание слабых объявлений.

Отчёт Директа (CUSTOM_REPORT) по объявлениям выбранных кампаний — отдельно поиск и сети. Слабое
объявление — CTR заметно ниже соседей по группе в той же сети (если соседей нет — ниже кампании)
или ниже порога для поиска. ИИ предлагает новые тексты; они добавляются в ту же группу как новое
объявление (A/B), старое не меняется — статистика сравнит, какой вариант лучше.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable

from app import ads as ads_mod
from app import ai, direct_api
from app.brief import brief_prompt
from app.db import pool
from app.direct_reports import fetch_report
from app.journal import journal_quiet, log_action
from app.slices import _campaigns, _period
from app.stats import _count, _rub_to_kopecks, goal_columns
from app.workspaces import require_workspace
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.ad_stats")

Progress = Callable[[int, int, str], None]

NETS = {"SEARCH": "search", "AD_NETWORK": "network"}
NET_LABELS = {"search": "Поиск", "network": "Сети"}
# Минимум показов, чтобы судить о CTR объявления.
MIN_IMPRESSIONS = {"search": 200, "network": 1000}
# Слабое — CTR ниже 70 % от соседей.
LOW_SHARE = 0.7
# Абсолютный порог для поиска: CTR ниже 1 % на 500+ показах — слабое и без соседей.
SEARCH_FLOOR = (1.0, 500)
REWRITABLE = ("TEXT_AD", "RESPONSIVE_AD")
AI_MAX = 20
AI_BATCH = 5
AB_MAX = 50
KEYWORDS_IN_PROMPT = 15
# В Директе слово в заголовке и тексте — не длиннее 22 символов.
WORD_LEN = 22


# --- Отчёт ---------------------------------------------------------------------------------------


def _blank_net() -> dict:
    return {"impressions": 0, "clicks": 0, "cost_kopecks": 0, "conversions": 0}


def _finish_net(net: dict) -> dict:
    imp, clicks, cost, conv = net["impressions"], net["clicks"], net["cost_kopecks"], net["conversions"]
    net["ctr"] = round(clicks / imp * 100, 2) if imp else None
    net["cpc_kopecks"] = round(cost / clicks) if clicks else None
    net["cpa_kopecks"] = round(cost / conv) if conv else None
    return net


def _ctr(clicks: int, impressions: int) -> float | None:
    return round(clicks / impressions * 100, 2) if impressions else None


def flag_low(rows: list[dict]) -> None:
    """Помечает слабые объявления: net["low"], net["ref_ctr"], net["reasons"], net["best_ad_id"].

    rows — объявления с ключами ad_id, adgroup_id, campaign_id и nets: {search|network: метрики}.
    Сравнение — с остальными объявлениями группы в той же сети (у кого хватает показов);
    если таких нет — с остальными объявлениями кампании.
    """
    for net_key, min_imp in MIN_IMPRESSIONS.items():
        enough = [r for r in rows if (r["nets"].get(net_key) or {}).get("impressions", 0) >= min_imp]
        by_group: dict = defaultdict(list)
        by_campaign: dict = defaultdict(list)
        for r in enough:
            by_group[r["adgroup_id"]].append(r)
            by_campaign[r["campaign_id"]].append(r)
        for r in enough:
            net = r["nets"][net_key]
            peers = [p for p in by_group[r["adgroup_id"]] if p is not r]
            scope = "группы"
            if not peers:
                peers = [p for p in by_campaign[r["campaign_id"]] if p is not r]
                scope = "кампании"
            reasons: list[str] = []
            ref = None
            if peers:
                ref = _ctr(sum(p["nets"][net_key]["clicks"] for p in peers), sum(p["nets"][net_key]["impressions"] for p in peers))
                if ref and net["ctr"] is not None and net["ctr"] < ref * LOW_SHARE:
                    reasons.append(f"CTR {net['ctr']:g} % против {ref:g} % у соседей {scope}")
                best = max(peers, key=lambda p: (p["nets"][net_key]["ctr"] or 0, p["nets"][net_key]["impressions"]))
                if (best["nets"][net_key]["ctr"] or 0) > (net["ctr"] or 0):
                    net["best_ad_id"] = best["ad_id"]
            floor, floor_imp = SEARCH_FLOOR
            if net_key == "search" and not reasons and net["impressions"] >= floor_imp and (net["ctr"] or 0) < floor:
                reasons.append(f"CTR на поиске {net['ctr'] or 0:g} % — ниже {floor:g} %")
            net["ref_ctr"] = ref
            net["low"] = bool(reasons)
            net["reasons"] = reasons


def _local_ads(user_id: int, account_ids: list[int], ad_ids: list[int]) -> dict[tuple[int, int], dict]:
    if not ad_ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT ad.*, g.name AS adgroup_name, g.adgroup_id, g.campaign_row_id
            FROM direct_ads ad
            JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
            WHERE ad.user_id = %s AND ad.account_id = ANY(%s) AND ad.ad_id = ANY(%s)
            """,
            (user_id, account_ids, ad_ids),
        ).fetchall()
    return {(int(r["account_id"]), int(r["ad_id"])): r for r in rows}


def _sync_campaigns(user_id: int, campaign_row_ids: list[int], progress: Progress) -> None:
    """Подтягивает группы и объявления кампаний, где в отчёте есть объявления, которых нет в базе."""
    from app.adgroups import sync_campaign_adgroups

    for n, row_id in enumerate(campaign_row_ids):
        progress(n, len(campaign_row_ids), "Обновляю объявления кампаний")
        try:
            sync_campaign_adgroups(user_id, row_id)
            ads_mod.sync_campaign_ads(user_id, row_id)
        except Exception:
            log.warning("ad report: sync campaign %s failed", row_id, exc_info=True)


def ad_report(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """{"ads": [...], "period", "errors", "missing"}: объявления с метриками по сетям и пометкой слабых."""
    progress = progress or (lambda *_a: None)
    workspace_id = int(payload["workspace_id"])
    ws = require_workspace(user_id, workspace_id)
    campaigns = _campaigns(user_id, workspace_id, payload.get("campaign_ids"))
    date_from, date_to = _period(payload.get("days"))
    goal = int(ws["goal_id"]) if ws.get("goal_id") else None
    by_account: dict[int, list[dict]] = defaultdict(list)
    for c in campaigns:
        by_account[c["account_id"]].append(c)

    stats: dict[tuple[int, int], dict] = {}  # (account_id, ad_id) → строка
    errors: list[dict] = []
    for n, (account_id, camps) in enumerate(by_account.items()):
        login = camps[0]["client_login"]
        progress(n, len(by_account) + 1, f"Отчёт Директа: {login}")
        by_cid = {int(c["campaign_id"]): c for c in camps}
        try:
            token = get_connection_access_token(camps[0]["connection_id"], user_id)
            raw = fetch_report(
                token,
                login,
                report_type="CUSTOM_REPORT",
                field_names=["CampaignId", "AdGroupId", "AdId", "AdNetworkType", "Impressions", "Clicks", "Cost", "Conversions"],
                date_from=date_from,
                date_to=date_to,
                filters=[{"Field": "CampaignId", "Operator": "IN", "Values": [str(i) for i in by_cid]}],
                goals=[goal] if goal else None,
            )
        except YandexError as exc:
            if exc.code in ("token-expired", "unauthorized") or len(by_account) == 1:
                raise
            errors.append({"client_login": login, "error": exc.code, "message": exc.message or ""})
            continue
        conv_col = (goal_columns(raw).get(goal) or {}).get("Conversions") if goal else "Conversions"
        for item in raw:
            camp = by_cid.get(_count(item.get("CampaignId")))
            net_key = NETS.get(str(item.get("AdNetworkType") or ""))
            ad_id = _count(item.get("AdId"))
            if camp is None or net_key is None or not ad_id:
                continue
            row = stats.setdefault(
                (account_id, ad_id),
                {
                    "ad_id": ad_id,
                    "adgroup_id": _count(item.get("AdGroupId")),
                    "campaign_id": camp["id"],
                    "campaign_name": camp["name"],
                    "nets": {},
                },
            )
            net = row["nets"].setdefault(net_key, _blank_net())
            net["impressions"] += _count(item.get("Impressions"))
            net["clicks"] += _count(item.get("Clicks"))
            net["cost_kopecks"] += _rub_to_kopecks(item.get("Cost"))
            net["conversions"] += _count(item.get(conv_col)) if conv_col else 0

    account_ids = list(by_account)
    keys = list(stats)
    local = _local_ads(user_id, account_ids, [k[1] for k in keys])
    unknown = {stats[k]["campaign_id"] for k in keys if k not in local}
    if unknown:
        _sync_campaigns(user_id, sorted(unknown), progress)
        local = _local_ads(user_id, account_ids, [k[1] for k in keys])

    rows: list[dict] = []
    missing = 0
    for key, row in stats.items():
        ad = local.get(key)
        if ad is None:
            missing += 1
            continue
        for net in row["nets"].values():
            _finish_net(net)
        public = ads_mod._public(ad)
        row.update(
            {
                "ad_row_id": ad["id"],
                "adgroup_row_id": ad["adgroup_row_id"],
                "adgroup_name": ad.get("adgroup_name") or "",
                "type": public["type"],
                "state": public["state"],
                "status": public["status"],
                "title": public["title"],
                "title2": public["title2"],
                "text": public["text"],
                "titles": public["titles"],
                "texts": public["texts"],
                "href": public["href"],
                "rewritable": public["type"] in REWRITABLE and public["state"] != "ARCHIVED",
            }
        )
        total = _blank_net()
        for net in row["nets"].values():
            for k in total:
                total[k] += net[k]
        row["total"] = _finish_net(total)
        rows.append(row)

    flag_low(rows)
    by_ad_id = {r["ad_id"]: r for r in rows}
    for r in rows:
        r["low"] = any(n.get("low") for n in r["nets"].values())
        for net in r["nets"].values():
            best = by_ad_id.get(net.pop("best_ad_id", None))
            net["best_ad_row_id"] = best["ad_row_id"] if best else None
    rows.sort(key=lambda r: (-r["total"]["cost_kopecks"], -r["total"]["clicks"], -r["total"]["impressions"]))
    progress(1, 1, "Готово")
    from app.headlines import report_rows, summarize

    return {
        "ads": rows,
        # Заголовки: точные — из объявлений с одним заголовком и загруженных отчётов Мастера.
        "headlines": summarize(rows, report_rows(user_id, workspace_id, campaigns)),
        "missing": missing,
        "errors": errors,
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
    }


# --- ИИ: новые тексты ----------------------------------------------------------------------------

_AI_SYSTEM = """Ты — копирайтер и специалист по Яндекс Директу. У объявлений клиента низкий CTR.
Напиши для каждого новый вариант, который получит больше кликов от целевой аудитории.

{brief}

Правила:
- заголовок до {title_len} символов, второй заголовок до {title2_len}, текст до {text_len}, слово — до {word_len} символов;
- в заголовке — главный запрос группы или его суть, в тексте — выгода, цифры, условия и призыв;
- не обещай того, чего нет в брифе и старом объявлении (цены, скидки, сроки бери только оттуда);
- без КАПСА, восклицательных знаков подряд и превосходных степеней («лучший», «№1») — их не пропустит модерация;
- для комбинаторного объявления (RESPONSIVE_AD) — 3–5 разных заголовков и 2–3 текста, для текстового
  (TEXT_AD) — один заголовок, второй заголовок и один текст.

Верни только JSON: {{"ads": [{{"id": номер объявления, "titles": ["..."], "title2": "...", "texts": ["..."], "why": "что изменил и почему, до 120 символов"}}]}}"""


def _ad_block(ad: dict, keywords: list[str], best: dict | None, stat: dict) -> str:
    lines = [f"Объявление {ad['id']} ({ad['type']}), группа «{ad.get('adgroup_name') or ''}»"]
    net = stat.get("net")
    if stat.get("ctr") is not None:
        ref = f", у соседей {stat['ref_ctr']} %" if stat.get("ref_ctr") is not None else ""
        where = f" ({NET_LABELS[net].lower()})" if net in NET_LABELS else ""
        lines.append(f"CTR{where}: {stat['ctr']} %{ref}")
    lines.append("Заголовки: " + " | ".join(ad["titles"]))
    if ad.get("title2"):
        lines.append(f"Второй заголовок: {ad['title2']}")
    lines.append("Тексты: " + " | ".join(ad["texts"]))
    if keywords:
        lines.append("Фразы группы: " + ", ".join(keywords))
    if best:
        lines.append(f"Лучшее объявление группы: {' | '.join(best['titles'])} — {' | '.join(best['texts'])}")
    return "\n".join(lines)


def _clean_list(values, limit: int, length: int) -> list[str]:
    out: list[str] = []
    for value in values if isinstance(values, list) else [values]:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or len(text) > length or any(len(w) > WORD_LEN for w in text.split()):
            continue  # не обрезаем молча — оборванная фраза хуже, чем её отсутствие
        if text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def clean_suggestion(ad: dict, got: dict) -> dict | None:
    """Ответ ИИ по объявлению → {titles, texts, title2, why} в пределах Директа или None."""
    responsive = ad["type"] == "RESPONSIVE_AD"
    titles = _clean_list(got.get("titles") or got.get("title"), ads_mod.MAX_TITLES if responsive else 1, ads_mod.TITLE_LEN)
    texts = _clean_list(got.get("texts") or got.get("text"), ads_mod.MAX_TEXTS if responsive else 1, ads_mod.TEXT_LEN)
    title2 = "" if responsive else (_clean_list(got.get("title2"), 1, ads_mod.TITLE2_LEN) or [""])[0]
    if not titles or not texts:
        return None
    if titles == ad["titles"][: len(titles)] and texts == ad["texts"][: len(texts)] and title2 == (ad.get("title2") or ""):
        return None  # ИИ вернул то же самое
    return {"titles": titles, "texts": texts, "title2": title2, "why": str(got.get("why") or "").strip()[:200]}


def _workspace_ads(user_id: int, workspace_id: int, ad_row_ids: list[int]) -> dict[int, dict]:
    """Объявления пространства из базы; чужие и вне пространства — ошибка."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT ad.*, g.name AS adgroup_name, g.adgroup_id, g.campaign_row_id
            FROM direct_ads ad
            JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
            JOIN workspace_campaigns wc ON wc.campaign_row_id = g.campaign_row_id
            WHERE ad.user_id = %s AND wc.workspace_id = %s AND ad.id = ANY(%s)
            """,
            (user_id, workspace_id, ad_row_ids),
        ).fetchall()
    found = {int(r["id"]): {**ads_mod._public(r), "campaign_row_id": r["campaign_row_id"]} for r in rows}
    if len(found) != len(set(ad_row_ids)):
        raise YandexError("not-found", "Часть объявлений не найдена в пространстве — обновите отчёт")
    return found


def _group_keywords(user_id: int, adgroup_row_ids: list[int]) -> dict[int, list[str]]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT adgroup_row_id, keyword
            FROM direct_keywords
            WHERE user_id = %s AND adgroup_row_id = ANY(%s) AND state <> 'ARCHIVED' AND keyword <> '---autotargeting'
            ORDER BY adgroup_row_id, period_clicks DESC, period_impressions DESC, id
            """,
            (user_id, adgroup_row_ids),
        ).fetchall()
    out: dict[int, list[str]] = defaultdict(list)
    for r in rows:
        if len(out[r["adgroup_row_id"]]) < KEYWORDS_IN_PROMPT:
            out[r["adgroup_row_id"]].append(r["keyword"])
    return out


def ai_rewrite_ads(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """{"suggestions": {ad_row_id: {titles, texts, title2, why}}, "usage", "error"}. Ничего не меняет.

    payload.items: [{ad_row_id, net, ctr, ref_ctr, best_ad_row_id}] — метрики из отчёта для промпта.
    """
    progress = progress or (lambda *_a: None)
    workspace_id = int(payload["workspace_id"])
    ws = require_workspace(user_id, workspace_id)
    items = [i for i in payload.get("items") or [] if isinstance(i, dict) and i.get("ad_row_id")][:AI_MAX]
    if not items:
        raise YandexError("empty", "Не выбраны объявления")
    ids = [int(i["ad_row_id"]) for i in items]
    best_ids = [int(i["best_ad_row_id"]) for i in items if i.get("best_ad_row_id")]
    ads = _workspace_ads(user_id, workspace_id, list(dict.fromkeys(ids + best_ids)))
    if any(ads[i]["type"] not in REWRITABLE for i in ids):
        raise YandexError("type", "Переписать можно только текстовые и комбинаторные объявления")
    keywords = _group_keywords(user_id, list({ads[i]["adgroup_row_id"] for i in ids}))
    cfg = ai.load_config(user_id, "ads", workspace_id)
    system = _AI_SYSTEM.format(
        brief=brief_prompt(ws),
        title_len=ads_mod.TITLE_LEN,
        title2_len=ads_mod.TITLE2_LEN,
        text_len=ads_mod.TEXT_LEN,
        word_len=WORD_LEN,
    )
    suggestions: dict[int, dict] = {}
    error = None
    with ai.meter() as spent:
        for start in range(0, len(items), AI_BATCH):
            batch = items[start : start + AI_BATCH]
            progress(start, len(items), f"ИИ пишет тексты: {start} из {len(items)}")
            blocks = []
            for i in batch:
                ad = ads[int(i["ad_row_id"])]
                best = ads.get(int(i["best_ad_row_id"])) if i.get("best_ad_row_id") else None
                blocks.append(_ad_block(ad, keywords.get(ad["adgroup_row_id"], []), best, i))
            try:
                data = ai.complete_json(
                    {**cfg, "units": len(batch)},
                    [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(blocks)}],
                )
            except YandexError as exc:
                if not suggestions:
                    raise
                error = exc.message or exc.code
                break
            wanted = {int(i["ad_row_id"]) for i in batch}
            for got in data.get("ads") or []:
                if not isinstance(got, dict):
                    continue
                try:
                    ad_row_id = int(got.get("id"))
                except (TypeError, ValueError):
                    continue
                if ad_row_id not in wanted:
                    continue
                clean = clean_suggestion(ads[ad_row_id], got)
                if clean:
                    suggestions[ad_row_id] = clean
    progress(len(items), len(items), "Готово")
    usage = None
    if spent["calls"]:
        usage = {
            "calls": spent["calls"],
            "tokens_in": spent["tokens_in"],
            "tokens_out": spent["tokens_out"],
            "cost_rub": round(spent["cost_rub"], 4) if spent["priced"] else None,
            "estimated": spent["estimated"],
        }
    return {"suggestions": suggestions, "usage": usage, "error": error}


# --- A/B: новые объявления рядом со старыми ------------------------------------------------------


def _moderate(user_id: int, created: list[dict]) -> tuple[int, list[str]]:
    by_login: dict[str, list[dict]] = defaultdict(list)
    for ad in created:
        by_login[ad["client_login"]].append(ad)
    sent = 0
    errors: list[str] = []
    for login, group in by_login.items():
        token = get_connection_access_token(group[0]["connection_id"], user_id)
        result = direct_api.call(token, login, "ads", "moderate", {"SelectionCriteria": {"Ids": [int(a["ad_id"]) for a in group]}})
        item_errs = direct_api.item_errors(result, "ModerateResults")
        sent += len(group) - len(item_errs)
        errors.extend(text for _i, text in item_errs)
    return sent, errors


def create_ab_ads(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """Новые объявления в те же группы с той же ссылкой; старые не трогаем.

    payload: {workspace_id, items: [{ad_row_id, titles, texts, title2}], moderate}.
    Одна запись журнала; откат — удалить созданные объявления.
    """
    progress = progress or (lambda *_a: None)
    workspace_id = int(payload["workspace_id"])
    require_workspace(user_id, workspace_id)
    items = [i for i in payload.get("items") or [] if isinstance(i, dict) and i.get("ad_row_id")]
    if not items:
        raise YandexError("empty", "Нет объявлений для добавления")
    if len(items) > AB_MAX:
        raise YandexError("limit", f"За раз — до {AB_MAX} объявлений")
    sources = _workspace_ads(user_id, workspace_id, [int(i["ad_row_id"]) for i in items])
    created: list[dict] = []
    errors: list[dict] = []
    for n, item in enumerate(items):
        src = sources[int(item["ad_row_id"])]
        progress(n, len(items), f"Добавляю объявление в «{src['adgroup_name']}»")
        clean = clean_suggestion(src, item)
        if clean is None:
            errors.append({"ad_row_id": src["id"], "message": "Пустые или слишком длинные тексты"})
            continue
        try:
            with journal_quiet():
                ad = ads_mod.create_text_ad(
                    user_id,
                    adgroup_row_id=src["adgroup_row_id"],
                    href=src["href"],
                    titles=clean["titles"],
                    texts=clean["texts"],
                    title2=clean["title2"],
                )
        except YandexError as exc:
            errors.append({"ad_row_id": src["id"], "message": exc.message or exc.code})
            continue
        row = ads_mod._ad_row(user_id, ad["id"])
        created.append({**ad, "source_ad_row_id": src["id"], "campaign_row_id": src["campaign_row_id"],
                        "connection_id": row["connection_id"], "client_login": row["client_login"]})

    moderated, mod_errors = 0, []
    if created and payload.get("moderate"):
        progress(len(items), len(items) + 1, "Отправляю на модерацию")
        try:
            moderated, mod_errors = _moderate(user_id, created)
        except YandexError as exc:
            mod_errors = [exc.message or exc.code]
    status = "ok" if created and not errors else ("partial" if created else "error")
    if created:
        log_action(
            user_id,
            "ads",
            {
                "source_ad_row_ids": [a["source_ad_row_id"] for a in created],
                "campaign_row_ids": sorted({a["campaign_row_id"] for a in created}),
                "moderate": bool(payload.get("moderate")),
            },
            {"created": len(created), "moderated": moderated, "errors": [e["message"] for e in errors][:10] + mod_errors[:10]},
            status,
            workspace_id=workspace_id,
            title=f"Новые варианты объявлений (A/B): {len(created)}",
            undo={
                "label": "Удалить новые объявления",
                "danger": True,
                "steps": [{"op": "ads_action", "args": {"ad_row_ids": [a["id"] for a in created], "action": "delete"}}],
            },
            targets=[f"ad:{a['id']}" for a in created],
        )
    progress(1, 1, "Готово")
    if not created:
        raise YandexError("direct", errors[0]["message"] if errors else None)
    public_keys = ("id", "ad_id", "adgroup_row_id", "type", "title", "titles", "texts", "title2", "source_ad_row_id")
    return {
        "created": [{k: a.get(k) for k in public_keys} for a in created],
        "errors": errors,
        "moderated": moderated,
        "moderate_errors": mod_errors[:10],
        "status": status,
    }
