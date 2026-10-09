---
name: taskgen-test-db
description: Тесты taskgen без своей БД идут в базу по умолчанию и падают ложно; как завести БД для прогона
metadata:
  node_type: memory
  type: project
  originSessionId: 735e8942-02bb-4c5b-a8e1-9ea36da1780b
  modified: 2026-10-09T23:36:31.847Z
---

`taskgen/tests/conftest.py` берёт `TASKGEN_DATABASE_URL`, а без переменной — умолчание `database_url` из `config.py`, общую базу `taskgen` на `localhost:5432`. Сервер Postgres — общий сервис платформы из `db/docker-compose.yml` (контейнер `fsp-postgres`), у `taskgen` на нём своя база и роль `taskgen`. В этой базе нет свежих миграций справочников, и тесты банка падают с `skills: нет навыков в справочнике: ...` — это не баг кода. Так ошибались и главный агент, и субагенты 2026-10-07

Своя база на worktree или агента, из `taskgen/`: `docker compose run --rm -e TASKGEN_DB_NAME=<имя> taskgen-db-init` (база с владельцем `taskgen` и расширением `vector`, которое роль `taskgen` сама создать не может), затем `TASKGEN_DATABASE_URL=postgresql+asyncpg://taskgen:taskgen@localhost:5432/<имя> uv run alembic upgrade head`, в конце `docker exec fsp-postgres dropdb -U taskgen <имя>`. Полный прогон: `uv run pytest -q --deselect tests/evaluation/test_judge_llm.py` с той же переменной. Тест `test_pool_refills_on_its_own_interval` ждёт базу без чужих шаблонов: после `taskgen import` в ту же базу он падает

**Why:** ложные падения тратят время на поиск несуществующей причины

**How to apply:** каждую команду pytest в taskgen запускать с явным `TASKGEN_DATABASE_URL` своей базы; субагенту выдавать имя базы в брифе. Связано с [[taskgen-decisions]]
