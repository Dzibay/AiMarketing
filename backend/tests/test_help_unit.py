"""Справка для ИИ: поиск по статьям docs/help и инструмент help_search."""

import json

from app import ai_agent
from app import help as help_docs


def titles(query: str) -> list[str]:
    return [a["title"] for a in help_docs.search(query)["articles"]]


def test_articles_loaded_without_readme():
    slugs = {a["slug"] for a in help_docs.articles()}
    assert {"undo", "telegram", "connections", "changelog", "faq"} <= slugs
    assert "README" not in slugs and "readme" not in slugs


def test_search_finds_by_word_forms():
    assert titles("как вернуть правку?")[0] == "Как вернуть как было"
    assert titles("где привязать Telegram")[0] == "Telegram"
    assert "Тарифы и оплата" in titles("куда ввести промокод")


def test_search_gives_text_and_link():
    out = help_docs.search("привязать телеграм бота")
    assert out["found"]
    first = out["articles"][0]
    assert first["link"] == "/cabinet/help/telegram"
    assert "](/cabinet/" in first["text"]  # текст статьи со ссылками на экраны — ИИ отдаёт их пользователю
    assert len(out["articles"]) <= help_docs.MAX_ARTICLES


def test_nothing_found_lists_titles():
    out = help_docs.search("ёлка-палка трамвай")
    assert out == {"found": False, "articles": [], "all_titles": out["all_titles"]}
    assert "Telegram" in out["all_titles"]
    assert help_docs.search("как где что")["found"] is False  # одни служебные слова


def test_help_tool_registered_for_both_chats():
    assert "help_search" in ai_agent.READ_TOOLS
    assert "help_search" in ai_agent.TOOL_LABELS
    assert "help_search" in ai_agent.GENERAL_TOOL_NAMES
    assert any(t["function"]["name"] == "help_search" for t in ai_agent.GENERAL_TOOLS)


def test_help_tool_note_when_not_found():
    out = ai_agent._t_help(None, {"query": "ёлка-палка трамвай"})
    assert out["found"] is False and "поддержку" in out["note"]
    # Лента чата узнаёт «не нашлось» по этой строке в ответе инструмента — формат JSON важен.
    assert '"found": false' in ai_agent._dump(out)
    assert json.loads(ai_agent._dump(ai_agent._t_help(None, {"query": "лимит ИИ"})))["found"] is True


def test_help_rule_in_system_prompt():
    assert "help_search" in ai_agent.HELP_RULE
    assert "/cabinet/" in ai_agent.HELP_RULE
