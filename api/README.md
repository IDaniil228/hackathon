# API платформы ФСП

FastAPI + SQLAlchemy 2.0 + Alembic. Работает в своём контейнере, PostgreSQL —
в соседнем.

```
app/
  main.py          точка входа FastAPI
  settings.py      настройки из переменных окружения
  handlers/        HTTP-роутеры
  db/
    base.py        Base, MetaData, соглашение об именах ограничений
    enums.py       33 ENUM-типа предметной области
    session.py     асинхронный движок и зависимость get_session
    relevance.py   пересчёт весов навыков из наблюдений
    models/        модели шести схем: core, ref, profile, assess, hire, matching
    seeds/         справочники, банк заданий, демо-данные
    cli.py         команды: seed, dump-schema, relevance, stats
migrations/        Alembic: env.py и ревизии
```

Все команды — из каталога репозитория:

```bash
docker compose exec api alembic upgrade head     # схема
docker compose exec api fspdb seed               # справочники и банк заданий
docker compose exec api fspdb seed-demo          # демо-данные
docker compose exec api fspdb dump-schema        # бэкап DDL в db/backup/schema.sql
```

Подробности по слою хранения — в `../db/README.md`.
