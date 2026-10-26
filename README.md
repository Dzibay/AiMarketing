# AiMarketing

SaaS-кабинет для автоматизации Яндекс Директа и Метрики.

Стек как у Aimarket: Vue 3 + FastAPI + Postgres + Nginx + Caddy.

## Что уже есть

- Регистрация и вход по почте, OAuth Яндекса (Метрика + Директ)
- Клиенты: карточка с KPI (CPA / ДРР) и бюджетом, привязка кабинетов Директа и счётчиков Метрики;
  новые кабинеты при синке раскладываются по клиентам автоматически
- Кабинет: подключения, связи Директ↔Метрика, цели Метрики
- Кампании: синк, пагинация, создание TEXT, имя/бюджет, пауза/архив/удаление
- Группы объявлений и TextAd: синк, создание, правка, пауза/архив/удаление
- Ключевые фразы: синк, добавление, ставка, пауза/запуск/удаление
- Минус-фразы кампании и группы объявлений
- Поисковые запросы: синк, фильтры, массово в ключи / минуса группы или кампании
- Статистика периода по группам и ключам (вместе с синком запросов)
- Аналитика: Direct + Метрика, склейка, цели, бэкфилл, тренд, CSV, топы ключей/групп/запросов
- Онбординг-чеклист и смена пароля в кабинете
- Правила: кампании, ключи и поисковые запросы; CPA/CPC/расход; пауза/ставка/минуса/notify; dry-run
- История sync_runs в кабинете; сброс пароля (SMTP или ссылка в лог)
- Telegram-алерты по правилам (бот + привязка через /start)
- Очередь задач в Postgres: все синки идут фоном с прогрессом и ретраями, расписание в БД
- Миграции схемы через Alembic; pytest (юнит + интеграционные с Postgres) / CI
- Админка (блок/разблок) и юрстраницы; OAuth без session token в URL

## Быстрый старт

```bat
copy .env.example .env
```

Заполните обязательные поля в `.env`, затем:

```bat
docker compose up -d --build
```

Сервисы: `postgres`, `backend` (API без встроенных циклов), `worker` (очередь задач, расписание, Telegram), `nginx`, `caddy`.
Локально без Docker API по умолчанию поднимает воркеры в том же процессе (`RUN_EMBEDDED_WORKERS=true`).

## Схема БД и очередь задач

Схема ведётся миграциями Alembic (`backend/migrations/versions`). `backend` и `worker` при старте
сами делают `alembic upgrade head` (параллельный запуск сериализуется advisory-lock). Базовая
миграция повторяет бывший `database/init.sql` и идемпотентна — БД, созданная до Alembic,
подхватывается без пересоздания.

```bat
cd backend
alembic upgrade head
alembic revision -m "что меняем"
```

Миграции пишутся на SQL через `op.execute`; `alembic.ini` — только ASCII (Alembic читает его в кодировке ОС).

Долгие операции (синк подключения, кампаний, структуры, запросов, целей, статистики, правила)
не выполняются в HTTP-запросе: API ставит задачу в таблицу `jobs` и отдаёт `{"job": …}`, фронт
опрашивает `GET /api/web/jobs/{id}`. Исполнители берут задачи через `FOR UPDATE SKIP LOCKED`,
одинаковая активная задача не дублируется, ошибки Яндекса ретраятся с backoff, зависшие
(без heartbeat) возвращаются в очередь. Периодические синки планируются через `job_schedules`.
Число параллельных задач на процесс — `WORKER_CONCURRENCY` (по умолчанию 3).

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

## Telegram

1. Создайте бота у [@BotFather](https://t.me/BotFather), пропишите `TELEGRAM_BOT_TOKEN` и `TELEGRAM_BOT_USERNAME`
2. В кабинете → «Получить ссылку» → Start в боте
3. Опционально webhook: `{PUBLIC_BASE_URL}/api/web/telegram/webhook`
   (задайте `TELEGRAM_WEBHOOK_SECRET` и тот же `secret_token` в `setWebhook`; иначе polling воркера)

## Тесты

```bat
cd backend
pip install -r requirements.txt
pytest -q
```

Без `TEST_DB_*` выполняются только юнит-тесты. Интеграционные (`tests/test_db_*.py`) **пересоздают
схему** — только в одноразовой БД, имя которой содержит `test`:

```bat
docker run -d --name aimarketing-testpg -e POSTGRES_USER=aim_test -e POSTGRES_PASSWORD=aim_test -e POSTGRES_DB=aim_test -p 55432:5432 postgres:16-alpine
set TEST_DB_HOST=127.0.0.1& set TEST_DB_PORT=55432& set TEST_DB_NAME=aim_test& set TEST_DB_USER=aim_test& set TEST_DB_PASSWORD=aim_test
pytest -q
```

CI (GitHub Actions): pytest с сервисом Postgres на каждый PR + docker smoke против `/api/health` и `smoke_test.py`.
