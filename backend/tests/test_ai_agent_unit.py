"""ИИ-ассистент без БД и модели: перезапуск копией, системные правила, запомненные ответы."""

from types import SimpleNamespace

import pytest

from app import actions, ai_agent
from app.workspaces import SCHEME_FACT, fact_key, normalize_ai_access, normalize_ai_facts
from app.yandex import YandexError

SOURCE = {"id": 7, "name": "Поиск Москва", "state": "ON", "status": "ACCEPTED", "account_id": 3}


class FakeContext:
    """Context без БД: пространство из словаря и одна кампания в нём."""

    def __init__(self, ws: dict | None = None):
        self.user_id = 1
        self.ws_id = 2
        self.ws = {"name": "Окна", "counter_id": 555, "accounts": [], **(ws or {})}
        self.access = normalize_ai_access(self.ws.get("ai_access"))

    def campaign(self, campaign_row_id: int) -> dict:
        if campaign_row_id != SOURCE["id"]:
            raise YandexError("scope", f"Кампании [{campaign_row_id}] не входят в это пространство")
        return SOURCE


@pytest.fixture
def direct(monkeypatch):
    """Копия, модерация и пауза без Директа; calls — что ушло бы в Директ."""
    fake = SimpleNamespace(
        calls=[],
        copy_status="ok",
        moderation={"drafts": 2, "sent": 2, "errors": [], "status": "ok"},
        pause_error=None,
    )

    def duplicate(user_id, row_id, *, name=None, workspace_id=None, **kw):
        fake.calls.append(("copy", row_id, name, workspace_id))
        return {"campaign": {"id": 70, "name": name or f"{SOURCE['name']} (копия)"}, "groups": 1, "keywords": 5,
                "autotargeting": 0, "ads": 2, "warnings": [], "status": fake.copy_status}

    def moderate(user_id, row_id):
        fake.calls.append(("moderate", row_id))
        if isinstance(fake.moderation, Exception):
            raise fake.moderation
        return fake.moderation

    def bulk(user_id, *, campaign_ids, action, workspace_id=None, **kw):
        fake.calls.append((action, campaign_ids, workspace_id))
        if fake.pause_error:
            raise fake.pause_error
        return {"status": "ok"}

    monkeypatch.setattr(actions, "duplicate_campaign", duplicate)
    monkeypatch.setattr(actions, "moderate_campaign", moderate)
    monkeypatch.setattr(actions, "bulk_campaign_action", bulk)
    return fake


@pytest.mark.parametrize(
    "value, expected",
    [(None, True), ("", True), (True, True), (1, True), ("true", True), ("да", True),
     (False, False), (0, False), ("false", False), ("0", False), ("no", False), (" Нет ", False)],
)
def test_bool_arg(value, expected):
    assert ai_agent._bool({"flag": value}, "flag", True) is expected


def test_bool_arg_missing_uses_default():
    assert ai_agent._bool({}, "flag", False) is False


def test_restart_plan_lists_steps_and_categories():
    ctx = FakeContext()
    plan = ai_agent._p_restart(ctx, {"campaign_row_id": 7})
    assert plan.summary == (
        "Перезапуск «Поиск Москва» копией: копия «Поиск Москва (копия)» в том же кабинете → на модерацию"
        " → оригинал на паузу"
    )
    assert plan.categories == ["campaigns", "pause_resume"]

    keep = ai_agent._p_restart(ctx, {"campaign_row_id": 7, "name": "Поиск Москва 2", "pause_source": False})
    assert "копия «Поиск Москва 2»" in keep.summary
    assert keep.summary.endswith("на модерацию, оригинал работает")
    assert keep.categories == ["campaigns"]  # без паузы оригинала — категория «пауза» не нужна


def test_restart_foreign_campaign_is_refused(direct):
    with pytest.raises(YandexError):
        ai_agent._x_restart(FakeContext(), {"campaign_row_id": 8})
    assert direct.calls == []


def test_restart_pauses_source_after_copy_went_to_moderation(direct):
    out = ai_agent._x_restart(FakeContext(), {"campaign_row_id": 7})
    assert direct.calls == [("copy", 7, None, 2), ("moderate", 70), ("pause", [7], 2)]
    assert out["source_paused"] is True
    assert out["copy"] == {"campaign_row_id": 70, "name": "Поиск Москва (копия)", "status": "ok",
                           "groups": 1, "keywords": 5, "ads": 2}
    assert "note" not in out


@pytest.mark.parametrize("broken", ["partial_copy", "nothing_sent", "moderation_error"])
def test_restart_keeps_source_running_when_copy_is_not_ready(direct, broken):
    """Без готовой копии оригинал не останавливаем — иначе кампания осталась бы совсем без показов."""
    if broken == "partial_copy":
        direct.copy_status = "partial"
    elif broken == "nothing_sent":
        direct.moderation = {"drafts": 0, "sent": 0, "errors": [], "status": "ok"}
    else:
        direct.moderation = YandexError("direct", "Объявления не отправлены")
    out = ai_agent._x_restart(FakeContext(), {"campaign_row_id": 7})
    assert [call[0] for call in direct.calls] == ["copy", "moderate"]
    assert out["source_paused"] is False
    assert out["note"].startswith("Оригинал не остановлен")
    if broken == "moderation_error":
        assert out["moderation"] == {"status": "error", "error": "Объявления не отправлены"}


def test_restart_without_pause_leaves_source_alone(direct):
    out = ai_agent._x_restart(FakeContext(), {"campaign_row_id": 7, "name": " Поиск 2 ", "pause_source": "false"})
    assert direct.calls == [("copy", 7, "Поиск 2", 2), ("moderate", 70)]
    assert out["source_paused"] is False
    assert out["note"] == "Оригинал работает — остановите его, когда копия пройдёт модерацию"


def test_restart_reports_failed_pause(direct):
    direct.pause_error = YandexError("direct", "Кампания в архиве")
    out = ai_agent._x_restart(FakeContext(), {"campaign_row_id": 7})
    assert out["source_paused"] is False
    assert out["note"] == "Оригинал не остановлен: Кампания в архиве"


def test_every_tool_has_schema_and_label():
    schemas = {tool["function"]["name"] for tool in ai_agent.TOOLS}
    assert schemas == set(ai_agent.READ_TOOLS) | set(ai_agent.WRITE_TOOLS)
    assert set(ai_agent.WRITE_TOOLS) <= set(ai_agent.TOOL_LABELS)


@pytest.fixture
def no_memory(monkeypatch):
    monkeypatch.setattr(ai_agent, "memory_examples", lambda ws_id, limit: {})


def test_prompt_offers_scheme_hypothesis_until_marketer_confirms(no_memory):
    prompt = ai_agent._system_prompt(FakeContext())
    assert f"key {SCHEME_FACT}" in prompt
    assert "restart_campaign_copy" in prompt
    assert "Что маркетолог уже ответил" not in prompt

    fact = {"key": SCHEME_FACT, "question": "Как работает схема 0 ₽?", "answer": "Платим только за заказ"}
    prompt = ai_agent._system_prompt(FakeContext({"ai_facts": [fact, {"key": "", "answer": "без ключа"}]}))
    assert f"key {SCHEME_FACT}" not in prompt
    assert "Что маркетолог уже ответил (учитывай, не переспрашивай):\n- Как работает схема 0 ₽?: Платим только за заказ" in prompt
    assert "без ключа" not in prompt


def test_prompt_access_policy(no_memory):
    assert "Без согласования разрешено: ничего." in ai_agent._system_prompt(FakeContext())
    full = ai_agent._system_prompt(FakeContext({"ai_access": {"full_access": True}}))
    assert "Полный доступ: все действия выполняются сразу" in full


def test_fact_key_and_normalize_facts():
    assert fact_key("  Zero Cost  scheme ") == "zero_cost_scheme"
    assert len(fact_key("x" * 100)) == 60
    raw = [
        {"key": "Схема", "question": " Как? ", "answer": " Так ", "at": "2026-10-27T10:00:00+03:00"},
        {"key": "пусто", "answer": "  "},
        {"key": "", "answer": "без ключа"},
        "мусор",
    ]
    assert normalize_ai_facts(raw) == [
        {"key": "схема", "question": "Как?", "answer": "Так", "at": "2026-10-27T10:00:00+03:00"}
    ]
    assert normalize_ai_facts(None) == []
    assert normalize_ai_facts({"key": "x", "answer": "y"}) == []
