"""Черновик кампании пишется в Директ по шагам сборки (docs/board-plan.md, пункт 2).

После «Брифа» сервис сам создаёт черновик кампании, а каждый следующий согласованный шаг — группы и
фразы, минус-фразы, аудитории, объявления, картинки, расширения, настройки — дописывает в него.
Модель для этого не нужна: сервис сверяет согласованное на доске с тем, что уже записал, и в Директ
уходит только разница. Каждая запись — служебная строка ai_actions (tool = build_write) с итоговым
состоянием: какие группы, фразы и объявления в Директе — наши (их сервис может поправить или убрать).

Черновик сам не запускается и на модерацию не уходит: это делает маркетолог кнопкой на доске.
"""

from __future__ import annotations

import logging

from psycopg.types.json import Jsonb

from app.db import pool
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger(__name__)

TOOL = "build_write"
# Сколько предупреждений одной записи хранить и показывать.
MAX_WARNINGS = 30
# Поля брифа, которые уходят в кампанию; account_id и type после создания черновика не меняются.
CAMPAIGN_FIELDS = ("account_id", "name", "type", "region_ids", "strategy", "daily_budget_rub", "href")


def _norm(text) -> str:
    """Фраза или текст для сравнения записанного с доской: регистр и пробелы не важны, операторы — важны."""
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def _approved(items) -> list[dict]:
    return [i for i in items or [] if not i.get("pending") and i.get("checked", True)]


def _texts(items) -> list[str]:
    return list(dict.fromkeys(str(i.get("text") or "").strip() for i in _approved(items) if str(i.get("text") or "").strip()))


# --- Состояние записанного -------------------------------------------------------------------------


def empty_state() -> dict:
    return {"campaign_row_id": None, "account_id": None, "applied": {}, "groups": {}, "negatives": None}


# --- Очередь правок (пункт 4) ------------------------------------------------------------------------
#
# Ход ИИ после «Дальше» пишет в черновик новые шаги, а правки маркетолога на доске в уже записанных шагах
# копятся до «Применить в Директ». Граница — state.edits_upto: правки доски (board_edit) с id больше неё ещё
# не применены. Правки «Подтянуть из Директа» (args.pulled) доску с Директом выравнивают — в очередь не идут.


def edits_upto(actions: list[dict]) -> int | None:
    """До какой правки доски включительно всё применено; None — черновика ещё нет (в него пойдёт всё)."""
    writes = [a for a in actions if a.get("tool") == TOOL]
    if not writes:
        return None
    state = (writes[-1].get("result") or {}).get("state") or {}
    # Записи до пункта 4 писали все правки сразу: применено всё, что было до последней записи.
    return int(state["edits_upto"]) if state.get("edits_upto") is not None else int(writes[-1]["id"])


def queued(action: dict, upto: int | None) -> bool:
    """Правка доски ждёт «Применить в Директ»."""
    return (
        upto is not None
        and action.get("tool") == "board_edit"
        and action.get("status") != "rejected"
        and int(action["id"]) > upto
        and not (action.get("args") or {}).get("pulled")
    )


def last_state(actions: list[dict]) -> dict:
    """Состояние после последней записи (её result.state); записей нет — пустое."""
    for action in reversed(actions):
        if action.get("tool") == TOOL:
            state = (action.get("result") or {}).get("state")
            if isinstance(state, dict):
                return {**empty_state(), **state}
    return empty_state()


def direct_info(actions: list[dict]) -> dict | None:
    """Черновик в Директе для доски и модели: id, что записано и что не прошло в последней записи."""
    writes = [a for a in actions if a.get("tool") == TOOL]
    if not writes:
        return None
    state = last_state(writes)
    last = writes[-1]
    result = last.get("result") or {}
    return {
        "campaign_row_id": state.get("campaign_row_id"),
        "status": last.get("status") or "done",
        "error": last.get("error") or "",
        "warnings": [str(w) for w in result.get("warnings") or []][:MAX_WARNINGS],
        "stages": list(result.get("stages") or []),
        "moderated": any((a.get("result") or {}).get("moderated") for a in writes),
        "written": {
            "groups": len(state.get("groups") or {}),
            "keywords": sum(len(g.get("keywords") or []) for g in (state.get("groups") or {}).values()),
            "ads": sum(1 for g in (state.get("groups") or {}).values() if g.get("ad")),
        },
        "at": last.get("decided_at").isoformat() if hasattr(last.get("decided_at"), "isoformat") else None,
    }


# --- Запись ----------------------------------------------------------------------------------------


class _Writer:
    def __init__(self, user_id: int, workspace_id: int, build: dict, state: dict):
        self.user_id = user_id
        self.ws_id = workspace_id
        self.build = build
        self.state = state
        self.warnings: list[str] = []
        self.stages: list[str] = []
        status = {s["key"]: s["status"] for s in build["stages"]}
        self.done = {k for k, v in status.items() if v == "done"}
        self.skipped = {k for k, v in status.items() if v == "skipped"}

    def warn(self, text: str) -> None:
        if text and text not in self.warnings:
            self.warnings.append(text)

    def mark(self, stage: str) -> None:
        if stage not in self.stages:
            self.stages.append(stage)

    # Кампания

    def campaign(self) -> bool:
        """Черновик есть или создан сейчас; False — писать дальше некуда."""
        from app import actions

        camp = self.build.get("campaign") or {}
        if self.state.get("campaign_row_id"):
            return True
        spec = {
            "type": camp.get("type") or "TEXT_CAMPAIGN",
            "name": camp["name"],
            "strategy": camp.get("strategy") or {},
            "groups": [],
        }
        if camp.get("daily_budget_rub"):
            spec["daily_budget_rub"] = camp["daily_budget_rub"]
        out = actions.create_campaign(
            self.user_id, account_id=int(camp["account_id"]), spec=spec, workspace_id=self.ws_id
        )
        row = out.get("campaign") or {}
        if not row.get("id"):
            raise YandexError("direct", "Директ не вернул созданную кампанию")
        self.state["campaign_row_id"] = int(row["id"])
        self.state["account_id"] = int(camp["account_id"])
        self.state["applied"] = {
            "name": spec["name"],
            "strategy": spec["strategy"],
            "daily_budget_rub": spec.get("daily_budget_rub"),
            "region_ids": list(camp.get("region_ids") or []),
        }
        for text in out.get("warnings") or []:
            self.warn(str(text))
        self.mark("goals")
        return True

    def settings(self) -> None:
        """Имя, стратегия и бюджет — как в согласованном брифе и «Настройках»; регионы — всем нашим группам."""
        from app import actions, adgroups

        camp = self.build.get("campaign") or {}
        applied = self.state.setdefault("applied", {})
        changes: dict = {}
        if camp.get("name") and camp["name"] != applied.get("name"):
            changes["name"] = camp["name"]
        if camp.get("strategy") and camp["strategy"] != applied.get("strategy"):
            changes["strategy"] = camp["strategy"]
        daily = camp.get("daily_budget_rub") or None
        if daily != (applied.get("daily_budget_rub") or None):
            changes["daily_budget_rub"] = daily or 0
        if changes:
            try:
                actions.update_campaign_settings(self.user_id, self.state["campaign_row_id"], changes)
                applied.update({k: (v or None) if k == "daily_budget_rub" else v for k, v in changes.items()})
                self.mark("settings")
            except YandexError as exc:
                self.warn(f"Настройки кампании не записаны: {exc.message or exc.code}")
        regions = list(camp.get("region_ids") or [])
        if regions and regions != list(applied.get("region_ids") or []):
            ids = [int(g["id"]) for g in (self.state.get("groups") or {}).values() if g.get("id")]
            try:
                if ids:
                    out = adgroups.set_regions(self.user_id, regions, adgroup_row_ids=ids, workspace_id=self.ws_id)
                    for text in out.get("errors") or []:
                        self.warn(f"Регионы: {text}")
                applied["region_ids"] = regions
                self.mark("settings")
            except YandexError as exc:
                self.warn(f"Регионы групп не записаны: {exc.message or exc.code}")

    # Группы и фразы

    def groups(self) -> None:
        from app import adgroups, keywords

        row_id = self.state["campaign_row_id"]
        regions = list((self.build.get("campaign") or {}).get("region_ids") or [])
        mine: dict = self.state.setdefault("groups", {})
        wanted = {}
        for group in self.build["groups"]:
            phrases = _texts(group.get("keywords"))
            if phrases or _approved(group.get("audiences")):
                wanted[group["name"]] = phrases
        # Группы, которых на доске больше нет (переименовали, убрали все фразы), — из черновика.
        gone = [name for name in mine if name not in wanted]
        if gone:
            ids = [int(mine[n]["id"]) for n in gone if mine[n].get("id")]
            try:
                if ids:
                    adgroups.delete_adgroups(self.user_id, ids)
                for name in gone:
                    mine.pop(name, None)
                self.mark("keywords")
            except YandexError as exc:
                self.warn(f"Лишние группы не удалены: {exc.message or exc.code}")
        for name, phrases in wanted.items():
            entry = mine.get(name)
            if entry is None:
                try:
                    made = adgroups.create_adgroup(
                        self.user_id, campaign_row_id=row_id, name=name, region_ids=regions or None
                    )
                except YandexError as exc:
                    self.warn(f"Группа «{name}» не создана: {exc.message or exc.code}")
                    continue
                entry = mine[name] = {
                    "id": int(made["id"]), "direct_id": int(made["adgroup_id"]), "keywords": [], "ad": None,
                    "audiences": [],
                }
                self.mark("keywords")
            have = {_norm(p): p for p in entry.get("keywords") or []}
            want = {_norm(p): p for p in phrases}
            add = [p for k, p in want.items() if k not in have]
            drop = [p for k, p in have.items() if k not in want]
            for start in range(0, len(add[:200]), 100):
                part = add[start:start + 100]
                try:
                    res = keywords.create_keywords(self.user_id, adgroup_row_id=entry["id"], phrases=part)
                except YandexError as exc:
                    self.warn(f"«{name}»: фразы не добавлены: {exc.message or exc.code}")
                    continue
                failed = int(res.get("errors") or 0)
                if failed:
                    reason = f": {res['message']}" if res.get("message") else ""
                    self.warn(f"«{name}»: не добавлено фраз: {failed}{reason}")
                added = {_norm(k.get("keyword")) for k in res.get("items") or [] if isinstance(k, dict)}
                ok = part if not failed else [p for p in part if _norm(p) in added]
                entry["keywords"] = list(entry.get("keywords") or []) + ok
                self.mark("keywords")
            if drop:
                keys = {_norm(p) for p in drop}
                rows = [
                    int(k["id"]) for k in keywords.list_keywords(self.user_id, adgroup_row_id=entry["id"])
                    if _norm(k["keyword"]) in keys
                ]
                try:
                    if rows:
                        keywords.bulk_keyword_action(self.user_id, keyword_row_ids=rows[:100], action="delete")
                    entry["keywords"] = [p for p in entry.get("keywords") or [] if _norm(p) not in keys]
                    self.mark("keywords")
                except YandexError as exc:
                    self.warn(f"«{name}»: снятые фразы не удалены: {exc.message or exc.code}")

    def negatives(self) -> None:
        from app import actions

        want = _texts(self.build.get("negatives"))
        if self.state.get("negatives") is not None and [_norm(p) for p in want] == [
            _norm(p) for p in self.state["negatives"]
        ]:
            return
        if self.state.get("negatives") is None and not want:
            return
        try:
            actions.update_campaign_settings(self.user_id, self.state["campaign_row_id"], {"negative_keywords": want})
            self.state["negatives"] = want
            self.mark("negatives")
        except YandexError as exc:
            self.warn(f"Минус-фразы не записаны: {exc.message or exc.code}")

    def audiences(self) -> None:
        from app import actions, audiences
        from app.workspaces import workspace_goals

        mine = self.state.get("groups") or {}
        todo = []
        for group in self.build["groups"]:
            entry = mine.get(group["name"])
            if entry is None:
                continue
            fresh = [
                a for a in _approved(group.get("audiences"))
                if isinstance(a.get("audience"), dict) and _norm(a.get("text")) not in set(entry.get("audiences") or [])
            ]
            if fresh:
                todo.append((group["name"], entry, fresh))
        if not todo:
            return
        camp = actions._campaign_full(self.user_id, self.state["campaign_row_id"])
        token = get_connection_access_token(camp["connection_id"], self.user_id)
        network = ((self.build.get("campaign") or {}).get("strategy") or {}).get("network") != "SERVING_OFF"
        allowed = {int(g["goal_id"]) for g in workspace_goals(self.user_id, self.ws_id)} or None
        spec_groups = [{"name": name, "audiences": [a["audience"] for a in fresh]} for name, _e, fresh in todo]
        try:
            ctx = audiences.prepare(
                token, camp["client_login"], spec_groups, network=network, allowed_goals=allowed,
                rights=audiences.account_rights(self.user_id, int(camp["account_id"])),
            )
        except YandexError as exc:
            self.warn(f"Аудитории не записаны: {exc.message or exc.code}")
            return
        for index, (name, entry, fresh) in enumerate(todo):
            if not entry.get("direct_id"):
                continue
            res = audiences.attach(token, camp["client_login"], int(entry["direct_id"]), ctx["groups"][index], ctx)
            for text in res.get("warnings") or []:
                self.warn(f"«{name}»: {text}")
            if res.get("attached"):
                entry["audiences"] = list(entry.get("audiences") or []) + [_norm(a.get("text")) for a in fresh]
                self.mark("audiences")

    # Объявления и расширения

    def _href(self, group: dict) -> str:
        return str(group.get("href") or (self.build.get("campaign") or {}).get("href") or "").strip()

    def ads(self) -> None:
        from app import ads, links

        mine = self.state.get("groups") or {}
        template = links.campaign_template(self.user_id, self.state["campaign_row_id"])
        for group in self.build["groups"]:
            entry = mine.get(group["name"])
            titles, texts = _texts(group.get("titles")), _texts(group.get("texts"))
            if entry is None or not titles or not texts:
                continue
            href = self._href(group) or (links.template_href(template) if template else "")
            if not href:
                self.warn(f"«{group['name']}»: объявление не создано — нет ссылки на сайт")
                continue
            sig = {"titles": titles, "texts": texts, "href": href}
            if entry.get("ad") and entry.get("ad_sig") == sig:
                continue
            try:
                if entry.get("ad"):
                    made = ads.update_text_ad(self.user_id, int(entry["ad"]), titles=titles, texts=texts, href=href)
                else:
                    made = ads.create_text_ad(
                        self.user_id, adgroup_row_id=int(entry["id"]), href=href, titles=titles, texts=texts
                    )
                    entry["ad"] = int(made["id"])
                    entry["extras_sig"] = None
                entry["ad_sig"] = sig
                # Как текст лёг в Директ (он чистит кавычки и пробелы) — с этим сверка и сравнивает.
                made = made if isinstance(made, dict) else {}
                entry["ad_direct"] = {
                    "titles": list(made.get("titles") or titles), "texts": list(made.get("texts") or texts),
                }
                self.mark("ads")
            except YandexError as exc:
                self.warn(f"«{group['name']}»: объявление не записано: {exc.message or exc.code}")

    def extras(self) -> None:
        from app import ads

        mine = self.state.get("groups") or {}
        sitelinks = [
            {"title": s.get("text") or "", "href": s.get("href") or "", "description": s.get("description") or ""}
            for s in _approved(self.build.get("sitelinks"))
        ]
        callouts = _texts(self.build.get("callouts"))
        for group in self.build["groups"]:
            entry = mine.get(group["name"])
            if entry is None or not entry.get("ad"):
                continue
            extras: dict = {}
            if "images" in self.done:
                extras["image_hashes"] = [i["hash"] for i in _approved(group.get("images")) if i.get("hash")]
            if "extras" in self.done:
                href = self._href(group)
                extras["sitelinks"] = [{**s, "href": s["href"] or href} for s in sitelinks]
                extras["callouts"] = callouts
            if not extras or entry.get("extras_sig") == extras:
                continue
            try:
                ads.set_ads_extras(self.user_id, [int(entry["ad"])], extras)
                entry["extras_sig"] = extras
                self.mark("extras" if "sitelinks" in extras else "images")
            except YandexError as exc:
                self.warn(f"«{group['name']}»: расширения не записаны: {exc.message or exc.code}")

    def run(self) -> None:
        if "goals" not in self.done or not (self.build.get("campaign") or {}).get("name"):
            return
        self.campaign()
        self.settings()
        if "keywords" in self.done:
            self.groups()
        if "keywords" in self.done or "negatives" in self.done:
            self.negatives()
        if "audiences" in self.done:
            self.audiences()
        if "ads" in self.done:
            self.ads()
        if self.done & {"images", "extras"}:
            self.extras()


def sync(
    user_id: int, workspace_id: int, chat_id: int, build: dict | None, state: dict, upto: int | None = None
) -> dict | None:
    """Дописать согласованное в черновик. None — писать нечего (сборка не по шагам или ничего не изменилось).
    state — что уже записано в этой сборке (last_state её действий); upto — до какой правки доски теперь
    всё применено (state.edits_upto после записи)."""
    if not build or not build.get("campaign") or build.get("draft"):
        return None
    state = {**empty_state(), **(state or {})}
    moved = upto is not None and upto != state.get("edits_upto")
    if upto is not None:
        state["edits_upto"] = upto
    writer = _Writer(user_id, workspace_id, build, {**state, "groups": {k: dict(v) for k, v in state["groups"].items()}})
    error = ""
    try:
        writer.run()
    except YandexError as exc:
        error = exc.message or exc.code or "Директ не принял запись"
    except Exception:  # запись в Директ не должна ронять ход ИИ: ошибка — на доске, «Повторить»
        log.exception("Запись сборки в Директ, чат %s", chat_id)
        error = "Сбой при записи в Директ"
    if not writer.stages and not writer.warnings and not error:
        if not moved or not writer.state.get("campaign_row_id"):
            return None
        # Записывать было нечего (правки свелись к тому, что уже в Директе), но очередь применена.
        result = {"state": writer.state, "stages": [], "warnings": []}
        _record(user_id, chat_id, result, summary="Правки доски совпадают с Директом")
        return {**result, "error": ""}
    result = {"state": writer.state, "stages": writer.stages, "warnings": writer.warnings[:MAX_WARNINGS]}
    _record(user_id, chat_id, result, error)
    return {**result, "error": error}


def _record(user_id: int, chat_id: int, result: dict, error: str = "", summary: str = "") -> None:
    with pool.connection() as conn:
        anchor = conn.execute("SELECT MAX(id) AS id FROM ai_messages WHERE chat_id = %s", (chat_id,)).fetchone()["id"]
        if anchor is None:
            return
        conn.execute(
            """
            INSERT INTO ai_actions
                (chat_id, message_id, user_id, tool_call_id, tool, args, summary, category, status, auto, result, error, decided_at)
            VALUES (%s, %s, %s, '', %s, '{}'::jsonb, %s, '', %s, FALSE, %s, %s, NOW())
            """,
            (
                chat_id, anchor, user_id, TOOL, summary or _summary(result, error),
                "error" if error else "done", Jsonb(result), error,
            ),
        )


_STAGE_WORDS = {
    "goals": "черновик создан",
    "keywords": "группы и фразы",
    "negatives": "минус-фразы",
    "audiences": "аудитории",
    "ads": "объявления",
    "images": "картинки",
    "extras": "расширения",
    "settings": "настройки",
}


def _summary(result: dict, error: str) -> str:
    if error:
        return f"Не записано в Директ: {error}"
    parts = [_STAGE_WORDS.get(s, s) for s in result.get("stages") or []]
    text = "Записано в Директ: " + ", ".join(parts) if parts else "Записано в Директ"
    if result.get("warnings"):
        text += f" (замечаний: {len(result['warnings'])})"
    return text


# --- Модерация -------------------------------------------------------------------------------------


def moderate(user_id: int, chat_id: int, build: dict) -> dict:
    """«Отправить на модерацию» с доски: только записанный целиком черновик без красных проверок."""
    from app import actions as direct_actions

    state = build.get("direct_state") or empty_state()
    if not state.get("campaign_row_id"):
        raise YandexError("args", "Черновика в Директе ещё нет")
    status = {s["key"]: s["status"] for s in build["stages"]}
    left = [s["label"] for s in build["stages"] if s["status"] in ("todo", "pending", "rejected") and s["key"] not in (
        "negatives", "audiences", "images", "extras"
    )]
    if left or status.get("settings") != "done":
        raise YandexError("args", "Сначала пройдите шаги: " + ", ".join(left or ["Настройки"]))
    bad = [c["label"] for c in build.get("checks") or [] if c["state"] == "bad"]
    if bad:
        raise YandexError("args", "Исправьте красные проверки: " + ", ".join(bad))
    info = build.get("direct") or {}
    if info.get("moderated"):
        raise YandexError("args", "Черновик уже отправлен на модерацию")
    if info.get("status") == "error":
        raise YandexError("args", "Последняя запись в Директ не прошла — повторите её")
    out = direct_actions.moderate_campaign(user_id, int(state["campaign_row_id"]))
    result = {"state": state, "stages": [], "warnings": [str(e) for e in out.get("errors") or []], "moderated": True}
    sent = int(out.get("sent") or 0)
    _record(user_id, chat_id, result, "" if sent or not out.get("errors") else "Директ не принял объявления",
            summary=f"Отправлено на модерацию: объявлений {sent}")
    return out


# --- Доска из Директа ------------------------------------------------------------------------------


def direct_snapshot(user_id: int, campaign_row_id: int) -> dict | None:
    """Кампания из локальной копии Директа: группы с фразами и объявлениями, минус-фразы, состояние."""
    from app import adgroups, ads, keywords

    with pool.connection() as conn:
        camp = conn.execute(
            "SELECT id, campaign_id, name, state, status, negative_keywords, campaign_type, account_id "
            "FROM direct_campaigns WHERE user_id = %s AND id = %s",
            (user_id, campaign_row_id),
        ).fetchone()
    if camp is None:
        return None
    groups = {int(g["id"]): {**g, "keywords": [], "ads": []} for g in adgroups.list_adgroups(user_id, campaign_row_id)}
    for kw in keywords.list_keywords(user_id, campaign_row_id=campaign_row_id):
        if int(kw["adgroup_row_id"]) in groups:
            groups[int(kw["adgroup_row_id"])]["keywords"].append(kw["keyword"])
    for ad in ads.list_ads(user_id, campaign_row_id=campaign_row_id):
        if int(ad["adgroup_row_id"]) in groups:
            groups[int(ad["adgroup_row_id"])]["ads"].append(ad)
    negatives = camp.get("negative_keywords") or []
    if isinstance(negatives, str):
        from app.adgroups import _parse_phrases

        negatives = _parse_phrases(negatives)
    return {
        "campaign": {
            "id": camp["id"], "campaign_id": camp["campaign_id"], "name": camp["name"], "state": camp["state"],
            "status": camp["status"], "type": camp["campaign_type"], "account_id": camp["account_id"],
        },
        "groups": list(groups.values()),
        "negatives": [str(n) for n in negatives],
    }


def _item(text: str, **extra) -> dict:
    return {
        "text": text, "value": "", "note": "", "pending": False, "checked": True, "action_id": None, "n": None,
        "in_direct": True, "direct_only": True, **extra,
    }


def overlay(build: dict, snapshot: dict | None, state: dict) -> None:
    """Доска глазами Директа: ✓ (in_direct) у строки, которая есть в кампании; что есть только в Директе
    (добавили там руками) — строками direct_only. Наше, снятое на доске и ещё не удалённое, не возвращается."""
    if snapshot is None:
        return
    mine = state.get("groups") or {}
    ours_keywords = {name: {_norm(p) for p in g.get("keywords") or []} for name, g in mine.items()}
    by_name = {g["name"]: g for g in snapshot["groups"]}
    board = {g["name"]: g for g in build["groups"]}
    for name, direct in by_name.items():
        group = board.get(name)
        keys = {_norm(k) for k in direct["keywords"]}
        ad = direct["ads"][0] if direct["ads"] else None
        titles = {_norm(t) for a in direct["ads"] for t in a.get("titles") or []}
        texts = {_norm(t) for a in direct["ads"] for t in a.get("texts") or []}
        hashes = {h for a in direct["ads"] for h in a.get("image_hashes") or []}
        if group is None:
            if name in mine:
                continue  # наша группа, которую убрали с доски, — удалится следующей записью
            group = {
                "name": name, "keywords": [], "titles": [], "texts": [], "audiences": [], "images": [], "href": "",
                "sitelinks": None, "callouts": None, "extras_from_ad": False, "direct_only": True,
            }
            build["groups"].append(group)
            board[name] = group
            if ad:
                group["titles"] = [_item(t) for t in ad.get("titles") or []]
                group["texts"] = [_item(t) for t in ad.get("texts") or []]
        group["in_direct"] = True
        group["adgroup_row_id"] = direct["id"]
        for item in group["keywords"]:
            item["in_direct"] = _norm(item["text"]) in keys
        for item in group["titles"]:
            item["in_direct"] = _norm(item["text"]) in titles
        for item in group["texts"]:
            item["in_direct"] = _norm(item["text"]) in texts
        for item in group.get("images") or []:
            item["in_direct"] = item.get("hash") in hashes
        on_board = {_norm(i["text"]) for i in group["keywords"]}
        ours = ours_keywords.get(name, set())
        group["keywords"] += [
            _item(k) for k in direct["keywords"] if _norm(k) not in on_board and _norm(k) not in ours
        ]
    negatives = {_norm(n) for n in snapshot["negatives"]}
    for item in build["negatives"]:
        item["in_direct"] = _norm(item["text"]) in negatives
    written = state.get("negatives") or []
    on_board = {_norm(n["text"]) for n in build["negatives"]}
    build["negatives"] += [
        _item(n) for n in snapshot["negatives"] if _norm(n) not in on_board and _norm(n) not in {_norm(w) for w in written}
    ]
    build["direct_campaign"] = snapshot["campaign"]


# --- Очередь «Применить в Директ» ------------------------------------------------------------------


def _change(kind: str, op: str, text: str, group: str = "", **extra) -> dict:
    return {"kind": kind, "op": op, "group": group, "text": text, **extra}


def _done(build: dict) -> set[str]:
    return {s["key"] for s in build.get("stages") or [] if s["status"] == "done"}


def changes(build: dict | None, state: dict) -> list[dict]:
    """Что изменится в черновике по «Применить в Директ»: доска против записанного (state). Тот же расчёт, что
    у записи (_Writer), только без вызовов Директа. Пусто — доска и черновик совпадают."""
    if not build or not build.get("campaign") or build.get("draft") or not (state or {}).get("campaign_row_id"):
        return []
    done = _done(build)
    out: list[dict] = []
    camp = build.get("campaign") or {}
    applied = state.get("applied") or {}
    if camp.get("name") and camp["name"] != applied.get("name"):
        out.append(_change("campaign", "edit", f"Название: «{camp['name']}»"))
    if camp.get("strategy") and camp["strategy"] != applied.get("strategy"):
        out.append(_change("campaign", "edit", "Стратегия"))
    daily = camp.get("daily_budget_rub") or None
    if daily != (applied.get("daily_budget_rub") or None):
        out.append(_change("campaign", "edit", f"Бюджет в день: {daily} ₽" if daily else "Бюджет в день: снять"))
    regions = list(camp.get("region_ids") or [])
    if regions and regions != list(applied.get("region_ids") or []):
        out.append(_change("campaign", "edit", "Регионы"))

    mine = state.get("groups") or {}
    planned: set[str] = set(mine)
    if "keywords" in done:
        wanted = {}
        for group in build["groups"]:
            phrases = _texts(group.get("keywords"))
            if phrases or _approved(group.get("audiences")):
                wanted[group["name"]] = phrases
        for name in mine:
            if name not in wanted:
                out.append(_change("group", "remove", name, name))
        planned = set(wanted)
        for name, phrases in wanted.items():
            entry = mine.get(name) or {}
            if name not in mine:
                out.append(_change("group", "add", name, name))
            have = {_norm(p) for p in entry.get("keywords") or []}
            want = {_norm(p): p for p in phrases}
            out += [_change("keyword", "add", p, name) for k, p in want.items() if k not in have]
            out += [_change("keyword", "remove", p, name) for p in entry.get("keywords") or [] if _norm(p) not in want]
    if done & {"keywords", "negatives"}:
        want = _texts(build.get("negatives"))
        have = state.get("negatives")
        if have is not None or want:
            had = {_norm(p) for p in have or []}
            keys = {_norm(p) for p in want}
            out += [_change("negative", "add", p) for p in want if _norm(p) not in had]
            out += [_change("negative", "remove", p) for p in have or [] if _norm(p) not in keys]
    if "audiences" in done:
        for group in build["groups"]:
            if group["name"] not in planned:
                continue
            had = set((mine.get(group["name"]) or {}).get("audiences") or [])
            out += [
                _change("audience", "add", a.get("text") or "", group["name"])
                for a in _approved(group.get("audiences"))
                if isinstance(a.get("audience"), dict) and _norm(a.get("text")) not in had
            ]
    images: list[str] = []
    extras: list[str] = []
    sitelinks = [s.get("text") or "" for s in _approved(build.get("sitelinks"))]
    callouts = _texts(build.get("callouts"))
    for group in build["groups"]:
        name = group["name"]
        if name not in planned:
            continue
        entry = mine.get(name) or {}
        titles, texts = _texts(group.get("titles")), _texts(group.get("texts"))
        will_have_ad = "ads" in done and bool(titles and texts)
        if will_have_ad:
            sig = entry.get("ad_sig") or {}
            if not entry.get("ad"):
                out.append(_change("ad", "add", f"заголовков {len(titles)}, текстов {len(texts)}", name))
            else:
                for kind, now, before in (("title", titles, sig.get("titles")), ("text", texts, sig.get("texts"))):
                    had = {_norm(t) for t in before or []}
                    keys = {_norm(t) for t in now}
                    out += [_change(kind, "add", t, name) for t in now if _norm(t) not in had]
                    out += [_change(kind, "remove", t, name) for t in before or [] if _norm(t) not in keys]
        if not entry.get("ad") and not will_have_ad:
            continue
        sig = entry.get("extras_sig") or {}
        if "images" in done:
            hashes = [i["hash"] for i in _approved(group.get("images")) if i.get("hash")]
            if sorted(hashes) != sorted(sig.get("image_hashes") or []):
                images.append(name)
        if "extras" in done and (
            [s.get("title") for s in sig.get("sitelinks") or []] != sitelinks
            or list(sig.get("callouts") or []) != callouts
        ):
            extras.append(name)
    if images:
        out.append(_change("images", "edit", "Картинки: " + ", ".join(f"«{n}»" for n in images[:5])))
    if extras:
        out.append(_change("extras", "edit", f"Быстрые ссылки и уточнения — групп: {len(extras)}"))
    return out


# --- Сверка с Директом -------------------------------------------------------------------------------


def _direct_ad(group: dict, ad_row_id) -> dict | None:
    return next((a for a in group.get("ads") or [] if int(a["id"]) == int(ad_row_id)), None)


def drift(snapshot: dict | None, state: dict, queue: list[dict] | None = None) -> list[dict]:
    """Что в черновике поменяли в самом Директе с последней записи: Директ (локальная копия) против state.
    conflict — тот же объект правят и на доске (он в очереди «Применить») иначе, чем в Директе: одну фразу
    удалили и там, и там — не конфликт."""
    if snapshot is None or not (state or {}).get("campaign_row_id"):
        return []
    queue = queue or []
    ops: dict[tuple, set[str]] = {}
    for c in queue:
        ops.setdefault((c["kind"], c["group"], _norm(c["text"])), set()).add(c["op"])

    def differs(kind: str, group: str, text: str, op: str) -> bool:
        return bool(ops.get((kind, group, _norm(text)), set()) - {op})

    groups_touched = {c["group"] for c in queue if c["group"]}
    ads_touched = {c["group"] for c in queue if c["kind"] in ("title", "text", "ad")}
    by_id = {int(g["id"]): g for g in snapshot["groups"]}
    out: list[dict] = []
    for name, entry in (state.get("groups") or {}).items():
        direct = by_id.get(int(entry["id"])) if entry.get("id") else None
        if direct is None:
            out.append(_change("group", "remove", name, name, conflict=name in groups_touched))
            continue
        if direct["name"] != name:
            out.append(_change("group", "edit", direct["name"], name, conflict=name in groups_touched))
        have = {_norm(k): k for k in direct["keywords"]}
        ours = {_norm(p) for p in entry.get("keywords") or []}
        out += [
            _change("keyword", "add", k, name, conflict=differs("keyword", name, k, "add"))
            for key, k in have.items() if key not in ours
        ]
        out += [
            _change("keyword", "remove", p, name, conflict=differs("keyword", name, p, "remove"))
            for p in entry.get("keywords") or [] if _norm(p) not in have
        ]
        if entry.get("ad"):
            ad = _direct_ad(direct, entry["ad"])
            if ad is None:
                out.append(_change("ad", "remove", "объявление удалено", name, conflict=name in ads_touched))
                continue
            wrote = entry.get("ad_direct") or entry.get("ad_sig") or {}
            now = {"titles": list(ad.get("titles") or []), "texts": list(ad.get("texts") or [])}
            if any(
                sorted(_norm(t) for t in now[key]) != sorted(_norm(t) for t in wrote.get(key) or [])
                for key in ("titles", "texts")
            ):
                out.append(_change(
                    "ad", "edit", " · ".join(now["titles"][:3]), name, conflict=name in ads_touched, direct=now,
                ))
    if state.get("negatives") is not None:
        have = {_norm(n): n for n in snapshot["negatives"]}
        ours = {_norm(n) for n in state["negatives"]}
        out += [
            _change("negative", "add", n, conflict=differs("negative", "", n, "add"))
            for key, n in have.items() if key not in ours
        ]
        out += [
            _change("negative", "remove", n, conflict=differs("negative", "", n, "remove"))
            for n in state["negatives"] if _norm(n) not in have
        ]
    return out


def refresh_local(user_id: int, campaign_row_id: int) -> None:
    """Перечитать черновик из Директа в локальную копию: группы, фразы, объявления, название и минус-фразы.
    Удалённое в Директе из копии тоже уходит (обычная синхронизация только дописывает)."""
    from app import actions, adgroups, ads, keywords

    camp = actions._campaign_full(user_id, campaign_row_id)
    token = get_connection_access_token(camp["connection_id"], user_id)
    login, account_id = camp["client_login"], int(camp["account_id"])
    items = adgroups.fetch_adgroups(token, login, int(camp["campaign_id"]))
    adgroups.upsert_adgroups(user_id, account_id, campaign_row_id, items)
    with pool.connection() as conn:
        conn.execute(
            "DELETE FROM direct_adgroups WHERE user_id = %s AND campaign_row_id = %s AND NOT (adgroup_id = ANY(%s))",
            (user_id, campaign_row_id, [int(i["adgroup_id"]) for i in items]),
        )
        rows = conn.execute(
            "SELECT id, adgroup_id FROM direct_adgroups WHERE user_id = %s AND campaign_row_id = %s",
            (user_id, campaign_row_id),
        ).fetchall()
    map_ids = {int(r["adgroup_id"]): int(r["id"]) for r in rows}
    if map_ids:
        found = keywords.fetch_keywords(token, login, list(map_ids))
        keywords.upsert_keywords(user_id, account_id, map_ids, found)
        made = ads.fetch_ads(token, login, list(map_ids))
        ads.upsert_ads(user_id, account_id, map_ids, made)
        with pool.connection() as conn:
            conn.execute(
                "DELETE FROM direct_keywords WHERE user_id = %s AND adgroup_row_id = ANY(%s) "
                "AND NOT (keyword_id = ANY(%s))",
                (user_id, list(map_ids.values()), [int(k["keyword_id"]) for k in found]),
            )
            conn.execute(
                "DELETE FROM direct_ads WHERE user_id = %s AND adgroup_row_id = ANY(%s) AND NOT (ad_id = ANY(%s))",
                (user_id, list(map_ids.values()), [int(a["ad_id"]) for a in made]),
            )
    settings = actions.get_campaign_settings(user_id, campaign_row_id)
    actions._update_local(
        campaign_row_id, {"name": settings["name"], "negative_keywords": list(settings.get("negative_keywords") or [])}
    )


def check(user_id: int, chat_id: int, state: dict) -> bool:
    """Лёгкая сверка черновика с Директом: changes.checkCampaigns с прошлой метки; кампанию меняли — перечитать
    её в локальную копию. → True, если перечитали. Метка хранится в последней записи сборки."""
    from app import actions, direct_api
    from app.account_load import parse_changed_campaigns

    row_id = (state or {}).get("campaign_row_id")
    if not row_id:
        return False
    camp = actions._campaign_full(user_id, int(row_id))
    token = get_connection_access_token(camp["connection_id"], user_id)
    since = state.get("direct_ts")
    if since:
        res = direct_api.call(token, camp["client_login"], "changes", "checkCampaigns", {"Timestamp": since})
        changed = int(camp["campaign_id"]) in parse_changed_campaigns(res)
    else:
        # Метки ещё нет — перечитать один раз и запомнить, с какого момента смотреть дальше.
        res = direct_api.call(token, camp["client_login"], "changes", "checkDictionaries", {})
        changed = True
    if changed:
        refresh_local(user_id, int(row_id))
    stamp = str((res or {}).get("Timestamp") or "")
    if stamp:
        with pool.connection() as conn:
            conn.execute(
                """
                UPDATE ai_actions SET result = jsonb_set(result, '{state,direct_ts}', to_jsonb(%s::text))
                WHERE id = (SELECT MAX(id) FROM ai_actions WHERE chat_id = %s AND tool = %s)
                  AND result ? 'state'
                """,
                (stamp, chat_id, TOOL),
            )
    return changed


def pulled_state(state: dict, snapshot: dict) -> dict:
    """Записанное = как сейчас в Директе (после «Подтянуть» или «Оставить мои» сверка начинается с нуля)."""
    out = {**empty_state(), **state, "groups": {}}
    by_id = {int(g["id"]): g for g in snapshot["groups"]}
    for name, entry in (state.get("groups") or {}).items():
        direct = by_id.get(int(entry["id"])) if entry.get("id") else None
        if direct is None:
            continue
        entry = {**entry, "keywords": list(direct["keywords"])}
        if entry.get("ad"):
            ad = _direct_ad(direct, entry["ad"])
            if ad is None:
                entry.update(ad=None, ad_sig=None, ad_direct=None, extras_sig=None)
            else:
                now = {"titles": list(ad.get("titles") or []), "texts": list(ad.get("texts") or [])}
                entry["ad_direct"] = now
                entry["ad_sig"] = {**(entry.get("ad_sig") or {}), **now}
        out["groups"][direct["name"]] = entry
    if state.get("negatives") is not None or snapshot["negatives"]:
        out["negatives"] = list(snapshot["negatives"])
    return out


def revert_ops(queue: list[dict]) -> list[dict]:
    """Правки доски, отменяющие очередь «Применить» (фразы, минус-фразы, заголовки, тексты): доска = записанное."""
    flip = {"add": "remove", "remove": "add"}
    return [
        {"op": flip[c["op"]], "kind": c["kind"], "group": c["group"], "text": c["text"]}
        for c in queue
        if c["kind"] in ("keyword", "negative", "title", "text") and c["op"] in flip
    ]


def pull_ops(build: dict, snapshot: dict, state: dict) -> list[dict]:
    """Правки доски, после которых на ней то же, что в Директе (по объектам из drift)."""
    board = {g["name"]: g for g in build["groups"]}
    ops: list[dict] = []

    def approved(items) -> list[str]:
        return [i["text"] for i in items or [] if not i.get("pending") and i.get("checked", True)]

    for item in drift(snapshot, state):
        group = board.get(item["group"]) if item["group"] else None
        kind, op = item["kind"], item["op"]
        if kind == "group" and op == "remove" and group:
            for key, k in (("keywords", "keyword"), ("titles", "title"), ("texts", "text")):
                ops += [{"op": "remove", "kind": k, "group": item["group"], "text": t} for t in approved(group[key])]
        elif kind == "group" and op == "edit" and group:
            ops.append({"op": "rename_group", "group": item["group"], "to": item["text"]})
        elif kind == "keyword":
            ops.append({"op": op, "kind": "keyword", "group": item["group"], "text": item["text"]})
        elif kind == "ad" and group:
            now = item.get("direct") or {"titles": [], "texts": []}
            for key, k in (("titles", "title"), ("texts", "text")):
                keep = {_norm(t) for t in now[key]}
                have = {_norm(t) for t in approved(group[key])}
                ops += [
                    {"op": "remove", "kind": k, "group": item["group"], "text": t}
                    for t in approved(group[key]) if _norm(t) not in keep
                ]
                ops += [{"op": "add", "kind": k, "group": item["group"], "text": t} for t in now[key] if _norm(t) not in have]
        elif kind == "negative":
            ops.append({"op": op, "kind": "negative", "group": "", "text": item["text"]})
    # Переименование — последним: правки выше адресуют группу по старому имени.
    return [o for o in ops if o["op"] != "rename_group"] + [o for o in ops if o["op"] == "rename_group"]
