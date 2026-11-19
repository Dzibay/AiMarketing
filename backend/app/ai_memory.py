"""Память ИИ в промпте: общие правила маркетолога → клиент → проект.

Один источник для всех мест, где ИИ пишет тексты для клиентов проекта (чат проекта, переписывание объявлений):
везде одни и те же правила аккаунта, тон и память клиента. При споре проект важнее клиента, клиент — аккаунта.
"""

from __future__ import annotations

from app.workspaces import AI_ADDRESS, AI_TONES, normalize_ai_facts, normalize_ai_style, tone_own

# Порядок памяти в промпте проекта и что важнее при споре.
MEMORY_ORDER_RULE = (
    "Правила и память ниже идут от общего к частному: общие правила маркетолога → клиент → этот проект. "
    "При споре правила проекта важнее правил клиента, а правила клиента — важнее общих правил маркетолога."
)
STYLE_TITLE = "Как писать объявления и тексты для клиентов проекта (настройки маркетолога):"
# Тон по умолчанию, когда ни аккаунт, ни проект его не задали.
FALLBACK_TONE = "calm"


def account_tone(prefs: dict | None) -> str:
    """Тон по умолчанию из аккаунта или '' — не задан."""
    tone = (prefs or {}).get("tone") if isinstance(prefs, dict) else ""
    return tone if tone in AI_TONES else ""


def project_tone_own(ws: dict) -> bool:
    """Свой тон у проекта. Выход get_workspace — по ai_tone_own (ai_style там уже с тоном), сырая строка — по ai_style."""
    return bool(ws["ai_tone_own"]) if "ai_tone_own" in ws else tone_own(ws.get("ai_style"))


def _client_id(ws: dict):
    return ws.get("client_id") if "client_id" in ws else ws.get("client_row_id")


def client_lines(ws: dict, project_keys: set[str]) -> list[str]:
    """«Что ИИ знает о клиенте» — общее для всех его проектов; ответ сайта с тем же key важнее и тут не повторяется."""
    if not _client_id(ws):
        return []
    notes = str(ws.get("client_ai_notes") or "").strip()
    facts = [f for f in normalize_ai_facts(ws.get("client_ai_facts")) if f["key"] not in project_keys]
    if not notes and not facts:
        return []
    name = ws.get("client_name") or ""
    out = ["", f"Что ИИ знает о клиенте{f' «{name}»' if name else ''} (общее для всех его проектов):"]
    if notes:
        out.append(notes)
    out += [f"- {f['question'] or f['key']}: {f['answer']}" for f in facts]
    return out


def style_line(ws: dict) -> str:
    """Тон и обращение проекта. Тон — только свой: унаследованный стоит выше, на уровне аккаунта, и уступает клиенту."""
    style = normalize_ai_style(ws.get("ai_style"))
    address = f"К клиентам в объявлениях обращаемся {AI_ADDRESS[style['address']]}."
    if not project_tone_own(ws):
        return address
    return f"Тон текстов для клиентов — {AI_TONES[style['tone']]}. {address}"


def memory_lines(ws: dict, prefs: dict | None, *, facts: list[dict] | None = None) -> list[str]:
    """Правила аккаунта → память клиента → ответы, стиль и заметки проекта — строками промпта.

    Тон: свой тон проекта → тон, сказанный о клиенте → тон аккаунта → спокойный. Унаследованный тон стоит в
    блоке аккаунта, до клиента, поэтому тон из «Что ИИ знает о клиенте» его перекрывает.
    """
    prefs = prefs if isinstance(prefs, dict) else {}
    facts = normalize_ai_facts(ws.get("ai_facts")) if facts is None else facts
    rules = str(prefs.get("rules") or "").strip()
    client = client_lines(ws, {f["key"] for f in facts})
    out: list[str] = []
    if rules or client:
        out += ["", MEMORY_ORDER_RULE]
    if rules:
        out += ["", "Общие правила маркетолога (для всех его проектов и чатов):", rules]
    if not project_tone_own(ws):
        tone = account_tone(prefs) or FALLBACK_TONE
        out += [
            "",
            f"Тон текстов для клиентов по умолчанию — {AI_TONES[tone]}. Если о тоне сказано у клиента или в "
            "проекте, важнее это.",
        ]
    out += client
    if facts:
        out += ["", "Что маркетолог уже ответил (учитывай, не переспрашивай):"]
        out += [f"- {f['question'] or f['key']}: {f['answer']}" for f in facts]
    out += ["", STYLE_TITLE, style_line(ws)]
    notes = str(ws.get("ai_notes") or "").strip()
    if notes:
        out += ["", "Заметки маркетолога о проекте:", notes]
    return out


def account_prefs(user_id: int) -> dict:
    """«Мои правила для ИИ» и тон аккаунта; не прочитались — пусто (текст всё равно пишем)."""
    try:
        from app.users import get_ai_prefs

        return get_ai_prefs(user_id)
    except Exception:
        return {}
