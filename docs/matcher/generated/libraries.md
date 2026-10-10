# Библиотеки и компоненты matcher

> [!NOTE]
> Файл собран командой `uv run python scripts/gen_docs.py` из каталога `matcher/`. Не правьте его руками: правьте источник и пересоберите файл. Источник — [`uv.lock`](../../../matcher/uv.lock), [`docker-compose.yml`](../../../matcher/docker-compose.yml) с подключённым [`db/docker-compose.yml`](../../../db/docker-compose.yml) и [`Dockerfile`](../../../matcher/Dockerfile)

Проект требует Python `>=3.12`, образ сервиса задаёт `Dockerfile`. Версии ниже — те, что зафиксированы в `uv.lock`; `uv sync --frozen` ставит ровно их

## Прямые зависимости сервиса

| Пакет | Версия | Ограничение в pyproject.toml |
|---|---|---|
| `alembic` | `1.20.0` | любая |
| `asyncpg` | `0.32.0` | любая |
| `fastapi` | `0.143.0` | любая |
| `httpx` | `0.28.1` | любая |
| `markdown-it-py` | `4.2.0` | `>=4.2.0` |
| `mdit-py-plugins` | `0.6.1` | `>=0.6.1` |
| `pydantic-settings` | `2.15.0` | любая |
| `sqlalchemy` | `2.1.4` | любая |
| `uvicorn` | `0.54.0` | любая |

## Зависимости разработки

| Пакет | Версия | Ограничение в pyproject.toml |
|---|---|---|
| `pytest` | `9.1.1` | любая |
| `pytest-asyncio` | `1.4.0` | любая |
| `ruff` | `0.17.0` | `>=0.16.10` |

## Транзитивные зависимости

Зависимости всех пакетов выше

| Пакет | Версия |
|---|---|
| `annotated-doc` | `0.0.5` |
| `annotated-types` | `0.8.0` |
| `anyio` | `4.15.1` |
| `certifi` | `2026.7.22` |
| `click` | `8.5.0` |
| `colorama` | `0.4.6` |
| `greenlet` | `3.5.6` |
| `h11` | `0.16.0` |
| `httpcore` | `1.0.9` |
| `httptools` | `0.9.0` |
| `idna` | `3.20` |
| `iniconfig` | `2.3.1` |
| `mako` | `1.4.3` |
| `markupsafe` | `3.0.4` |
| `mdurl` | `0.1.2` |
| `opentelemetry-api` | `1.45.1` |
| `packaging` | `26.3` |
| `pluggy` | `1.6.0` |
| `pydantic` | `2.14.0` |
| `pydantic-core` | `2.50.0` |
| `pygments` | `2.21.0` |
| `python-dotenv` | `1.2.4` |
| `pyyaml` | `6.0.3` |
| `starlette` | `1.7.0` |
| `typing-extensions` | `4.16.0` |
| `typing-inspection` | `0.4.4` |
| `uvloop` | `0.23.0` |
| `watchfiles` | `1.3.0` |
| `websockets` | `17.2` |

## Контейнеры

| Образ | Тег | Где |
|---|---|---|
| `adminer` | `5` | adminer (db/docker-compose.yml) |
| `ghcr.io/astral-sh/uv` | `latest` | образ matcher |
| `pgvector/pgvector` | `pg18` | matcher-db-init (matcher/docker-compose.yml) |
| `pgvector/pgvector` | `pg18` | postgres (db/docker-compose.yml) |
| `python` | `3.12-slim` | образ matcher |
