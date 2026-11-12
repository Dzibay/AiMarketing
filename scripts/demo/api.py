"""API кабинета на временной БД стенда (порт 8000, без фоновых воркеров).

  backend/.venv/Scripts/python scripts/demo/api.py

Плюс GET /api/_demo/session — токен демо-пользователя: браузер стенда входит без копирования токена руками.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import env  # noqa: E402  — до app.*: настройки читаются при импорте

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    import uvicorn

    from app.main import app

    @app.get("/api/_demo/session", include_in_schema=False)
    def demo_session():
        return {"token": env.TOKEN_FILE.read_text(encoding="utf-8").strip()}

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("DEMO_API_PORT", "8000")), loop="none")
