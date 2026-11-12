"""Окружение демо-стенда: временная БД, секреты стенда и путь к backend.

Импортируйте до любого `app.*`: настройки бэкенда читаются при импорте. Рабочая БД под защитой — имя базы
обязано содержать «demo» или «test», хост — только локальный.

Переменные (все необязательны):
  DEMO_DB_HOST / DEMO_DB_PORT / DEMO_DB_NAME / DEMO_DB_USER  — по умолчанию 127.0.0.1:54491, aim_demo, aim_test;
  DEMO_DIR — папка стенда (секрет сессий, токен, ids), по умолчанию C:/Users/Public/tolk_demo или /tmp/tolk_demo.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DEMO_DIR = Path(
    os.environ.get("DEMO_DIR") or ("C:/Users/Public/tolk_demo" if os.name == "nt" else "/tmp/tolk_demo")
)
DEMO_DIR.mkdir(parents=True, exist_ok=True)

DB = {
    "DB_HOST": os.environ.get("DEMO_DB_HOST", "127.0.0.1"),
    "DB_PORT": os.environ.get("DEMO_DB_PORT", "54491"),
    "DB_NAME": os.environ.get("DEMO_DB_NAME", "aim_demo"),
    "DB_USER": os.environ.get("DEMO_DB_USER", "aim_test"),
    "DB_PASSWORD": os.environ.get("DEMO_DB_PASSWORD", "aim_test"),
}
if not any(word in DB["DB_NAME"] for word in ("demo", "test")):
    sys.exit(f"Демо-стенд пишет только во временную БД: имя «{DB['DB_NAME']}» должно содержать demo или test")
if DB["DB_HOST"] not in ("127.0.0.1", "localhost", "::1"):
    sys.exit(f"Демо-стенд работает только с локальной БД, а не с {DB['DB_HOST']}")


def _secret(name: str) -> str:
    """Случайный секрет стенда: один на папку, чтобы сид и API подписывали сессии одинаково."""
    path = DEMO_DIR / name
    if not path.is_file():
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


os.environ.update(DB)
os.environ.update(
    {
        "RUN_EMBEDDED_WORKERS": "false",
        "WEB_SESSION_SECRET": _secret("session-secret.txt"),
        "TOKEN_ENCRYPT_SECRET": _secret("token-secret.txt"),
        "PUBLIC_BASE_URL": "http://localhost:5173",
        # Пустые OAuth-ключи: кнопки «Подключить» видны, но наружу стенд не ходит.
        "YANDEX_CLIENT_ID": "",
        "YANDEX_CLIENT_SECRET": "",
        "SMTP_HOST": "",
        "TELEGRAM_BOT_TOKEN": "",
    }
)
sys.path.insert(0, str(BACKEND))

TOKEN_FILE = DEMO_DIR / "token.txt"
STAND_FILE = DEMO_DIR / "stand.json"


def save_stand(data: dict) -> None:
    STAND_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
