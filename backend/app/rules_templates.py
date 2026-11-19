"""Шаблоны правил автопилота и «Защита бюджета» проекта.

Шаблон — готовая связка существующих типа (RULE_TYPES) и действия (RULE_ACTIONS) правила с понятным названием.
В обычном режиме шаблоны «Защиты бюджета» видны как два-три переключателя в настройках проекта: под каждым —
обычное правило автопилота с отметкой template, так что проверка, колокольчик, Telegram и журнал (откат
остановки) — общие с правилами экспертного режима.

Порог расхода можно задать «в N раз больше целевой цены заявки» (threshold_kind = 'cpa_x'): одно и то же
правило подходит клиенту с заявкой за 300 ₽ и за 5000 ₽, цена заявки берётся при каждой проверке.
"""

from __future__ import annotations

from decimal import Decimal

from app import rules
from app.db import pool
from app.workspaces import list_workspaces, require_workspace
from app.yandex import YandexError

# Расход без заявок считается за неделю; правило проверяется не чаще раза в сутки.
LOOKBACK_DAYS = 7
COOLDOWN_HOURS = 24
# Порог «в N цен заявки» — от половины до 50 цен; в рублях — от 100 ₽.
X_MIN, X_MAX = Decimal("0.5"), Decimal("50")
RUB_MIN, RUB_MAX = Decimal("100"), Decimal("10000000")

TEMPLATES: list[dict] = [
    {
        "key": "spend_notify",
        "name": "Защита бюджета: сообщить о расходе без заявок",
        "title": "Сообщить, если потрачено {x} без заявок",
        "caption": "Сообщение придёт в колокольчик и в Telegram.",
        "rule_type": "spend_no_conv",
        "action": "notify",
        "money": True,
        "default_x": Decimal("2"),
        "default_rub": Decimal("3000"),
        "default_on": True,
    },
    {
        "key": "spend_pause",
        "name": "Защита бюджета: остановить кампанию без заявок",
        "title": "Остановить кампанию, если потрачено {x} без заявок",
        "caption": "Сервис сам остановит кампанию. Вернуть можно в «Истории».",
        "rule_type": "spend_no_conv",
        "action": "pause",
        "money": True,
        "default_x": Decimal("3"),
        "default_rub": Decimal("5000"),
        # Остановку сервис включает только по явному выбору — в «Включить у всех клиентов» её нет.
        "default_on": False,
    },
    {
        "key": "daily_budget",
        "name": "Защита бюджета: дневной бюджет на 90 %",
        "title": "Сообщить, если дневной бюджет израсходован на 90 %",
        "caption": "Проверка по расходу за вчера.",
        "rule_type": "daily_budget_pct",
        "action": "notify",
        "money": False,
        "threshold": Decimal("90"),
        "default_on": True,
    },
]
BY_KEY = {t["key"]: t for t in TEMPLATES}

# Все шаблоны собраны из существующих типов и действий правил.
assert all(t["rule_type"] in rules.RULE_TYPES and t["action"] in rules.RULE_ACTIONS for t in TEMPLATES)


def _rub_text(value: Decimal) -> str:
    return f"{int(value):,}".replace(",", " ") + " ₽"


def _x_text(value: Decimal) -> str:
    return f"{value.normalize():f}".replace(".", ",")


def price_word(value: Decimal) -> str:
    """«цена / цены / цен заявки» по правилам русского языка; у дробных — «цены» («1,5 цены заявки»)."""
    if value != value.to_integral_value():
        return "цены заявки"
    n = int(value)
    if n % 10 == 1 and n % 100 != 11:
        return "цена заявки"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "цены заявки"
    return "цен заявки"


def threshold_text(tpl: dict, kind: str, value: Decimal, cpa: Decimal | None) -> str:
    """Порог словами: «2 цены заявки (6 000 ₽)» или «3 000 ₽»."""
    if not tpl["money"]:
        return f"{int(value)} %"
    if kind == "cpa_x":
        word = price_word(value)
        base = f"{_x_text(value)} {word}"
        return f"{base} ({_rub_text(value * cpa)})" if cpa is not None else base
    return _rub_text(value)


def default_threshold(tpl: dict, cpa: Decimal | None) -> tuple[str, Decimal]:
    """Порог нового правила: в ценах заявки, если она задана, иначе в рублях."""
    if not tpl["money"]:
        return "rub", tpl["threshold"]
    return ("cpa_x", tpl["default_x"]) if cpa is not None else ("rub", tpl["default_rub"])


def clean_threshold(tpl: dict, kind: str | None, value, cpa: Decimal | None) -> tuple[str, Decimal]:
    """Проверить порог из формы. Без цены заявки порог в ценах задать нельзя — только в рублях."""
    if not tpl["money"]:
        return "rub", tpl["threshold"]
    kind = kind or "rub"
    if kind not in rules.THRESHOLD_KINDS:
        raise YandexError("rule-type")
    try:
        number = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 — любая нечисловая строка
        raise YandexError("threshold", "Укажите порог числом") from exc
    if not number.is_finite():
        # NaN и бесконечность: сравнение Decimal('NaN') с границами падает, а не отказывает.
        raise YandexError("threshold", "Укажите порог числом")
    if kind == "cpa_x":
        if cpa is None:
            raise YandexError("no-target-cpa", "Сначала задайте цену заявки в «Основном»")
        if not X_MIN <= number <= X_MAX:
            raise YandexError("threshold", "Порог — от 0,5 до 50 цен заявки")
        return kind, number.quantize(Decimal("0.1"))
    if not RUB_MIN <= number <= RUB_MAX:
        raise YandexError("threshold", "Порог — от 100 ₽")
    return kind, number.quantize(Decimal("1"))


def _template_rules(user_id: int, workspace_ids: list[int]) -> dict[tuple[int, str], dict]:
    if not workspace_ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, scope_workspace_id, template, enabled, threshold_kind, threshold_value, last_run_at
            FROM automation_rules
            WHERE user_id = %s AND template IS NOT NULL AND scope_workspace_id = ANY(%s)
            """,
            (user_id, workspace_ids),
        ).fetchall()
    return {(int(r["scope_workspace_id"]), r["template"]): r for r in rows}


def _target_cpas(user_id: int, workspace_ids: list[int]) -> dict[int, Decimal | None]:
    if not workspace_ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT w.id, w.kpi_type, w.kpi_target, cl.kpi_type AS client_kpi_type, cl.kpi_target AS client_kpi_target
            FROM workspaces w LEFT JOIN clients cl ON cl.id = w.client_row_id
            WHERE w.user_id = %s AND w.id = ANY(%s)
            """,
            (user_id, workspace_ids),
        ).fetchall()
    return {int(r["id"]): rules.target_cpa_of(r) for r in rows}


def _item(tpl: dict, rule: dict | None, cpa: Decimal | None) -> dict:
    if rule is not None:
        kind, value = rule["threshold_kind"] or "rub", Decimal(str(rule["threshold_value"]))
    else:
        kind, value = default_threshold(tpl, cpa)
    threshold = threshold_text(tpl, kind, value, cpa)
    return {
        "key": tpl["key"],
        "title": tpl["title"].format(x=threshold) if tpl["money"] else tpl["title"],
        "caption": tpl["caption"],
        "money": tpl["money"],
        "action": tpl["action"],
        "enabled": bool(rule and rule["enabled"]),
        "rule_id": int(rule["id"]) if rule else None,
        "threshold_kind": kind,
        "value": float(value),
        "threshold_text": threshold,
        # Порог в ценах заявки, а цену убрали: правило молчит, пока её не зададут снова.
        "needs_target": bool(tpl["money"] and kind == "cpa_x" and cpa is None),
        "last_run_at": rule["last_run_at"].isoformat() if rule and rule.get("last_run_at") else None,
    }


def protection_state(user_id: int, workspace_id: int) -> dict:
    """Блок «Защита бюджета» проекта: переключатели, пороги и целевая цена заявки."""
    require_workspace(user_id, workspace_id, allow_locked=True)
    cpa = _target_cpas(user_id, [workspace_id]).get(workspace_id)
    existing = _template_rules(user_id, [workspace_id])
    return {
        "workspace_id": workspace_id,
        "target_cpa_rub": float(cpa) if cpa is not None else None,
        "items": [_item(t, existing.get((workspace_id, t["key"])), cpa) for t in TEMPLATES],
    }


def set_protection(
    user_id: int,
    workspace_id: int,
    key: str,
    *,
    enabled: bool,
    threshold_kind: str | None = None,
    value=None,
) -> dict:
    """Включить, выключить или поменять порог одного переключателя. Под ним — правило автопилота проекта."""
    tpl = BY_KEY.get(key)
    if tpl is None:
        raise YandexError("not-found")
    explicit = not (threshold_kind is None and value is None)
    # Выключить можно и в заблокированном проекте; включить или сохранить порог — нет.
    require_workspace(user_id, workspace_id, allow_locked=not (enabled or explicit))
    cpa = _target_cpas(user_id, [workspace_id]).get(workspace_id)
    rule = _template_rules(user_id, [workspace_id]).get((workspace_id, key))
    if not explicit:
        kind, number = (
            (rule["threshold_kind"] or "rub", Decimal(str(rule["threshold_value"]))) if rule else default_threshold(tpl, cpa)
        )
        if kind == "cpa_x" and cpa is None and enabled:
            # Включают без цены заявки — порог в рублях по умолчанию, а не правило, которое молчит.
            kind, number = "rub", tpl.get("default_rub", number)
    else:
        kind, number = clean_threshold(tpl, threshold_kind, value, cpa)
    if rule is None:
        if not enabled and not explicit:
            # Выключить то, чего нет, — ничего не создаёт.
            return protection_state(user_id, workspace_id)
        created = _create(user_id, workspace_id, tpl, kind, number)
        if not enabled:
            # Порог сохранили до включения — правило создаётся выключенным, чтобы порог не потерялся.
            rules.update_rule(user_id, int(created["id"]), enabled=False)
    else:
        rules.update_rule(
            user_id, int(rule["id"]), enabled=enabled, dry_run=False, threshold_kind=kind, threshold_value=number
        )
    return protection_state(user_id, workspace_id)


def _create(user_id: int, workspace_id: int, tpl: dict, kind: str, number: Decimal) -> dict:
    return rules.create_rule(
        user_id,
        name=tpl["name"],
        rule_type=tpl["rule_type"],
        threshold_value=number,
        lookback_days=LOOKBACK_DAYS,
        action=tpl["action"],
        dry_run=False,
        scope_workspace_id=workspace_id,
        cooldown_hours=COOLDOWN_HOURS,
        threshold_kind=kind,
        template=tpl["key"],
    )


# --- «Включить у всех клиентов» на главной -------------------------------------------------------


def default_plan(user_id: int) -> dict:
    """Что сделает кнопка: проекты без защиты и какие проверки у каждого включатся."""
    projects = [w for w in list_workspaces(user_id) if not w["locked"]]
    ids = [int(w["id"]) for w in projects]
    existing = _template_rules(user_id, ids)
    covered = {ws_id for ws_id, _ in existing}
    cpas = _target_cpas(user_id, ids)
    items = []
    for w in projects:
        if int(w["id"]) in covered:
            continue
        cpa = cpas.get(int(w["id"]))
        checks = []
        for tpl in TEMPLATES:
            if not tpl["default_on"]:
                continue
            kind, value = default_threshold(tpl, cpa)
            text = tpl["title"].format(x=threshold_text(tpl, kind, value, cpa)) if tpl["money"] else tpl["title"]
            checks.append(text)
        items.append(
            {
                "workspace_id": int(w["id"]),
                "name": w["name"],
                "client_name": w.get("client_name") or "",
                "target_cpa_rub": float(cpa) if cpa is not None else None,
                "checks": checks,
            }
        )
    return {"items": items, "protected": len(covered), "total": len(projects)}


def apply_default(user_id: int, workspace_ids: list[int]) -> dict:
    """Включить защиту по умолчанию в выбранных проектах из default_plan (тех, что всё ещё без неё)."""
    plan = {i["workspace_id"]: i for i in default_plan(user_id)["items"]}
    wanted = [int(i) for i in workspace_ids if int(i) in plan]
    cpas = _target_cpas(user_id, wanted)
    created = 0
    for ws_id in wanted:
        for tpl in TEMPLATES:
            if not tpl["default_on"]:
                continue
            kind, value = default_threshold(tpl, cpas.get(ws_id))
            _create(user_id, ws_id, tpl, kind, value)
            created += 1
    return {"projects": len(wanted), "rules": created}
