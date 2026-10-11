# Локальный запуск Сцепки

## Подготовка бэкенда

Настройте окружение и сервисные ключи по [инструкции API](../api/deployment.md). Подготовьте банк и Piston по [инструкции taskgen](../taskgen/deployment.md), эмбеддер — по [инструкции matcher](../matcher/deployment.md)

Используйте одинаковое имя хоста для браузера и API. Для локального HTTP нужны `COOKIE_SECURE=false`, разрешённый frontend origin в `CORS_ORIGINS` и правильный `FRONTEND_URL` для ссылок из писем

## Разработка интерфейса

Нужны Node.js из требования `frontend/package.json` и npm. Из `frontend/`:

```sh
npm ci
API_TARGET=http://127.0.0.1:8000 npm run dev
```

Адрес интерфейса задаёт npm script в `frontend/package.json`. `API_TARGET` нужен только dev proxy; сервисные ключи остаются в окружении API и не попадают во frontend build

После изменения HTTP-контракта экспортируйте OpenAPI командой из [README API](../../api/README.md), затем выполните `npm run api:generate` из `frontend/`

## Сборка с Compose

Из корня репозитория:

```sh
docker compose up --build -d
docker compose ps
docker compose logs frontend api matcher taskgen
```

`frontend/Dockerfile` собирает приложение и отдаёт его через Nginx. `/api/` проксируется к сервису API; пути cookie совпадают с dev proxy. Опубликованный порт задаёт `FRONTEND_PORT`, адреса писем и разрешённый origin должны соответствовать выбранному адресу браузера

Для проверки параллельно с действующим окружением используйте отдельные базы, роли, storage и порты. Общий Compose имеет фиксированный сервер Postgres: один `-p` не создаёт независимый стенд

## Демоданные

Новый режим `fspdb seed-demo --count` создаёт профессионально согласованные профили через Faker. Он добавляет собственные записи, повторный запуск сохраняет существующие данные. Фиксированные старые fixtures доступны в режиме без `--count`; его ограничения описаны в [инструкции API](../api/deployment.md)

Из `api/`, на отдельной демонстрационной базе с применёнными миграциями:

```sh
uv sync --frozen --group demo
DATABASE_URL="$DEMO_DATABASE_URL" uv run fspdb seed
DATABASE_URL="$DEMO_DATABASE_URL" SCEPKA_DEMO_ENABLED=true SCEPKA_DEMO_PASSWORD="$DEMO_PASSWORD" uv run fspdb seed-demo --count 1200 --seed 20261011 --as-of 2026-10-11T00:00:00+00:00
DATABASE_URL="$DEMO_DATABASE_URL" uv run fspdb relevance
```

`DEMO_DATABASE_URL` должен принадлежать роли подготовки, а не ограниченной runtime-роли API. `DEMO_PASSWORD` задаёт пароль локальных демонстрационных аккаунтов и должен удовлетворять проверке CLI

Войти можно как `candidate@scepka.example` и `employer@scepka.example` с заданным паролем. Профили помечены синтетическими; грейд в них заявлен, не подтверждён выдуманным тестом. Генератор также создаёт входной опрос, компанию, потребности и опубликованные вакансии

Faker не пишет случайные векторы. Запустите настоящий подбор через кабинет работодателя либо пересчитайте embeddings командой из [инструкции matcher](../matcher/deployment.md). После префильтра пул должен превышать `vector.top_k`, чтобы проверить векторный отбор

## Проверки интерфейса

Из `frontend/`:

```sh
npm run typecheck
npm test
npm run build
npx --no-install playwright test --project=ui --forbid-only --fail-on-flaky-tests --retries 0
```

Для живых сценариев нужен отдельный API с демоданными, Mailpit, taskgen, Piston и matcher:

```sh
SCEPKA_BASE_URL="$FRONTEND_URL" SCEPKA_DEMO_PASSWORD="$DEMO_PASSWORD" SCEPKA_MAILPIT_URL="$MAILPIT_URL" npx --no-install playwright test --project=live --forbid-only --fail-on-flaky-tests --retries 0
```

На системе без совместимого Playwright browser задайте `PLAYWRIGHT_CHROMIUM_EXECUTABLE` путём к Chromium. На NixOS проверенный в этой работе вариант использовал системный Chromium; наличие бинарника отдельно не доказывает работоспособность браузерного сценария

Скриншоты снимает `tests/capture.ts`: он ждёт шрифты и отключает анимации для воспроизводимого кадра. Файлы находятся в [галерее](screenshots/), сценарии — в [отчёте проверки](validation.md). Они получены из настоящего приложения, не из отдельного макета

## Документация и PDF

Веб-раздел `/docs` использует существующие файлы репозитория. Из `frontend/` команда `npm run docs:pdf -- <путь-к-файлу.pdf>` собирает руководство интерфейса из тех же исходников через запущенное приложение. Для системного браузера используется та же переменная `PLAYWRIGHT_CHROMIUM_EXECUTABLE`
