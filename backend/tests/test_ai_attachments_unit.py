"""Картинки в чате с ИИ без БД: разбор файла, требования Директа, подпись ссылки, цена картинок
и выбор модели, которая их видит; свои картинки в таблице этапа «Картинки»."""

import base64
import struct
import zlib

import pytest

from app import ai, ai_agent, ai_attachments
from app.yandex import YandexError
from tests.test_ad_images_unit import FakeContext, library  # noqa: F401 — фикстура библиотеки кабинетов


def png(width: int, height: int) -> bytes:
    """Настоящий PNG нужного размера (пустой): размер сервер читает из IHDR."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rows = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def jpeg(width: int, height: int) -> bytes:
    """Заголовок JPEG: SOI, APP0 и SOF0 с размером — больше разбору не нужно."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def test_image_size_reads_headers():
    assert ai_attachments.image_size(png(640, 480), "image/png") == (640, 480)
    assert ai_attachments.image_size(jpeg(1200, 900), "image/jpeg") == (1200, 900)
    with pytest.raises(YandexError):
        ai_attachments.image_size(b"\xff\xd8\xff" + b"0" * 40, "image/jpeg")


def test_decode_checks_type_and_size():
    raw, mime, w, h = ai_attachments.decode(data_url(png(800, 600)))
    assert (mime, w, h) == ("image/png", 800, 600) and raw.startswith(b"\x89PNG")
    assert ai_attachments.decode(data_url(jpeg(1000, 1000), "image/jpeg"))[1:] == ("image/jpeg", 1000, 1000)
    for bad, text in [
        ("data:image/gif;base64,R0lGOD", "JPEG или PNG"),
        ("просто текст", "JPEG или PNG"),
        ("data:image/png;base64,не-base64!", "прочитать"),
        (data_url(jpeg(10, 10), "image/png"), "прочитать"),  # тип не совпадает с содержимым
        (data_url(png(2400, 100)), "2000 px"),
    ]:
        with pytest.raises(YandexError) as err:
            ai_attachments.decode(bad)
        assert text in err.value.message and err.value.code == "attachment"


@pytest.mark.parametrize(
    "size, text",
    [
        ((1080, 607), ""),
        ((1920, 1080), ""),
        ((800, 600), ""),
        ((600, 800), ""),
        ((450, 450), ""),
        ((1000, 562), "16:9 для Директа — от 1080×607"),
        ((1000, 500), "2:1"),
        ((500, 1000), "1:2"),
        ((400, 400), "меньшая сторона — от 450"),
    ],
)
def test_direct_problem(size, text):
    problem = ai_attachments.direct_problem(*size)
    assert (problem == "") if not text else (text in problem)


def test_purpose_check_explains_direct_problem():
    ai_attachments._check_purpose("ai", 1000, 300)  # ИИ покажем любую
    with pytest.raises(YandexError) as err:
        ai_attachments._check_purpose("ads", 1000, 300)
    assert err.value.code == "attachment-direct" and "обрежьте" in err.value.message
    with pytest.raises(YandexError):
        ai_attachments._check_purpose("logo", 800, 600)


def test_pseudo_hash_and_signed_url():
    assert ai_attachments.pseudo_hash(12) == "tolk-12"
    assert ai_attachments.attachment_id("tolk-12") == 12
    assert ai_attachments.attachment_id("abc123") is None and ai_attachments.attachment_id("tolk-") is None
    url = ai_attachments.url(7)
    assert url.startswith("/api/web/ai/attachments/7/") and url != ai_attachments.url(8).replace("/8/", "/7/")
    # Подпись не та — картинки нет, в БД не ходим.
    assert ai_attachments.image(7, "0" * 32) is None
    assert ai_attachments.image(8, url.rsplit("/", 1)[1]) is None


def test_text_size_and_image_tokens():
    picture = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 100_000}}
    messages = [{"role": "user", "content": [{"type": "text", "text": "Что на картинке?"}, picture, picture]}]
    chars, images = ai._text_size(messages)
    assert images == 2 and chars < 200  # base64 картинки — не текст
    assert ai.estimate_tokens(messages) == pytest.approx(2 * ai.IMAGE_TOKENS, abs=100)
    cfg = {"units_in": 100, "units_out": 100}
    assert ai.units_for(cfg, 0, 0, images=2) == pytest.approx(2 * ai.IMAGE_TOKENS * 100 / 1_000_000)
    # Без usage провайдера оценка входа — вместе с картинками.
    usage = ai._usage(cfg, {}, messages, None, "Кот", [])
    assert usage["images"] == 2 and usage["estimated"] and usage["tokens_in"] >= 2 * ai.IMAGE_TOKENS
    # С usage провайдера картинки уже в prompt_tokens: число берём как есть.
    usage = ai._usage(cfg, {"usage": {"prompt_tokens": 3500, "completion_tokens": 5}}, messages, None, "Кот", [])
    assert usage["tokens_in"] == 3500 and not usage["estimated"]


def _cls(cid, model, vision):
    return {"id": cid, "name": cid, "model": model, "enabled": bool(model), "vision": vision}


def test_resolve_class_with_vision():
    classes = {
        "fast": _cls("fast", "yandexgpt-lite", False),
        "standard": _cls("standard", "yandexgpt", False),
        "thinking": _cls("thinking", "claude-opus-5-5", True),
    }
    assert ai._resolve_class("fast", "chat", classes, {})["id"] == "fast"
    # В запросе картинки — класс, который их видит, даже если выбран другой.
    assert ai._resolve_class("fast", "chat", classes, {}, vision=True)["id"] == "thinking"
    classes["thinking"]["vision"] = False
    assert ai._resolve_class("fast", "chat", classes, {}, vision=True) is None


def _own_row(image_hash, group="Окна"):
    return {"section": group, "kind": "image", "cells": ["Своя"], "image": {"hash": image_hash}}


def test_items_images_with_own_pictures(library, monkeypatch):
    seen = []

    def for_ads(user_id, ids, workspace_id):
        ids = sorted(ids)
        seen.append((user_id, ids, workspace_id))
        return {i: {"id": i} for i in ids if i != 99}

    monkeypatch.setattr(ai_attachments, "for_ads", for_ads)
    out = ai_agent._q_items(
        FakeContext(), {"stage": "images", "rows": [_own_row("tolk-5"), _own_row("h1")], "account_id": 3}
    )
    own, lib = out["rows"]
    assert own["image"]["hash"] == "tolk-5" and own["image"]["preview_url"] == ai_attachments.url(5)
    assert lib["image"]["preview_url"] == "https://img/h1"
    assert seen == [(1, [5], 2)]
    # Своих картинок в библиотеке Директа ещё нет — превью у Директа их не спрашиваем.
    assert all("tolk-5" not in hashes for _, hashes in library.calls)
    with pytest.raises(YandexError) as err:
        ai_agent._q_items(FakeContext(), {"stage": "images", "rows": [_own_row("tolk-99")], "account_id": 3})
    assert "среди приложенных" in err.value.message
