from datetime import date

from app.clock import today_msk
from app.settings_store import get_setting, public_base_url


def _legal_email() -> str:
    return get_setting("legal_email").strip()


def _legal_date_display() -> str:
    raw = get_setting("legal_date").strip()
    if not raw:
        return today_msk().strftime("%d.%m.%Y")
    try:
        return date.fromisoformat(raw).strftime("%d.%m.%Y")
    except ValueError:
        return raw


def legal_page_data(*, title: str, heading: str, body: str, page_path: str, footer: str) -> dict:
    email = _legal_email() or "не указана"
    revised = _legal_date_display()
    base = public_base_url()
    link = f"{base}{page_path}" if base else page_path
    meta_bits = [f"Редакция от {revised}"]
    if _legal_email():
        meta_bits.append(email)
    paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
    return {
        "title": title,
        "heading": heading,
        "meta": " · ".join(meta_bits),
        "paragraphs": paragraphs,
        "footer": footer,
        "link": link,
    }


def _fill(text: str) -> str:
    base = public_base_url() or "https://example.com"
    email = _legal_email() or "support@example.com"
    return text.replace("[URL_BASE]", base).replace("[EMAIL]", email)


_PRIVACY = """
ПОЛИТИКА КОНФИДЕНЦИАЛЬНОСТИ

1. Общие положения

1.1. Настоящая Политика регулирует обработку персональных данных пользователей сервиса AiMarketing ([URL_BASE]).

1.2. Оператор обрабатывает данные в соответствии с Федеральным законом № 152-ФЗ «О защите персональных данных».

2. Какие данные мы обрабатываем

2.1. Адрес электронной почты, имя, идентификатор аккаунта Яндекса.

2.2. OAuth-токены доступа к API Яндекс Метрики и Яндекс Директа — хранятся в зашифрованном виде и используются только для работы сервиса от вашего имени.

2.3. Технические данные: IP, журналы запросов, сведения о сеансах.

3. Цели обработки

3.1. Регистрация и вход в личный кабинет.

3.2. Подключение рекламных кабинетов и счётчиков, синхронизация статистики, автоматизация рекламы.

3.3. Связь по вопросам поддержки и безопасности.

4. Передача третьим лицам

4.1. Данные передаются Яндексу в рамках OAuth и вызовов официальных API по вашему поручению.

4.2. Иным третьим лицам данные не продаются.

5. Контакты

По вопросам обработки ПД: [EMAIL].
"""

_CONSENT = """
СОГЛАСИЕ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ

Регистрируясь в сервисе AiMarketing ([URL_BASE]) или входя через Яндекс, вы даёте согласие на обработку указанных в Политике конфиденциальности данных, включая получение и хранение OAuth-токенов Яндекса для доступа к Метрике и Директу.

Согласие действует до отзыва. Отозвать согласие можно, удалив аккаунт или написав на [EMAIL].
"""

_OFFER = """
ПУБЛИЧНАЯ ОФЕРТА

Сервис AiMarketing ([URL_BASE]) предоставляет программный доступ к автоматизации работы с рекламными кабинетами Яндекс Директа и аналитикой Яндекс Метрики.

Акцепт оферты — регистрация или вход через Яндекс. Актуальные условия и тарифы публикуются на сайте. По вопросам: [EMAIL].
"""

_COOKIES = """
ПОЛИТИКА COOKIES

Сайт AiMarketing использует необходимые cookie и localStorage для хранения сессии входа в кабинет. Аналитические cookie могут применяться для улучшения сервиса. Продолжая пользоваться сайтом, вы соглашаетесь с использованием указанных технологий. Подробности — в Политике конфиденциальности. Контакт: [EMAIL].
"""


def legal_privacy_data() -> dict:
    return legal_page_data(
        title="Политика конфиденциальности — AiMarketing",
        heading="Политика конфиденциальности",
        body=_fill(_PRIVACY),
        page_path="/privacy",
        footer="AiMarketing",
    )


def legal_consent_data() -> dict:
    return legal_page_data(
        title="Согласие на обработку ПД — AiMarketing",
        heading="Согласие на обработку персональных данных",
        body=_fill(_CONSENT),
        page_path="/consent",
        footer="AiMarketing",
    )


def legal_offer_data() -> dict:
    return legal_page_data(
        title="Оферта — AiMarketing",
        heading="Публичная оферта",
        body=_fill(_OFFER),
        page_path="/offer",
        footer="AiMarketing",
    )


def legal_cookies_data() -> dict:
    return legal_page_data(
        title="Cookies — AiMarketing",
        heading="Политика cookies",
        body=_fill(_COOKIES),
        page_path="/cookies",
        footer="AiMarketing",
    )
