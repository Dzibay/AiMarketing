"""«Для новых проектов» и «Взять из проекта…».

Настройки по умолчанию хранятся у пользователя (users.project_defaults) и применяются к каждому новому проекту:
метки ссылки (домен подставляется из сайта проекта), класс модели ИИ, доступ ИИ, главный показатель (KPI) и
«Защита бюджета» (правила из rules_templates).

«Взять из проекта…» переносит выбранные настройки из другого проекта того же пользователя; перед переносом
показывает «было → станет» по каждому полю. Правки настроек проекта в журнал не пишутся (как и ручное
сохранение настроек), поэтому перенос просто сохраняет новые значения.
"""

from __future__ import annotations

import copy
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from app.db import pool
from app.yandex import YandexError

log = logging.getLogger(__name__)

# Что можно сделать настройкой по умолчанию — и что предложить «Сделать так по умолчанию?».
DEFAULT_KINDS = ("url_template", "model_class", "ai_access", "kpi", "budget_protection")

# Что можно взять из другого проекта: ключ, подпись.
COPY_KINDS = [
    ("url_template", "Шаблон ссылки"),
    ("ai_style", "Тон и обращение ИИ"),
    ("model_class", "Модель ИИ"),
    ("ai_access", "Доступ ИИ"),
    ("kpi", "Главный показатель"),
    ("budget_protection", "Защита бюджета"),
    ("ai_notes", "Заметки для ИИ"),
    ("ai_facts", "Что ИИ запомнил"),
]
COPY_LABELS = dict(COPY_KINDS)

TONE_LABELS = {"": "Как в аккаунте", "calm": "Спокойный", "sell": "Продающий", "expert": "Экспертный"}
ADDRESS_LABELS = {"vy": "на «вы»", "ty": "на «ты»"}


# --- Шаблон ссылки без домена ---------------------------------------------------------------


def site_host(site: str | None) -> tuple[str, str]:
    """(схема, домен) сайта проекта: «example.ru», «https://example.ru/» → ('https', 'example.ru')."""
    text = (site or "").strip()
    if not text:
        return "https", ""
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return "https", ""
    host = (parts.hostname or "").strip(".")
    if "." not in host:
        return "https", ""
    scheme = parts.scheme if parts.scheme in ("http", "https") else "https"
    netloc = parts.netloc.split("@")[-1]
    return scheme, netloc or host


def _looks_like_host(text: str) -> bool:
    head = text.split("/", 1)[0].split("?", 1)[0]
    return "." in head and "=" not in head and "{" not in head


def relative_template(raw: str | None) -> str:
    """Шаблон без домена: «/путь?метки». Принимает полную ссылку, «/?…», «?…» или просто «utm_source=…»."""
    from app.links import parse_template

    text = (raw or "").strip()
    if not text:
        return ""
    if "://" in text or _looks_like_host(text):
        t = parse_template(text)
        if t is None:
            return ""
        query = urlsplit(t["raw"]).query
        return (t["path"] or "/") + (f"?{query}" if query else "")
    if text.startswith("?"):
        text = "/" + text
    elif not text.startswith("/"):
        text = "/?" + text
    # Проверяем так же, как шаблон проекта, — на условном домене.
    t = parse_template("https://example.ru" + text)
    if t is None:
        return ""
    query = urlsplit(t["raw"]).query
    return (t["path"] or "/") + (f"?{query}" if query else "")


def template_for_site(relative: str, site: str | None) -> str:
    """Полный шаблон для сайта проекта; без сайта — пустая строка (шаблон не задаём)."""
    from app.links import clean_template

    if not relative:
        return ""
    scheme, host = site_host(site)
    if not host:
        return ""
    return clean_template(f"{scheme}://{host}{relative}")


# --- Хранение настроек по умолчанию ------------------------------------------------------------


def _clean_kpi(raw) -> dict | None:
    from app.workspaces import KPI_TYPES, _clean_kpi_target

    if not isinstance(raw, dict):
        return None
    kpi_type = str(raw.get("kpi_type") or "").strip().lower()
    if kpi_type not in KPI_TYPES:
        raise YandexError("kpi-type", "KPI — CPA или ДРР")
    if not kpi_type:
        return None
    target = _clean_kpi_target(raw.get("kpi_target")) if raw.get("kpi_target") not in (None, "") else None
    return {"kpi_type": kpi_type, "kpi_target": float(target) if target is not None else None}


def _clean_protection(raw) -> dict | None:
    from app.rules_templates import BY_KEY, RUB_MAX, RUB_MIN, X_MAX, X_MIN

    if not isinstance(raw, dict):
        return None
    out: dict = {}
    for key, item in raw.items():
        tpl = BY_KEY.get(key)
        if tpl is None or not isinstance(item, dict):
            continue
        entry = {"enabled": bool(item.get("enabled"))}
        if tpl["money"] and item.get("value") not in (None, ""):
            kind = item.get("threshold_kind") or "rub"
            if kind not in ("rub", "cpa_x"):
                raise YandexError("threshold", "Порог — в рублях или в ценах заявки")
            try:
                number = Decimal(str(item["value"]))
            except (InvalidOperation, ValueError) as exc:
                raise YandexError("threshold", "Укажите порог числом") from exc
            if not number.is_finite():
                raise YandexError("threshold", "Укажите порог числом")
            if kind == "cpa_x" and not X_MIN <= number <= X_MAX:
                raise YandexError("threshold", "Порог — от 0,5 до 50 цен заявки")
            if kind == "rub" and not RUB_MIN <= number <= RUB_MAX:
                raise YandexError("threshold", "Порог — от 100 ₽")
            entry["threshold_kind"] = kind
            entry["value"] = float(number)
        out[key] = entry
    return out if any(e["enabled"] for e in out.values()) else None


def _clean_model_class(raw) -> str:
    value = str(raw or "").strip()[:40]
    if value:
        from app import ai

        if value not in {c["id"] for c in ai.chat_classes()["items"]}:
            raise YandexError("model-class", "Эта модель сейчас недоступна")
    return value


def normalize(raw) -> dict:
    """Сохранённые настройки: неизвестное отбрасывается, кривое — «не задано» (без ошибок при чтении)."""
    from app.workspaces import normalize_ai_access

    raw = raw if isinstance(raw, dict) else {}
    out = {"url_template": "", "model_class": "", "ai_access": None, "kpi": None, "budget_protection": None}
    if isinstance(raw.get("url_template"), str):
        out["url_template"] = raw["url_template"]
    if isinstance(raw.get("model_class"), str):
        out["model_class"] = raw["model_class"][:40]
    if isinstance(raw.get("ai_access"), dict):
        out["ai_access"] = normalize_ai_access(raw["ai_access"])
    for key, clean in (("kpi", _clean_kpi), ("budget_protection", _clean_protection)):
        try:
            out[key] = clean(raw.get(key))
        except YandexError:
            out[key] = None
    return out


def get_defaults(user_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute("SELECT project_defaults FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        raise YandexError("not-found")
    return normalize(row["project_defaults"])


def _clean_patch(patch: dict) -> dict:
    """Проверить присланные поля; None у поля — «не задавать»."""
    from app.workspaces import normalize_ai_access

    out: dict = {}
    if "url_template" in patch:
        out["url_template"] = relative_template(patch["url_template"])
    if "model_class" in patch:
        out["model_class"] = _clean_model_class(patch["model_class"])
    if "ai_access" in patch:
        out["ai_access"] = normalize_ai_access(patch["ai_access"]) if isinstance(patch["ai_access"], dict) else None
    if "kpi" in patch:
        out["kpi"] = _clean_kpi(patch["kpi"])
    if "budget_protection" in patch:
        out["budget_protection"] = _clean_protection(patch["budget_protection"])
    return out


def set_defaults(user_id: int, patch: dict) -> dict:
    """Меняет только присланные поля."""
    clean = _clean_patch({k: v for k, v in (patch or {}).items() if k in DEFAULT_KINDS})
    with pool.connection() as conn:
        row = conn.execute("SELECT project_defaults FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        if row is None:
            raise YandexError("not-found")
        merged = {**normalize(row["project_defaults"]), **clean}
        conn.execute("UPDATE users SET project_defaults = %s WHERE id = %s", (Jsonb(merged), user_id))
    return normalize(merged)


def is_set(defaults: dict, kind: str) -> bool:
    value = defaults.get(kind)
    return bool(value)


# --- Значения проекта ------------------------------------------------------------------------


def _protection_of(user_id: int, workspace_id: int) -> dict:
    """Правила «Защиты бюджета» проекта: {key: {enabled, threshold_kind, value}} — только созданные."""
    from app.rules_templates import _template_rules

    out = {}
    for (_, key), rule in _template_rules(user_id, [workspace_id]).items():
        out[key] = {
            "enabled": bool(rule["enabled"]),
            "threshold_kind": rule["threshold_kind"] or "rub",
            "value": float(rule["threshold_value"]),
        }
    return out


def project_value(user_id: int, ws: dict, kind: str):
    """Значение настройки проекта в виде настройки по умолчанию (ws — строка require_workspace)."""
    from app.workspaces import normalize_ai_access, normalize_ai_style

    if kind == "url_template":
        try:
            return relative_template(ws.get("url_template") or "")
        except YandexError:
            return ""
    if kind == "model_class":
        return normalize_ai_style(ws.get("ai_style"))["model_class"]
    if kind == "ai_access":
        return normalize_ai_access(ws.get("ai_access"))
    if kind == "kpi":
        if not ws.get("kpi_type"):
            return None
        target = ws.get("kpi_target")
        return {"kpi_type": ws["kpi_type"], "kpi_target": float(target) if target is not None else None}
    if kind == "budget_protection":
        prot = _protection_of(user_id, int(ws["id"]))
        return prot if any(p["enabled"] for p in prot.values()) else None
    raise YandexError("not-found")


def save_as_defaults(user_id: int, workspace_id: int, kinds: list[str]) -> dict:
    """«Сделать так по умолчанию»: текущие значения проекта — в настройки для новых проектов."""
    from app.workspaces import require_workspace

    ws = require_workspace(user_id, workspace_id, allow_locked=True)
    wanted = [k for k in kinds if k in DEFAULT_KINDS]
    if not wanted:
        raise YandexError("kinds", "Выберите, что сделать по умолчанию")
    return set_defaults(user_id, {k: project_value(user_id, ws, k) for k in wanted})


# --- Применение к новому проекту ---------------------------------------------------------------


def _apply_protection(user_id: int, workspace_id: int, wanted: dict, *, disable_missing: bool = False) -> int:
    """Включить проверки «Защиты бюджета» как в wanted. Порог в ценах заявки без цены — в рублях по умолчанию."""
    from app import plans
    from app.rules_templates import TEMPLATES, set_protection

    allowed = plans.has_feature(user_id, "autopilot")
    have = _protection_of(user_id, workspace_id) if disable_missing else {}
    changed = 0
    for tpl in TEMPLATES:
        key = tpl["key"]
        item = wanted.get(key)
        if item is None:
            if disable_missing and have.get(key, {}).get("enabled"):
                set_protection(user_id, workspace_id, key, enabled=False)
                changed += 1
            continue
        if item.get("enabled") and not allowed:
            continue
        kind, value = item.get("threshold_kind"), item.get("value")
        try:
            if tpl["money"] and value is not None:
                set_protection(user_id, workspace_id, key, enabled=bool(item["enabled"]), threshold_kind=kind, value=value)
            else:
                set_protection(user_id, workspace_id, key, enabled=bool(item["enabled"]))
        except YandexError as exc:
            if exc.code != "no-target-cpa":
                raise
            # Цены заявки у проекта нет — порог в рублях по умолчанию (как в предпросмотре), а не старый порог правила.
            set_protection(
                user_id, workspace_id, key, enabled=bool(item["enabled"]), threshold_kind="rub", value=tpl["default_rub"]
            )
        changed += 1
    return changed


def apply_to_new(user_id: int, workspace_ids: list[int]) -> None:
    """Настройки «Для новых проектов» — новым проектам. Сбой одной настройки не мешает остальным."""
    if not workspace_ids:
        return
    try:
        defaults = get_defaults(user_id)
    except Exception:  # noqa: BLE001
        log.exception("Настройки для новых проектов пользователя %s", user_id)
        return
    if not any(is_set(defaults, k) for k in DEFAULT_KINDS):
        return
    for ws_id in workspace_ids:
        _apply_one(user_id, int(ws_id), defaults)


def _apply_one(user_id: int, workspace_id: int, defaults: dict) -> None:
    from app.workspaces import require_workspace, update_workspace

    def run(label: str, fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("Настройка «%s» для нового проекта %s", label, workspace_id)

    try:
        ws = require_workspace(user_id, workspace_id)
    except YandexError:
        # Проект сверх тарифа (под замком) — настраивать нечего.
        return
    site = ws.get("site") or ws.get("counter_site") or ""
    if defaults["url_template"]:
        full = template_for_site(defaults["url_template"], site)
        if full:
            run("url_template", lambda: update_workspace(user_id, workspace_id, url_template=full))
    if defaults["model_class"]:
        run("model_class", lambda: update_workspace(user_id, workspace_id, ai_style={"model_class": defaults["model_class"]}))
    if defaults["ai_access"]:
        run("ai_access", lambda: update_workspace(user_id, workspace_id, ai_access=defaults["ai_access"]))
    if defaults["kpi"]:
        kpi = defaults["kpi"]
        fields = {"kpi_type": kpi["kpi_type"]}
        if kpi.get("kpi_target") is not None:
            fields["kpi_target"] = kpi["kpi_target"]
        run("kpi", lambda: update_workspace(user_id, workspace_id, **fields))
    if defaults["budget_protection"]:
        run("budget_protection", lambda: _apply_protection(user_id, workspace_id, defaults["budget_protection"]))


# --- «Взять из проекта…» ---------------------------------------------------------------------


def _rub(value: float | Decimal) -> str:
    return f"{int(round(float(value))):,}".replace(",", " ") + " ₽"


def _num(value: float) -> str:
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def kpi_text(kpi: dict | None) -> str:
    if not kpi or not kpi.get("kpi_type"):
        return "Не задан"
    target = kpi.get("kpi_target")
    if kpi["kpi_type"] == "cpa":
        return "Цена заявки" + (f" {_rub(target)}" if target is not None else "")
    return "Доля расходов на рекламу" + (f" {_num(target)} %" if target is not None else "")


def access_text(access: dict) -> str:
    from app.workspaces import AI_CATEGORIES

    if access.get("full_access"):
        return "Полный доступ"
    allowed = [label.lower() for key, label in AI_CATEGORIES if access.get("categories", {}).get(key)]
    if not allowed:
        return "Всё — после вашего «Выполнить»"
    return f"Без согласования: {', '.join(allowed)}; предел {access.get('max_change_pct', 30)} %"


def style_text(style: dict, own: bool) -> str:
    tone = TONE_LABELS.get(style.get("tone") if own else "", "Как в аккаунте")
    return f"{tone}, {ADDRESS_LABELS.get(style.get('address'), 'на «вы»')}"


def model_text(model_class: str) -> str:
    from app import ai

    if not model_class:
        return "По умолчанию"
    try:
        names = ai.class_names()
    except Exception:  # noqa: BLE001
        names = {}
    return names.get(model_class) or "По умолчанию"


def protection_text(prot: dict | None, cpa: Decimal | None) -> str:
    from app.rules_templates import TEMPLATES, threshold_text

    titles = []
    for tpl in TEMPLATES:
        item = (prot or {}).get(tpl["key"])
        if not item or not item.get("enabled"):
            continue
        if tpl["money"]:
            kind = item.get("threshold_kind") or "rub"
            value = Decimal(str(item.get("value") if item.get("value") is not None else tpl["default_rub"]))
            if kind == "cpa_x" and cpa is None:
                kind, value = "rub", tpl["default_rub"]
            titles.append(tpl["title"].format(x=threshold_text(tpl, kind, value, cpa)))
        else:
            titles.append(tpl["title"])
    return "; ".join(titles) if titles else "Выключена"


def _model_ok(model_class: str) -> bool:
    try:
        _clean_model_class(model_class)
    except Exception:  # noqa: BLE001
        return False
    return True


def _short(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    if not text:
        return "Пусто"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _facts_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} ответ"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} ответа"
    return f"{n} ответов"


def _pair(user_id: int, target_id: int, source_id: int) -> tuple[dict, dict]:
    from app.workspaces import require_workspace

    if int(target_id) == int(source_id):
        raise YandexError("same-project", "Выберите другой проект")
    target = require_workspace(user_id, target_id)
    source = require_workspace(user_id, source_id, allow_locked=True)
    return target, source


def _merged_facts(target: dict, source: dict) -> tuple[list[dict], int]:
    """Свои ответы проекта + новые из source, сколько поместится. Возвращает список и сколько не поместилось.

    Свои ответы проекта не вытесняются: память ограничена MAX_AI_FACTS, лишние ответы source не переносятся.
    """
    from app.workspaces import MAX_AI_FACTS, normalize_ai_facts

    have = normalize_ai_facts(target.get("ai_facts"))
    keys = {f["key"] for f in have}
    added = [f for f in normalize_ai_facts(source.get("ai_facts")) if f["key"] not in keys]
    room = max(0, MAX_AI_FACTS - len(have))
    return have + added[:room], max(0, len(added) - room)


def _kpi_fields(kpi: dict | None) -> dict:
    """Поля проекта после переноса KPI из другого проекта."""
    return {"kpi_type": kpi["kpi_type"] if kpi else "", "kpi_target": kpi.get("kpi_target") if kpi else None}


def _plan(user_id: int, target: dict, source: dict) -> list[dict]:
    """По каждому полю: было, станет, совпадает ли и можно ли перенести."""
    from app import rules
    from app.workspaces import MAX_AI_FACTS, normalize_ai_access, normalize_ai_facts, normalize_ai_style, tone_own

    cpa = rules.target_cpa_of(target)
    target_site = target.get("site") or target.get("counter_site") or ""
    items = []
    for kind, label in COPY_KINDS:
        item = {"kind": kind, "label": label, "available": True, "note": ""}
        if kind == "url_template":
            before = target.get("url_template") or ""
            rel = project_value(user_id, source, "url_template")
            after = template_for_site(rel, target_site) if rel else ""
            if rel and not after:
                item["available"] = False
                item["note"] = "У проекта не указан сайт — шаблон не на что перенести"
            item.update(before=before or "Не задан", after=after or "Не задан", same=before == after)
        elif kind == "ai_style":
            b_style, a_style = normalize_ai_style(target.get("ai_style")), normalize_ai_style(source.get("ai_style"))
            b_own, a_own = tone_own(target.get("ai_style")), tone_own(source.get("ai_style"))
            before, after = style_text(b_style, b_own), style_text(a_style, a_own)
            item.update(before=before, after=after, same=before == after)
        elif kind == "model_class":
            b = normalize_ai_style(target.get("ai_style"))["model_class"]
            a = normalize_ai_style(source.get("ai_style"))["model_class"]
            item.update(before=model_text(b), after=model_text(a), same=b == a)
            if a and a != b and not _model_ok(a):
                item["available"] = False
                item["note"] = "Эта модель сейчас недоступна"
        elif kind == "ai_access":
            b, a = normalize_ai_access(target.get("ai_access")), normalize_ai_access(source.get("ai_access"))
            item.update(before=access_text(b), after=access_text(a), same=b == a)
        elif kind == "kpi":
            b, a = project_value(user_id, target, "kpi"), project_value(user_id, source, "kpi")
            item.update(before=kpi_text(b), after=kpi_text(a), same=b == a)
        elif kind == "budget_protection":
            b = _protection_of(user_id, int(target["id"]))
            a = _protection_of(user_id, int(source["id"]))
            # Порог «в ценах заявки» считается от цены заявки этого проекта (после переноса KPI она может стать другой).
            before, after = protection_text(b, cpa), protection_text(a, cpa)
            item.update(before=before, after=after, same=before == after)
            kpi_cpa = rules.target_cpa_of({**target, **_kpi_fields(project_value(user_id, source, "kpi"))})
            if kpi_cpa != cpa:
                with_kpi = protection_text(a, kpi_cpa)
                if with_kpi != after:
                    item["note"] = f"Если перенести и KPI: {with_kpi}"
        elif kind == "ai_notes":
            b, a = (target.get("ai_notes") or "").strip(), (source.get("ai_notes") or "").strip()
            item.update(before=_short(b), after=_short(a), same=b == a)
        elif kind == "ai_facts":
            b = normalize_ai_facts(target.get("ai_facts"))
            merged, left_out = _merged_facts(target, source)
            added = len(merged) - len(b)
            after = _facts_word(len(merged)) + (f" (добавится {added})" if added > 0 else "")
            item.update(before=_facts_word(len(b)), after=after, same=added <= 0 and not left_out)
            if left_out:
                item["note"] = f"Не поместятся: {_facts_word(left_out)} — память ИИ вмещает {MAX_AI_FACTS}"
                if added <= 0:
                    item["available"] = False
        items.append(item)
    return items


def copy_preview(user_id: int, target_id: int, source_id: int) -> dict:
    target, source = _pair(user_id, target_id, source_id)
    return {
        "source": {"id": int(source["id"]), "name": source["name"]},
        "items": _plan(user_id, target, source),
    }


def copy_apply(user_id: int, target_id: int, source_id: int, kinds: list[str]) -> dict:
    """Перенести выбранные настройки из source в target. Возвращает проект и что перенесено."""
    from app import plans
    from app.workspaces import (
        _edit_facts,
        get_workspace,
        normalize_ai_access,
        normalize_ai_style,
        tone_own,
        update_workspace,
    )

    target, source = _pair(user_id, target_id, source_id)
    wanted = [k for k, _ in COPY_KINDS if k in set(kinds or [])]
    if not wanted:
        raise YandexError("kinds", "Отметьте, что перенести")
    plan = {i["kind"]: i for i in _plan(user_id, target, source)}
    applied, skipped = [], []
    fields: dict = {}
    for kind in wanted:
        if not plan[kind]["available"]:
            skipped.append({"kind": kind, "label": COPY_LABELS[kind], "reason": plan[kind]["note"]})
            continue
        if kind == "url_template":
            rel = project_value(user_id, source, "url_template")
            fields["url_template"] = template_for_site(rel, target.get("site") or target.get("counter_site") or "")
        elif kind == "ai_style":
            style = normalize_ai_style(source.get("ai_style"))
            patch = {"address": style["address"], "tone": style["tone"] if tone_own(source.get("ai_style")) else ""}
            fields["ai_style"] = {**fields.get("ai_style", {}), **patch}
        elif kind == "model_class":
            model = normalize_ai_style(source.get("ai_style"))["model_class"]
            fields["ai_style"] = {**fields.get("ai_style", {}), "model_class": model}
        elif kind == "ai_access":
            fields["ai_access"] = copy.deepcopy(normalize_ai_access(source.get("ai_access")))
        elif kind == "kpi":
            fields.update(_kpi_fields(project_value(user_id, source, "kpi")))
        elif kind == "ai_notes":
            fields["ai_notes"] = source.get("ai_notes") or ""
        applied.append(kind)
    # Тон «как в аккаунте» при переносе только модели не трогаем: шлём лишь модель.
    if fields:
        update_workspace(user_id, int(target["id"]), **fields)
    if "ai_facts" in wanted and plan["ai_facts"]["available"]:
        source_facts = source.get("ai_facts")

        def merge(have: list[dict]) -> list[dict]:
            return _merged_facts({"ai_facts": have}, {"ai_facts": source_facts})[0]

        _edit_facts(user_id, int(target["id"]), merge)
        applied.append("ai_facts")
    if "budget_protection" in wanted:
        wanted_prot = _protection_of(user_id, int(source["id"]))
        if any(p["enabled"] for p in wanted_prot.values()) and not plans.has_feature(user_id, "autopilot"):
            skipped.append(
                {"kind": "budget_protection", "label": COPY_LABELS["budget_protection"], "reason": "Защита бюджета — на тарифе выше"}
            )
        else:
            _apply_protection(user_id, int(target["id"]), wanted_prot, disable_missing=True)
            applied.append("budget_protection")
    order = [k for k, _ in COPY_KINDS]
    applied.sort(key=order.index)
    return {
        "workspace": get_workspace(user_id, int(target["id"])),
        "applied": [{"kind": k, "label": COPY_LABELS[k]} for k in applied],
        "skipped": skipped,
    }


def defaults_meta() -> dict:
    """Что нужно форме «Для новых проектов»: категории доступа ИИ и проверки «Защиты бюджета»."""
    from app.rules_templates import TEMPLATES
    from app.workspaces import AI_CATEGORIES, DEFAULT_AI_ACCESS

    return {
        "ai_categories": [{"key": k, "label": v} for k, v in AI_CATEGORIES],
        "default_ai_access": copy.deepcopy(DEFAULT_AI_ACCESS),
        "protection": [
            {
                "key": t["key"],
                # Порог подставится свой — в форме вместо {x} «N цен заявки» или рубли.
                "title": t["title"].replace(" {x}", "").replace("{x}", ""),
                "caption": t["caption"],
                "money": t["money"],
                "default_x": float(t.get("default_x") or 0) or None,
                "default_rub": float(t.get("default_rub") or 0) or None,
                "default_on": t["default_on"],
            }
            for t in TEMPLATES
        ],
    }
