# Запуск API

## Конфигурация

Параметры приложения определяет `api/app/settings.py`. Корневой `.env.example` перечисляет переменные для Compose, локальный `.env` не входит в репозиторий

Создайте `JWT_SECRET` и пароль роли API независимо друг от друга: `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. Заполните `API_DB_PASSWORD`, `TASKGEN_CLIENT_KEY` и `MATCHER_CLIENT_KEY`; сервисные ключи должны иметь роль `client`

Ключи выпускают CLI соответствующих сервисов, их инструкции — [taskgen](../taskgen/deployment.md) и [matcher](../matcher/deployment.md). Не передавайте сервисные ключи фронтенду

Для локального HTTP-демо включите `DEMO_AUTH_ALLOW_UNVERIFIED=true`, `DEMO_EMAIL_TOKENS=true`, `FSP_DEMO_ENABLED=true` и `COOKIE_SECURE=false`. Для обычного режима отключите демонстрационные флаги, настройте SMTP и HTTPS. `ALLOW_PUBLIC_CONTACTS=false` сохраняет правило ТЗ; включение требует отдельного согласия кандидата

Пароль в URL БД должен быть URL-совместимым или percent-encoded. Сгенерированный `token_urlsafe` пароль подходит без преобразования

## Compose

Из корня проекта: `docker compose up --build -d`. API использует образ из `api/Dockerfile`; `api-migrate` применяет миграции ролью владельца, `api-db-init` выдаёт runtime-права. Схема `match` доступна приложению только для чтения

Справочники: `docker compose run --rm api-migrate fspdb seed`. Демонстрационные данные на новой базе: `docker compose run --rm api-migrate fspdb seed-demo`, затем `docker compose run --rm api-migrate fspdb relevance`

Повторный `seed-demo` имеет известный дефект очистки. Не используйте его как способ сбросить действующий стенд

Проверка: `docker compose ps`, `docker compose logs api api-migrate api-db-init`. `/health` проверяет доступ к предметной БД; состояние каждой внешней операции видно через `/operations/{id}`

До тестирования подготовьте окружения Piston и загрузите банк taskgen по [его инструкции](../taskgen/deployment.md#3-пакеты-песочницы). Свежая песочница не содержит языков, запущенный контейнер сам по себе не подтверждает готовность заданий

Для LLM-разбора резюме включите `RESUME_LLM_ENABLED=true` и настройте `llm` из `app/settings.py`. Роли `resume_parser` и `resume_checker` используют общий слой taskgen; при отказе проверяльщика остаются механические предложения. Ollama должна быть доступна именно из процесса API; адрес loopback хоста недоступен из обычной сети Docker

Общий Compose использует фиксированное имя контейнера Postgres и имя тома из `db/docker-compose.yml`. Один `-p` не изолирует второй стенд: для параллельной проверки используйте отдельную тестовую БД и запускайте только новый API с отдельным портом

## Локальный процесс

Из `api/`: `uv sync --frozen --group dev`, затем `DATABASE_URL=<URL владельца тестовой БД> uv run alembic upgrade head`. Для runtime запуска задайте URL ограниченной роли API и выполните `uv run --env-file ../.env uvicorn app.main:app --host 127.0.0.1 --port 18000`. Нужен Python из требования `api/pyproject.toml`

API обслуживает очередь одним процессом. PostgreSQL advisory lock исключает второго исполнителя очереди для той же БД; не запускайте приложение с несколькими workers

## Проверки

Создайте отдельную PostgreSQL БД с расширениями `vector`, `citext`, `pg_trgm`, примените миграции и `fspdb seed`. `API_TEST_DATABASE_URL` должен указывать на эту базу; тесты сохраняют свои данные и не должны идти в production

Из `api/`:

```sh
uv run ruff check app tests
uv run ruff format --check app tests
API_TEST_DATABASE_URL=<URL тестовой БД> uv run pytest
DATABASE_URL=<URL владельца тестовой БД> uv run alembic heads
DATABASE_URL=<URL владельца тестовой БД> uv run alembic check
```

После изменения схемы выполните интеграционные тесты matcher по [его инструкции](../matcher/deployment.md). Роль подготовки тестов должна также иметь права на очистку `matcher.api_keys`; это не runtime-роль API
