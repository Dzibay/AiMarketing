"""Картинки в чате с ИИ: «Показать ИИ» и «Использовать в объявлениях».

Браузер уменьшает картинку до 2000 px и присылает JPEG или PNG; сервер проверяет тип, вес и размер
по заголовку файла. Картинка для объявлений проверяется по требованиям Директа сразу — ошибка
понятна до отправки. В библиотеку кабинета она уходит только при выполнении одобренного предложения
(resolve_hashes), до этого в предложениях её зовут псевдохэшем «tolk-<id>».

Ссылка на картинку подписана (HMAC от id): превью в галерее, на доске и в чате — обычный <img>,
без заголовка авторизации; угадать ссылку чужой картинки нельзя.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import struct

from psycopg.types.json import Jsonb

from app import auth
from app.db import pool
from app.yandex import YandexError

PURPOSES = ("ai", "ads")
MAX_PER_MESSAGE = 4
MAX_BYTES = 4_000_000  # после уменьшения в браузере; nginx пропускает тело до 8 МБ
MAX_SIDE = 2000
TYPES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
# Сколько загрузок без отправленного сообщения держим на пользователя: дальше — «отправьте или уберите».
MAX_UNSENT = 40
HASH_PREFIX = "tolk-"
_HASH_RE = re.compile(r"tolk-(\d{1,18})")

# Требования Директа к картинке текстово-графического и комбинаторного объявления.
DIRECT_MIN_SIDE = 450
DIRECT_MAX_SIDE = 5000
DIRECT_WIDE = (1080, 607)


def _bad(message: str, code: str = "attachment") -> YandexError:
    return YandexError(code, message)


def image_size(data: bytes, mime: str) -> tuple[int, int]:
    """Ширина и высота из заголовка JPEG или PNG; не разобрать — ошибка."""
    if mime == "image/png":
        if len(data) >= 24 and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        raise _bad("Не удалось прочитать картинку")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        # SOF0–SOF15, кроме DHT (C4), JPG (C8) и DAC (CC): там высота и ширина.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    raise _bad("Не удалось прочитать картинку")


def decode(data_url: str) -> tuple[bytes, str, int, int]:
    """«data:image/jpeg;base64,…» → байты, тип, ширина, высота."""
    head, _, payload = str(data_url or "").partition(",")
    mime = head.removeprefix("data:").removesuffix(";base64")
    if not head.startswith("data:") or not head.endswith(";base64") or mime not in TYPES:
        raise _bad("Приложить можно картинку JPEG или PNG")
    if len(payload) > MAX_BYTES * 4 // 3 + 8:
        raise _bad("Картинка слишком большая — до 4 МБ")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _bad("Не удалось прочитать картинку") from exc
    if not raw.startswith(TYPES[mime]):
        raise _bad("Не удалось прочитать картинку")
    if len(raw) > MAX_BYTES:
        raise _bad("Картинка слишком большая — до 4 МБ")
    width, height = image_size(raw, mime)
    if not width or not height:
        raise _bad("Не удалось прочитать картинку")
    if max(width, height) > MAX_SIDE + 8:
        raise _bad(f"Картинка больше {MAX_SIDE} px — обновите страницу")
    return raw, mime, width, height


def _ratio(width: int, height: int) -> str:
    """Пропорции словами: 4:3, 16:9 или «3,2:1»."""
    for a, b in ((1, 1), (4, 3), (3, 4), (16, 9), (9, 16), (3, 2), (2, 3), (2, 1), (1, 2)):
        if abs(width / height - a / b) < 0.02:
            return f"{a}:{b}"
    value = width / height if width >= height else height / width
    text = f"{value:.1f}".replace(".", ",").removesuffix(",0")
    return f"{text}:1" if width >= height else f"1:{text}"


def direct_problem(width: int, height: int) -> str:
    """Почему Директ не примет картинку в объявление; пусто — подходит."""
    ratio = width / height
    if abs(ratio - 16 / 9) < 0.02:
        if width >= DIRECT_WIDE[0] and height >= DIRECT_WIDE[1]:
            return ""
        return f"Широкая картинка 16:9 для Директа — от {DIRECT_WIDE[0]}×{DIRECT_WIDE[1]} px, а у этой {width}×{height}"
    if not 0.75 - 0.005 <= ratio <= 4 / 3 + 0.005:
        return (
            f"Директ принимает пропорции от 3:4 до 4:3 или 16:9, а у этой картинки {_ratio(width, height)} "
            f"({width}×{height}) — обрежьте её"
        )
    if min(width, height) < DIRECT_MIN_SIDE:
        return f"Для Директа меньшая сторона — от {DIRECT_MIN_SIDE} px, а у этой картинки {width}×{height}"
    if max(width, height) > DIRECT_MAX_SIDE:
        return f"Для Директа большая сторона — до {DIRECT_MAX_SIDE} px"
    return ""


def _check_purpose(purpose: str, width: int, height: int) -> None:
    if purpose not in PURPOSES:
        raise _bad("purpose — ai или ads", "args")
    if purpose == "ads":
        problem = direct_problem(width, height)
        if problem:
            raise _bad(problem, "attachment-direct")


def _sign(attachment_id: int) -> str:
    return hmac.new(auth._secret(), f"ai-attachment:{attachment_id}".encode(), hashlib.sha256).hexdigest()[:32]


def url(attachment_id: int) -> str:
    return f"/api/web/ai/attachments/{attachment_id}/{_sign(attachment_id)}"


def pseudo_hash(attachment_id: int) -> str:
    return f"{HASH_PREFIX}{attachment_id}"


def attachment_id(image_hash) -> int | None:
    """«tolk-12» → 12; обычный хэш Директа — None."""
    match = _HASH_RE.fullmatch(str(image_hash or ""))
    return int(match.group(1)) if match else None


def out(row: dict) -> dict:
    return {
        "id": row["id"],
        "purpose": row["purpose"],
        "name": row["name"],
        "width": row["width"],
        "height": row["height"],
        "url": url(row["id"]),
        "direct_problem": direct_problem(row["width"], row["height"]),
    }


def upload(user_id: int, data_url: str, *, purpose: str = "ai", name: str = "") -> dict:
    """Картинка из поля ввода, ещё без сообщения. → {id, purpose, name, width, height, url, direct_problem}."""
    raw, mime, width, height = decode(data_url)
    _check_purpose(purpose, width, height)
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:120] or "картинка"
    with pool.connection() as conn:
        conn.execute(
            "DELETE FROM ai_attachments WHERE user_id = %s AND message_id IS NULL AND created_at < NOW() - INTERVAL '1 day'",
            (user_id,),
        )
        unsent = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_attachments WHERE user_id = %s AND message_id IS NULL", (user_id,)
        ).fetchone()["n"]
        if unsent >= MAX_UNSENT:
            raise _bad("Слишком много неотправленных картинок — отправьте сообщение или обновите страницу", "too-many")
        row = conn.execute(
            """
            INSERT INTO ai_attachments (user_id, purpose, name, mime, width, height, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
            """,
            (user_id, purpose, name, mime, width, height, raw),
        ).fetchone()
    return out(row)


def set_purpose(user_id: int, attachment_id_: int, purpose: str) -> dict:
    """Переключатель «Показать ИИ / В объявления» — пока сообщение не отправлено."""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT * FROM ai_attachments WHERE id = %s AND user_id = %s AND message_id IS NULL FOR UPDATE",
            (attachment_id_, user_id),
        ).fetchone()
        if row is None:
            raise _bad("Картинка не найдена — приложите её заново", "not-found")
        _check_purpose(purpose, row["width"], row["height"])
        row = conn.execute(
            "UPDATE ai_attachments SET purpose = %s WHERE id = %s RETURNING *", (purpose, row["id"])
        ).fetchone()
    return out(row)


def image(attachment_id_: int, signature: str) -> tuple[bytes, str] | None:
    """Картинка по подписанной ссылке из url(); подпись не та — None."""
    if not hmac.compare_digest(str(signature or ""), _sign(attachment_id_)):
        return None
    with pool.connection() as conn:
        row = conn.execute("SELECT data, mime FROM ai_attachments WHERE id = %s", (attachment_id_,)).fetchone()
    return (bytes(row["data"]), row["mime"]) if row else None


def claim(conn, user_id: int, ids) -> list[dict]:
    """Картинки нового сообщения: свои, ещё не отправленные, до четырёх. → строки без байтов, по порядку."""
    if not ids:
        return []
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise _bad("attachments — список id картинок", "args")
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_PER_MESSAGE:
        raise _bad(f"К сообщению — до {MAX_PER_MESSAGE} картинок", "too-many")
    rows = conn.execute(
        """
        SELECT id, purpose, name, width, height FROM ai_attachments
        WHERE id = ANY(%s) AND user_id = %s AND message_id IS NULL
        FOR UPDATE
        """,
        (ids, user_id),
    ).fetchall()
    if len(rows) != len(ids):
        raise _bad("Картинка не найдена — приложите её заново", "not-found")
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids]


def link(conn, ids: list[int], *, chat_id: int, workspace_id: int | None, message_id: int) -> None:
    conn.execute(
        "UPDATE ai_attachments SET chat_id = %s, workspace_id = %s, message_id = %s WHERE id = ANY(%s)",
        (chat_id, workspace_id, message_id, ids),
    )


def meta(rows: list[dict]) -> list[dict]:
    """Что хранится в meta сообщения: показать в ленте без чтения таблицы."""
    return [{"id": r["id"], "purpose": r["purpose"], "name": r["name"], "width": r["width"], "height": r["height"]} for r in rows]


def data_urls(ids: list[int]) -> dict[int, str]:
    """Картинки для запроса к модели: id → data URL."""
    if not ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute("SELECT id, mime, data FROM ai_attachments WHERE id = ANY(%s)", (list(ids),)).fetchall()
    return {r["id"]: f"data:{r['mime']};base64,{base64.b64encode(bytes(r['data'])).decode('ascii')}" for r in rows}


def for_ads(user_id: int, ids, workspace_id: int | None) -> dict[int, dict]:
    """Картинки для объявлений, которые можно ставить в предложения этого проекта: id → строка без байтов."""
    ids = sorted({int(i) for i in ids})
    if not ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, width, height, workspace_id, direct_hashes FROM ai_attachments
            WHERE id = ANY(%s) AND user_id = %s AND purpose = 'ads' AND message_id IS NOT NULL
              AND (workspace_id IS NULL OR %s::bigint IS NULL OR workspace_id = %s)
            """,
            (ids, user_id, workspace_id, workspace_id),
        ).fetchall()
    return {r["id"]: r for r in rows}


def project_images(user_id: int, workspace_id: int | None, *, limit: int = 20) -> list[dict]:
    """Картинки для объявлений, приложенные в чатах проекта, новые первыми — для list_ad_images."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, width, height, created_at FROM ai_attachments
            WHERE user_id = %s AND purpose = 'ads' AND message_id IS NOT NULL
              AND (workspace_id = %s OR (workspace_id IS NULL AND %s::bigint IS NULL))
            ORDER BY id DESC LIMIT %s
            """,
            (user_id, workspace_id, workspace_id, limit),
        ).fetchall()
    return [
        {"hash": pseudo_hash(r["id"]), "name": r["name"], "size": f"{r['width']}×{r['height']}", "source": "приложил маркетолог"}
        for r in rows
    ]


def resolve_hashes(user_id: int, hashes, token, client_login: str, account_id: int, warnings: list[str]) -> dict[str, str]:
    """Псевдохэши «tolk-<id>» → настоящие хэши библиотеки кабинета: загружает то, чего там ещё нет.

    Зовётся только при выполнении одобренного предложения. Не загрузилась — предупреждение, хэша нет в ответе.
    """
    from app import ad_extras

    wanted = {h: attachment_id(h) for h in hashes}
    wanted = {h: i for h, i in wanted.items() if i is not None}
    if not wanted:
        return {}
    mapping: dict[str, str] = {}
    key = str(account_id)
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, name, data, direct_hashes FROM ai_attachments WHERE id = ANY(%s) AND user_id = %s AND purpose = 'ads'",
            (sorted(set(wanted.values())), user_id),
        ).fetchall()
    found = {r["id"]: r for r in rows}
    for pseudo, att_id in wanted.items():
        row = found.get(att_id)
        if row is None:
            warnings.append(f"Картинка {pseudo} не найдена — объявление без неё")
            continue
        done = (row["direct_hashes"] or {}).get(key)
        if done:
            mapping[pseudo] = done
            continue
        try:
            uploaded = ad_extras.upload_image(
                token, client_login, name=row["name"] or "Толк", data_b64=base64.b64encode(bytes(row["data"])).decode("ascii")
            )
        except YandexError as exc:
            warnings.append(f"Директ не принял картинку «{row['name']}»: {exc.message or exc.code}")
            continue
        mapping[pseudo] = uploaded["hash"]
        with pool.connection() as conn:
            conn.execute(
                "UPDATE ai_attachments SET direct_hashes = direct_hashes || %s WHERE id = %s",
                (Jsonb({key: uploaded["hash"]}), att_id),
            )
    return mapping
