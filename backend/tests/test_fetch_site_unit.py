"""fetch_site и DNS rebinding: подключаемся ровно к проверенному адресу, а не к тому, что DNS ответит потом.

Сеть не трогаем: DNS подменён, «сайт» — локальный сокет, к которому уводим подключение к проверенному IP.
"""

import socket
import threading

import pytest

from app import brief
from app.yandex import YandexError

PUBLIC = "93.184.216.34"
BODY = "<html><head><title>Окна</title></head><body><p>Окна от 9 900 ₽</p></body></html>".encode()


def _answers(monkeypatch, *ips):
    """DNS по очереди отвечает адресами ips (последний — и дальше); возвращает список запросов."""
    asked: list[str] = []
    real = socket.getaddrinfo

    def fake(host, port, *a, **k):
        if not str(host).endswith(".example"):
            return real(host, port, *a, **k)
        ip = ips[min(len(asked), len(ips) - 1)]
        asked.append(host)
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(brief.socket, "getaddrinfo", fake)
    return asked


def _site(monkeypatch, response: bytes):
    """Локальный «сайт» на один запрос; все подключения уводим к нему и запоминаем, куда хотели подключиться."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    seen = {"addresses": [], "request": ""}

    def connect(address, timeout=None, *a, **k):
        # Без create_connection и DNS: наружу тест не ходит.
        seen["addresses"].append(tuple(address[:2]))
        sock = socket.socket()
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", port))
        return sock

    def serve():
        conn, _ = server.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            seen["request"] = data.decode("latin-1")
            conn.sendall(response)
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(socket, "create_connection", connect)
    return seen, thread


def _ok(body: bytes = BODY) -> bytes:
    head = (
        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + body


def test_connects_to_checked_address(monkeypatch):
    # Прокси из окружения не перехватывает запрос: имя резолвим и проверяем сами.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    _answers(monkeypatch, PUBLIC)
    seen, thread = _site(monkeypatch, _ok())
    url, html = brief.fetch_site("http://okna.example/")
    thread.join(5)
    assert url == "http://okna.example/" and "от 9 900 ₽" in html
    assert seen["addresses"] == [(PUBLIC, 80)]
    # Сайт получает своё имя в Host, хотя подключались по IP.
    assert "host: okna.example" in seen["request"].lower()


def test_dns_rebinding_blocked(monkeypatch):
    # Проверке DNS отвечает публичным адресом, подключению — внутренним: подключения нет вовсе.
    asked = _answers(monkeypatch, PUBLIC, "127.0.0.1")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("подключаться нельзя"))
    with pytest.raises(YandexError) as err:
        brief.fetch_site("http://rebind.example/")
    assert err.value.message == "Адрес сайта указывает во внутреннюю сеть"
    assert len(asked) == 2
    # Для ИИ — простыми словами.
    with pytest.raises(YandexError) as err:
        brief.read_page("http://rebind.example/")
    assert err.value.message == "Сайт не открылся: этот адрес закрыт для чтения"


def test_redirect_hop_checked_again(monkeypatch):
    # Первый сайт публичный и перенаправляет на имя, которое при подключении ведёт в облачные метаданные.
    _answers(monkeypatch, PUBLIC, PUBLIC, PUBLIC, "169.254.169.254")
    redirect = b"HTTP/1.1 302 Found\r\nLocation: http://meta.example/latest\r\nContent-Length: 0\r\n\r\n"
    seen, thread = _site(monkeypatch, redirect)
    with pytest.raises(YandexError) as err:
        brief.fetch_site("http://start.example/")
    thread.join(5)
    assert err.value.message == "Адрес сайта указывает во внутреннюю сеть"
    assert seen["addresses"] == [(PUBLIC, 80)]


@pytest.mark.parametrize(
    "ip, public",
    [
        ("93.184.216.34", True),
        ("2a00:1450:4010:c05::64", True),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("::ffff:127.0.0.1", False),
        ("64:ff9b::7f00:1", False),  # NAT64 с 127.0.0.1 внутри
        ("64:ff9b::5db8:d822", True),  # NAT64 с публичным адресом
        ("2002:7f00:1::1", False),  # 6to4 с 127.0.0.1 внутри
        ("fe80::1%eth0", False),
        ("не адрес", False),
    ],
)
def test_is_public_ip(ip, public):
    assert brief._is_public_ip(ip) is public
