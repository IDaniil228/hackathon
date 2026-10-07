---
name: taskgen-test-db
description: Тесты taskgen без своей БД идут в базу по умолчанию и падают ложно; как завести БД для прогона
metadata:
  type: project
---

`taskgen/tests/conftest.py` берёт `TASKGEN_DATABASE_URL`, а без переменной — `postgresql+asyncpg://taskgen:taskgen@localhost:5432/taskgen`. В этой базе нет свежих миграций справочников, и тесты банка падают с `skills: нет навыков в справочнике: ...` — это не баг кода. Так ошибались и главный агент, и субагенты 2026-10-07

Своя база на worktree или агента: `docker exec taskgen-db-1 createdb -U taskgen <имя>`, затем из `taskgen/` `TASKGEN_DATABASE_URL=postgresql+asyncpg://taskgen:taskgen@localhost:5432/<имя> uv run alembic upgrade head`, в конце `docker exec taskgen-db-1 dropdb -U taskgen <имя>`. Полный прогон: `uv run pytest -q --deselect tests/evaluation/test_judge_llm.py` с той же переменной. Тест `test_pool_refills_on_its_own_interval` ждёт базу без чужих шаблонов: после `taskgen import` в ту же базу он падает

**Why:** ложные падения тратят время на поиск несуществующей причины

**How to apply:** каждую команду pytest в taskgen запускать с явным `TASKGEN_DATABASE_URL` своей базы; субагенту выдавать имя базы в брифе. Связано с [[taskgen-decisions]]
