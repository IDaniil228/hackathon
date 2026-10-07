# Библиотеки и компоненты taskgen

> [!NOTE]
> Файл собран командой `uv run python scripts/gen_docs.py` из каталога `taskgen/`. Не правьте его руками: правьте источник и пересоберите файл. Источник — [`uv.lock`](../../../taskgen/uv.lock), реестр окружений [`runtimes.yaml`](../../../taskgen/src/taskgen/sandbox/runtimes.yaml) с файлами зависимостей пакетов в [`sandbox/packages`](../../../taskgen/sandbox/packages), [`docker-compose.yml`](../../../taskgen/docker-compose.yml) и [`Dockerfile`](../../../taskgen/Dockerfile)

Python сервиса — `3.12` (`.python-version`), проект требует `>=3.12`. Версии ниже — те, что зафиксированы в `uv.lock`; `uv sync --frozen` ставит ровно их

## Прямые зависимости сервиса

| Пакет | Версия | Ограничение в pyproject.toml |
|---|---|---|
| `alembic` | `1.20.0` | любая |
| `asyncpg` | `0.31.0` | любая |
| `fastapi` | `0.142.2` | любая |
| `httpx` | `0.28.1` | любая |
| `pgvector` | `0.5.0` | любая |
| `pydantic-settings` | `2.15.0` | любая |
| `python-multipart` | `0.0.32` | `>=0.0.32` |
| `pyyaml` | `6.0.3` | любая |
| `sqlalchemy` | `2.1.3` | любая |
| `uvicorn` | `0.54.0` | любая |

## Зависимости разработки

| Пакет | Версия | Ограничение в pyproject.toml |
|---|---|---|
| `lizard` | `1.24.1` | `>=1.24.1` |
| `pytest` | `9.1.1` | любая |
| `pytest-asyncio` | `1.4.0` | любая |
| `radon` | `6.0.1` | `>=6.0.1` |
| `ruff` | `0.16.10` | `>=0.16.10` |

## Необязательная группа `nli`

Ставится только явно: `uv sync --group nli`

| Пакет | Версия | Ограничение в pyproject.toml |
|---|---|---|
| `huggingface-hub` | `1.33.0` | любая |
| `numpy` | `2.5.3` | любая |
| `onnxruntime` | `1.30.0` | любая |
| `tokenizers` | `0.23.2` | любая |

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
| `filelock` | `4.0.12` |
| `flatbuffers` | `25.12.19` |
| `fsspec` | `2026.9.0` |
| `greenlet` | `3.5.6` |
| `h11` | `0.16.0` |
| `hf-xet` | `1.7.0` |
| `httpcore` | `1.0.9` |
| `httptools` | `0.8.0` |
| `idna` | `3.20` |
| `iniconfig` | `2.3.0` |
| `mako` | `1.4.3` |
| `mando` | `0.7.1` |
| `markupsafe` | `3.0.4` |
| `opentelemetry-api` | `1.45.0` |
| `packaging` | `26.3` |
| `pathspec` | `1.1.1` |
| `pluggy` | `1.6.0` |
| `protobuf` | `7.36.2` |
| `pydantic` | `2.13.5` |
| `pydantic-core` | `2.46.5` |
| `pygments` | `2.21.0` |
| `python-dotenv` | `1.2.4` |
| `six` | `1.17.0` |
| `starlette` | `1.7.0` |
| `tqdm` | `4.70.1` |
| `typing-extensions` | `4.16.0` |
| `typing-inspection` | `0.4.4` |
| `uvloop` | `0.23.0` |
| `watchfiles` | `1.3.0` |
| `websockets` | `17.2` |

## Окружения песочницы

Окружения, в которых исполняются генераторы тестов, эталоны и решения кандидатов. Описание — тот же текст, который видит кандидат в задании

| Окружение | Пакет Piston | Версия | Источник | Описание |
|---|---|---|---|---|
| `analyzers` | `analyzers` | `1.0.0` | собирается локально | Окружение: анализаторы кода кандидата; библиотеки ruff 0.16.10, radon 6.0.1, lizard 1.24.1, clang-tidy 22.1.8, cppcheck 1.5.2 |
| `bash` | `bash` | `5.2.0` | официальный Piston | Окружение: Bash 5.2.0; утилиты GNU coreutils 8.30, grep 3.3, sed 4.7 и mawk 1.3.3 (команда awk) |
| `cpp` | `gcc` | `10.2.0` | официальный Piston | Окружение: GCC 10.2.0, стандарт C++17; только стандартная библиотека |
| `python` | `python` | `3.12.0` | официальный Piston | Окружение: Python 3.12.0; только стандартная библиотека |
| `python-ml` | `python-ml` | `3.12.0` | собирается локально | Окружение: Python 3.12.0 для задач машинного обучения; библиотеки torch 2.14.1, scikit-learn 1.9.1, numpy 1.26.1 |

## Библиотеки окружений песочницы

| Окружение | Библиотека | Версия |
|---|---|---|
| `analyzers` | `ruff` | `0.16.10` |
| `analyzers` | `radon` | `6.0.1` |
| `analyzers` | `lizard` | `1.24.1` |
| `analyzers` | `clang-tidy` | `22.1.8` |
| `analyzers` | `cppcheck` | `1.5.2` |
| `python-ml` | `torch` | `2.14.1` |
| `python-ml` | `scikit-learn` | `1.9.1` |
| `python-ml` | `numpy` | `1.26.1` |

## Контейнеры

| Образ | Тег | Где |
|---|---|---|
| `pgvector/pgvector` | `pg17` | db |
| `ollama/ollama` | тег не задан, `latest` | ollama |
| `ghcr.io/engineer-man/piston` | тег не задан, `latest` | piston |
| `ghcr.io/astral-sh/uv` | `latest` | образ taskgen |
