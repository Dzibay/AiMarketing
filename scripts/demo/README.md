# Демо-стенд кабинета

Кабинет с правдоподобными данными на временной базе — чтобы смотреть интерфейс, снимать скриншоты всех
страниц и проверять доступность. Рабочую базу стенд не трогает: `env.py` откажется работать, если в имени
базы нет «demo» или «test» или хост не локальный.

| Файл | Что делает |
|---|---|
| `env.py` | окружение стенда: временная БД, случайные секреты стенда, путь к `backend` |
| `seed.py` | наполняет пустую базу: 3 проекта, 2 кабинета Директа, 9 кампаний, 60 дней статистики, визиты Метрики, запросы, правила, минус-фразы, чаты — в том числе с карточками на согласование (минус-фразы и этап «Картинки») |
| `api.py` | API кабинета на базе стенда (порт 8000, без фоновых воркеров) + `GET /api/_demo/session` с токеном входа и `/api/_demo/assets/*` — картинки демо-чата |
| `assets/` | 8 картинок для этапа «Картинки» (SVG, без текста; квадрат, 4:3 и 16:9 — проверить `contain` в превью) |
| `shots.mjs` | скриншоты и аудит всех страниц в headless Chrome (без npm-зависимостей) |

Папка стенда — `DEMO_DIR` (по умолчанию `C:/Users/Public/tolk_demo`, на Linux/macOS `/tmp/tolk_demo`):
в ней секреты, токен демо-пользователя (`token.txt`) и номера проектов (`stand.json`). Токен в консоль не
выводится.

## 1. Временный PostgreSQL

Нужен PostgreSQL 18 с локалью `builtin C.UTF-8`: с обычной `C` функция `lower()` не понимает кириллицу.
Путь к кластеру — латиницей. Порт берите свободный: Windows резервирует диапазоны портов
(`netsh interface ipv4 show excludedportrange protocol=tcp`).

```bash
export PATH="/c/Program Files/PostgreSQL/18/bin:$PATH"
initdb -D /c/Users/Public/aimk_pg_demo/data -U aim_test --auth=trust -E UTF8 --locale=C --locale-provider=builtin --builtin-locale=C.UTF-8
```

```bash
pg_ctl -D /c/Users/Public/aimk_pg_demo/data -o "-p 54491 -c listen_addresses=127.0.0.1" -l /c/Users/Public/aimk_pg_demo/pg.log start
```

```bash
createdb -h 127.0.0.1 -p 54491 -U aim_test --template=template0 --locale-provider=builtin --builtin-locale=C.UTF-8 aim_demo
```

Другой порт или имя базы — через `DEMO_DB_PORT` / `DEMO_DB_NAME` (см. `env.py`).

## 2. Данные и сервер

```bash
backend/.venv/Scripts/python scripts/demo/seed.py
```

```bash
backend/.venv/Scripts/python scripts/demo/api.py
```

Фронтенд — обычный `npm run dev` в `frontend` (Vite на 5173 проксирует `/api` на 8000). Чтобы войти в
браузере, положите токен в `localStorage`:

```js
localStorage.setItem('aimarketing-session', (await (await fetch('/api/_demo/session')).json()).token)
```

На стенде нет ключей Яндекса, почты и Telegram, фоновые задачи не работают: «Данные ещё загружаются» и
«ИИ отвечает…» висят — так и задумано. Не отправляйте сообщения ИИ и не нажимайте «Применить в Директ».

## 3. Скриншоты и аудит

```bash
node scripts/demo/shots.mjs --out /c/Users/Public/tolk_demo/shots
```

Снимает 31 страницу (в том числе чат с галереей картинок) в каждом сочетании ширины (`--widths 1440,375`), темы
(`--themes light,dark`) и режима (`--modes simple,expert`) в `<out>/<тема>-<режим>-<ширина>/<страница>.png`.
`--pages home,ws-analytics` — только выбранные страницы. Режим кабинета хранится в аккаунте, поэтому скрипт
не переключает его на сервере, а подставляет нужный в ответ `/api/web/me` своему Chrome — параллельные прогоны
с разными режимами друг другу не мешают. Chrome берётся из `CHROME` (по умолчанию стандартный путь
на Windows), адрес кабинета — из `DEMO_URL` (по умолчанию `http://localhost:5173`; если там другой проект,
запустите Vite стенда на свободном порту и передайте его адрес).

`--audit` дополнительно проверяет каждую страницу:

- горизонтальная прокрутка страницы (важно на 375 px);
- кнопки, ссылки и поля без подписи (ни текста, ни `aria-label`, ни `title`);
- контраст текста ниже WCAG AA (4,5:1, для крупного текста 3:1);
- элементы, у которых при обходе Tab не видно фокуса (в том числе фокус за краем экрана — в закрытом меню);
- вложенные вертикальные прокрутки внутри страницы (кроме чата, всплывающих окон и полей ввода);
- системные `select`, `input[type=date]` и флажки вне `UiCheckbox` / `UiSwitch`;
- атрибуты `title` вне SVG (подсказки — только через `v-tip`);
- ссылки, кнопки и `.is-link` без `cursor: pointer`;
- лайм в фоне неактивных кнопок;
- на 1920 px ширина `.dpage` меньше 1500 px;
- от 1280 px горизонтальная прокрутка у таблицы кампаний.

Итог — `<out>/audit-<темы>-<режимы>.json`, строка на страницу в консоли и в конце сводка: по каждой проверке —
уникальные нарушения, сколько снимков и на каких страницах. Прогон долгий (несколько секунд на
страницу), поэтому темы и режимы удобно гонять параллельно — у каждого запуска свой профиль Chrome:

```bash
for t in light dark; do for m in simple expert; do node scripts/demo/shots.mjs --out audit --widths 375,1280,1440,1920,2560 --themes $t --modes $m --audit > audit-$t-$m.log & done; done; wait
```

### До и после

Для правок, которые не должны ничего менять на экране (удаление мёртвого CSS, рефакторинг), снимите два
прогона с одним и тем же замороженным временем (`--now`, миллисекунды) и без анимаций и сравните:

```bash
NOW=$(date +%s000)
```

```bash
node scripts/demo/shots.mjs --out before --stable --now $NOW
```

```bash
node scripts/demo/shots.mjs --out after --stable --now $NOW
```

```bash
node scripts/demo/shots.mjs --compare before after
```

Сравнение побайтное: выводит изменившиеся снимки. Открывайте их парами — разница бывает в пиксель.

Если между прогонами подменяете css при запущенном Vite, пишите файл целиком за раз (во временный файл, затем
`mv`): Vite может прочитать его посреди перезаписи и закэшировать пустым. Скрипт это заметит — остановится с
ошибкой «стили не загрузились»; тогда `touch` файл и запустите прогон заново.

## 4. Уборка

```bash
pg_ctl -D /c/Users/Public/aimk_pg_demo/data -m fast -w stop
```

```bash
rm -rf /c/Users/Public/aimk_pg_demo /c/Users/Public/tolk_demo
```

В браузере, где входили на стенд, удалите `aimarketing-session` из `localStorage`.
