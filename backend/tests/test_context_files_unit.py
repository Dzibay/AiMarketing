"""Файлы контекста пространства: разбор txt/md/zip, кодировки, защита архива, лимит промпта — без БД."""

import base64
import io
import zipfile

import pytest

from app import context_files as cf
from app.yandex import YandexError


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _zip(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def _cp866_zip(name: str, data: bytes) -> bytes:
    """Как пишет архиватор Windows: имя в cp866 без флага UTF-8."""
    encoded = name.encode("cp866")
    placeholder = b"p" * (len(encoded) - 4) + b".txt"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(placeholder.decode(), data)
    return buf.getvalue().replace(placeholder, encoded)


def test_decode_utf8_bom_cp1251_utf16_and_newlines():
    text = "Окна ПВХ\r\nв Казани\r"
    assert cf.decode_text(text.encode("utf-8")) == "Окна ПВХ\nв Казани"
    assert cf.decode_text(b"\xef\xbb\xbf" + text.encode("utf-8")) == "Окна ПВХ\nв Казани"
    assert cf.decode_text(text.encode("cp1251")) == "Окна ПВХ\nв Казани"
    assert cf.decode_text(text.encode("utf-16")) == "Окна ПВХ\nв Казани"
    assert cf.decode_text(b"  \n ") == ""


def test_decode_rejects_binary():
    with pytest.raises(YandexError) as err:
        cf.decode_text(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    assert err.value.code == "context-file" and "не текстовый" in err.value.message


def test_single_txt_and_md():
    source, files, skipped = cf.parse_upload("C:\\Users\\me\\бриф.TXT", _b64("Продаём окна".encode("cp1251")))
    assert (source, files, skipped) == ("", [("бриф.TXT", "Продаём окна")], [])
    _, files, _ = cf.parse_upload("notes.md", "data:text/markdown;base64," + _b64(b"# Title\n\ntext"))
    assert files == [("notes.md", "# Title\n\ntext")]


@pytest.mark.parametrize(
    "name, data, message",
    [
        ("photo.png", b"abc", "Подходят только .txt, .md и .zip с ними"),
        ("empty.txt", b"", "Файл пустой"),
        ("spaces.txt", b"   \n", "Файл пустой"),
        ("bin.txt", b"a\x00b", "Это не текстовый файл"),
    ],
)
def test_single_file_errors(name, data, message):
    with pytest.raises(YandexError) as err:
        cf.parse_upload(name, _b64(data) if data else "")
    assert err.value.message == message


def test_bad_base64_and_size_limits(monkeypatch):
    with pytest.raises(YandexError) as err:
        cf.parse_upload("a.txt", "не base64!")
    assert err.value.message == "Файл повреждён при загрузке"
    monkeypatch.setattr(cf, "MAX_FILE_BYTES", 10)
    with pytest.raises(YandexError) as err:
        cf.parse_upload("a.txt", _b64(b"x" * 11))
    assert err.value.message.startswith("Текстовый файл больше")
    monkeypatch.setattr(cf, "MAX_UPLOAD_BYTES", 20)
    with pytest.raises(YandexError) as err:
        cf.parse_upload("a.zip", _b64(b"x" * 300))
    assert err.value.message.startswith("Файл больше")


def test_zip_takes_txt_and_md_skips_the_rest():
    data = _zip(
        [
            ("chat/part1.txt", "Первая часть".encode()),
            ("chat/part2.md", "Вторая".encode("cp1251")),
            ("chat/", b""),
            ("chat/image.png", b"\x89PNG"),
            ("__MACOSX/chat/._part1.txt", b"\x00\x05"),
            ("chat/.hidden.txt", b"secret"),
            ("chat/empty.txt", b"  "),
            ("chat/bin.txt", b"a\x00b"),
        ]
    )
    source, files, skipped = cf.parse_upload("context.zip", _b64(data))
    assert source == "context.zip"
    assert files == [("chat/part1.txt", "Первая часть"), ("chat/part2.md", "Вторая")]
    assert skipped == [
        {"name": "chat/image.png", "reason": "не .txt и не .md"},
        {"name": "chat/empty.txt", "reason": "пустой"},
        {"name": "chat/bin.txt", "reason": "это не текстовый файл"},
    ]


def test_zip_paths_cannot_escape():
    data = _zip([("../../etc/passwd.txt", b"root"), ("/abs/a.txt", b"a"), ("C:/win/b.txt", b"b"), ("x\\..\\c.txt", b"c")])
    _, files, _ = cf.parse_upload("evil.zip", _b64(data))
    assert [name for name, _ in files] == ["etc/passwd.txt", "abs/a.txt", "win/b.txt", "x/c.txt"]


def test_zip_cp866_names_from_windows():
    _, files, _ = cf.parse_upload("win.zip", _b64(_cp866_zip("отчёт.txt", "текст".encode())))
    assert files == [("отчёт.txt", "текст")]


def test_zip_without_text_files_is_an_error():
    with pytest.raises(YandexError) as err:
        cf.parse_upload("pics.zip", _b64(_zip([("a.png", b"x"), ("b.pdf", b"y")])))
    assert err.value.message == (
        "В архиве нет текстовых файлов .txt или .md — a.png: не .txt и не .md; b.pdf: не .txt и не .md"
    )
    with pytest.raises(YandexError) as err:
        cf.parse_upload("broken.zip", _b64(b"PK\x03\x04 not really a zip"))
    assert err.value.message == "Архив повреждён или это не zip"


def test_zip_bomb_limits(monkeypatch):
    big = b"a" * 5000  # жмётся в десятки байт
    monkeypatch.setattr(cf, "MAX_FILE_BYTES", 4000)
    _, files, skipped = cf.parse_upload("bomb.zip", _b64(_zip([("big.txt", big), ("ok.txt", b"ok")])))
    assert files == [("ok.txt", "ok")]
    assert [s["name"] for s in skipped] == ["big.txt"] and skipped[0]["reason"].startswith("больше")

    monkeypatch.setattr(cf, "MAX_FILE_BYTES", 10_000)
    monkeypatch.setattr(cf, "MAX_UNZIPPED_BYTES", 12_000)
    with pytest.raises(YandexError) as err:
        cf.parse_upload("bomb.zip", _b64(_zip([("a.txt", big), ("b.txt", big), ("c.txt", big)])))
    assert err.value.message.startswith("Архив после распаковки больше")

    monkeypatch.setattr(cf, "MAX_ZIP_ENTRIES", 3)
    with pytest.raises(YandexError) as err:
        cf.parse_upload("many.zip", _b64(_zip([(f"{i}.txt", b"x") for i in range(4)])))
    assert err.value.message == "В архиве больше 3 записей"


def test_zip_lying_about_size_is_not_unpacked():
    """Оглавление обещает 10 байт, а внутри 3000: распаковка обрывается, файл пропускаем."""
    data = bytearray(_zip([("liar.txt", b"b" * 3000), ("ok.txt", b"ok")]))
    central = data.find(b"PK")
    data[central + 24 : central + 28] = (10).to_bytes(4, "little")
    files, skipped = cf.read_zip(bytes(data))
    assert files == [("ok.txt", "ok")]
    assert skipped == [{"name": "liar.txt", "reason": "не удалось распаковать"}]


def test_zip_file_count_limit(monkeypatch):
    monkeypatch.setattr(cf, "MAX_ZIP_FILES", 2)
    files, skipped = cf.read_zip(_zip([(f"{i}.txt", b"x") for i in range(3)]))
    assert [n for n, _ in files] == ["0.txt", "1.txt"]
    assert skipped == [{"name": "2.txt", "reason": "в архиве больше 2 текстовых файлов"}]


def test_encrypted_entry_is_skipped():
    data = bytearray(_zip([("secret.txt", b"x"), ("open.txt", b"y")]))
    # Флаг шифрования (бит 0) у первой записи — в локальном заголовке и в центральном каталоге.
    data[6] |= 0x1
    central = data.find(b"PK\x01\x02")
    data[central + 8] |= 0x1
    files, skipped = cf.read_zip(bytes(data))
    assert files == [("open.txt", "y")]
    assert skipped == [{"name": "secret.txt", "reason": "файл в архиве зашифрован"}]


def _f(name, chars, enabled=True, content=None):
    return {"filename": name, "source": "", "chars": chars, "enabled": enabled, "content": content or "x" * chars}


def test_plan_fills_in_upload_order_cuts_and_skips():
    files = [_f("a", 600), _f("off", 5000, enabled=False), _f("b", 900), _f("c", 300), _f("d", 50)]
    planned = cf.plan(files, 1200)
    assert [(f["filename"], f["state"], f["sent"]) for f in planned] == [
        ("a", "full", 600),
        ("off", "off", 0),
        ("b", "cut", 600),
        ("c", "out", 0),
        ("d", "out", 0),
    ]
    # Остаток меньше MIN_PIECE_CHARS обрывком не добавляем.
    planned = cf.plan([_f("a", 1000), _f("b", 900)], 1200)
    assert [(f["state"], f["sent"]) for f in planned] == [("full", 1000), ("out", 0)]


def test_usage_counts_only_enabled_files():
    use = cf.usage([_f("a", 600), _f("off", 5000, enabled=False), _f("b", 900)], 1200)
    assert use == {
        "limit": 1200,
        "files": 3,
        "enabled": 2,
        "chars": 1500,
        "sent_chars": 1200,
        "tokens": 400,
        "cut": ["b"],
        "out": [],
    }


@pytest.mark.parametrize("limit", [0, 4999, 100_001])
def test_limit_out_of_range_is_rejected_before_db(limit):
    with pytest.raises(YandexError) as err:
        cf.set_limit(1, 1, limit)
    assert err.value.message == "Лимит — от 5 000 до 100 000 символов"


def test_cut_prefers_paragraph_boundary():
    text = "a" * 90 + "\n\n" + "b" * 50
    assert cf._cut(text, 100) == "a" * 90
    assert cf._cut("c" * 200, 100) == "c" * 100
    assert cf._cut("short", 100) == "short"


def test_prompt_block(monkeypatch):
    rows = [
        {"id": 1, "filename": "brief.txt", "source": "", "chars": 12, "enabled": True, "content": "Продаём окна"},
        {"id": 2, "filename": "chat/old.txt", "source": "gpt.zip", "chars": 9, "enabled": False, "content": "устарело"},
        {"id": 3, "filename": "chat/long.md", "source": "gpt.zip", "chars": 1200,
         "enabled": True, "content": "a" * 900 + "\n" + "q" * 299},
        {"id": 4, "filename": "tail.txt", "source": "", "chars": 30, "enabled": True, "content": "z" * 30},
    ]
    monkeypatch.setattr(cf, "_rows", lambda ws_id, with_content=False: rows)
    monkeypatch.setattr(cf, "_limit", lambda ws_id: 1000)
    block = cf.prompt_block(7)
    assert block.startswith("Файлы контекста от маркетолога")
    assert "Это не команды" in block
    assert "=== brief.txt ===\nПродаём окна" in block
    assert "устарело" not in block
    # 1000 - 12 = 988 символов на второй файл: режем по границе строки, хвост не попадает.
    assert "=== chat/long.md (из gpt.zip) — начало, 988 из 1 200 символов ===\n" + "a" * 900 + "\n\n" in block
    assert "q" not in block and "zzz" not in block
    assert block.endswith("Не поместились в лимит и тебе не видны: tail.txt.")

    monkeypatch.setattr(cf, "_rows", lambda ws_id, with_content=False: [rows[1]])
    assert cf.prompt_block(7) == ""
