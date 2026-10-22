# AiMarketing

SaaS-кабинет для автоматизации Яндекс Директа и Метрики.

Стек как у Aimarket: Vue 3 + FastAPI + Postgres + Nginx + Caddy.

## Что уже есть

- Регистрация и вход по почте
- Главная кабинета: блоки «Метрика» и «Директ» + одна кнопка OAuth (редирект на Яндекс, как Google)
- Автоподтягивание счётчиков и кабинетов после согласия
- Вкл/выкл сущностей, повторный синк, админка, юрстраницы

## Быстрый старт

```bat
copy .env.example .env
```

Заполните обязательные поля в `.env`, затем:

```bat
docker compose up -d --build
```

Локально без Docker:

1. Поднимите Postgres и пропишите `DB_*` в `.env`
2. `cd backend && pip install -r requirements.txt && python run_api.py`
3. `cd frontend && npm install && npm run dev`

## Яндекс OAuth

1. Создайте приложение на [oauth.yandex.ru](https://oauth.yandex.ru)
2. Callback URL: `{PUBLIC_BASE_URL}/api/web/yandex/callback`
3. Права: `login:info`, `login:email`, `metrika:read`, `direct:api`
4. Для Директа отдельно подайте заявку на доступ к API в кабинете Яндекса
5. Пропишите `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET`, `TOKEN_ENCRYPT_SECRET`, `PUBLIC_BASE_URL`

## Поток «минимум действий»

1. Регистрация по почте → главная кабинета
2. Кнопка «Подключить…» → `oauth.yandex.ru` (скоупы Метрики и Директа)
3. После согласия — счётчики и кабинеты уже в кабинете
