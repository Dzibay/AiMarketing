"""Справка кабинета для ИИ: те же статьи docs/help/*.md, что показывает раздел «Справка».

Статей мало, поэтому хватает поиска по словам — без индекса и векторов. Папку ищем рядом с кодом: в репозитории
это ../../docs/help, в образе бэкенда — /app/docs/help (см. backend/Dockerfile); HELP_DIR задаёт её явно.
"""

from __future__ import annotations

import logging
import math
import os
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

ARTICLE_LIMIT = 3500  # знаков текста статьи в ответ инструменту
MAX_ARTICLES = 3

# Служебные слова вопроса: по ним статью не найти, а «все слова совпали» из-за них не выполнится.
STOP_WORDS = frozenset(
    "как где что когда зачем почему какой какая какие каких можно нужно надо мне меня мой моя мои это этот эта "
    "для или при без над под про его она они там тут уже еще ещё есть нет вас ваш ваша сделать делать".split()
)


def _help_dir() -> Path | None:
    env = os.environ.get("HELP_DIR")
    here = Path(__file__).resolve()
    candidates = [Path(env)] if env else []
    candidates += [here.parents[2] / "docs" / "help", here.parents[1] / "docs" / "help"]
    for path in candidates:
        if path.is_dir():
            return path
    return None


def _norm(text: str) -> str:
    return str(text or "").lower().replace("ё", "е")


def _plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*`#>|]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    meta: dict[str, str] = {}
    body = text
    head = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if head:
        body = text[head.end():]
        for line in head.group(1).split("\n"):
            m = re.match(r"^(\w+):\s*(.*)$", line)
            if m:
                meta[m.group(1)] = m.group(2).strip()
    slug = path.stem
    return {
        "slug": slug,
        "title": meta.get("title") or slug,
        "summary": meta.get("summary") or "",
        "body": body.strip(),
        "link": f"/cabinet/help/{slug}",
        "_title": _norm(meta.get("title") or slug),
        "_text": _norm(f"{meta.get('summary') or ''} {_plain(body)}"),
    }


@lru_cache(maxsize=1)
def articles() -> tuple[dict, ...]:
    folder = _help_dir()
    if folder is None:
        log.warning("Папка справки docs/help не найдена — ИИ ответит про сервис без статей")
        return ()
    return tuple(_parse(p) for p in sorted(folder.glob("*.md")) if p.name.lower() != "readme.md")


def _words(query: str) -> list[str]:
    words = [w for w in re.split(r"[^a-zа-я0-9%]+", _norm(query)) if len(w) > 2 and w not in STOP_WORDS]
    # Короткие окончания отрезаем: «правку» найдёт и «правки», «минус-фразу» — «минус-фразы».
    return list(dict.fromkeys(w[:-1] if len(w) > 4 else w for w in words))


def search(query: str) -> dict:
    """Подходящие статьи справки для ИИ: [{title, link, text}], лучшие первыми; found=False — ничего нет."""
    items = articles()
    words = _words(query)
    if not items or not words:
        return {"found": False, "articles": [], "all_titles": [a["title"] for a in items]}
    need = max(1, math.ceil(len(words) / 2))
    scored = []
    for a in items:
        hits = [w for w in words if w in a["_title"] or w in a["_text"]]
        if len(hits) < need:
            continue
        score = len(hits) * 10 + sum(3 for w in hits if w in a["_title"]) + sum(a["_text"].count(w) for w in hits)
        scored.append((score, a))
    scored.sort(key=lambda pair: -pair[0])
    if not scored:
        return {"found": False, "articles": [], "all_titles": [a["title"] for a in items]}
    out = []
    for _, a in scored[:MAX_ARTICLES]:
        text = a["body"]
        if len(text) > ARTICLE_LIMIT:
            text = text[:ARTICLE_LIMIT].rsplit("\n", 1)[0] + "\n…"
        out.append({"title": a["title"], "link": a["link"], "text": text})
    return {"found": True, "articles": out}
