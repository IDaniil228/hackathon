# Слой хранения

PostgreSQL 18 + pgvector в своём контейнере, приложение и вся работа с базой —
в соседнем. Схема описана моделями SQLAlchemy и разворачивается Alembic;
файлы в этом каталоге — **выгрузка**, а не источник правды.

| Что | Где |
|---|---|
| источник правды по схеме | `api/app/db/models/` |
| миграции | `api/migrations/versions/` |
| бэкап DDL | `db/backup/schema.sql` (генерируется) |
| бэкап содержимого | `db/backup/data.sql` (генерируется) |

## Запуск

```bash
cp .env.example .env     # пароль поменять
docker compose up -d
```

Поднимается два контейнера: `postgres` (образ `pgvector/pgvector:pg18` с Docker
Hub) и `api` (официальный `python:3.13-slim`, без своего Dockerfile). Контейнер
`api` при старте доустанавливает зависимости в `api/.venv-docker`, накатывает
`alembic upgrade head` и поднимает uvicorn на `localhost:8000`.

Наполнение:

```bash
docker compose exec api fspdb seed        # справочники и банк шаблонов заданий
docker compose exec api fspdb seed-demo   # демо-данные под сквозной сценарий
docker compose exec api fspdb relevance   # посчитать веса навыков
```

Проверка: `curl localhost:8000/health` → `{"status":"ok","candidates":6,...}`.

Веб-клиент к базе для демонстрации (http://localhost:8081, сервер `postgres`):

```bash
docker compose --profile tools up -d adminer
```

## Команды `fspdb`

| Команда | Что делает |
|---|---|
| `seed` | справочники и банк шаблонов заданий, идемпотентно |
| `seed-demo` | демо-данные; сносит предыдущие демо-строки и создаёт заново |
| `relevance` | пересчитать веса навыков из наблюдаемого спроса |
| `dump-schema` | DDL из моделей → `db/backup/schema.sql` |
| `dump-data` | содержимое таблиц → `db/backup/data.sql` |
| `stats` | что сейчас в базе |

## Миграции

```bash
docker compose exec api alembic upgrade head          # накатить
docker compose exec api alembic check                 # модели и база совпадают?
docker compose exec api alembic revision --autogenerate -m "что меняем"
docker compose exec api alembic downgrade -1          # откатить одну
```

Контейнер `api` работает от UID хозяина, поэтому новые файлы ревизий остаются
доступными на правку, а не появляются от root.

> [!] **Автогенерация и `search_path`.** Не прописывайте роли
> `search_path` со всеми шестью схемами: PostgreSQL начнёт отдавать имена без
> квалификации, и `alembic check` будет видеть фантомные различия в каждом
> внешнем ключе и каждом ENUM. Схема указывается явно в моделях.

Что автогенерация **не видит** и что дописано в первую ревизию руками: создание
схем, расширений (`vector`, `citext`, `pg_trgm`) и ENUM-типов. У типов в моделях
стоит `create_type=False`, иначе SQLAlchemy пытался бы создать один и тот же тип
при каждой использующей его таблице.

## Бэкап в .sql

```bash
docker compose exec api fspdb dump-schema
docker compose exec api fspdb dump-data
```

Оба файла собираются **чистым Python из моделей и текущей базы**, без `pg_dump`:
`schema.sql` компилируется из той же `MetaData`, по которой Alembic строит
миграции, поэтому разойтись со схемой он не может.

Восстановление в пустую базу:

```bash
docker compose exec postgres createdb -U fsp fsp_copy
docker compose exec -T postgres psql -U fsp -d fsp_copy -v ON_ERROR_STOP=1 -f /sql/backup/schema.sql
docker compose exec -T postgres psql -U fsp -d fsp_copy -v ON_ERROR_STOP=1 -f /sql/backup/data.sql
```

Проверено: восстановленная копия совпадает с оригиналом по всем таблицам.
В `data.sql` учтены две тонкости — `OVERRIDING SYSTEM VALUE` для
`GENERATED ALWAYS AS IDENTITY` (иначе исходные id не сохранить и разъедутся
внешние ключи) и выравнивание последовательностей в конце (иначе первый же
INSERT из приложения упал бы на дубликате ключа).

## Что должен знать бэкенд

**Схемы указываются явно.** Таблицы не в `public`:

```python
class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = {"schema": "profile"}
```

**Никаких триггеров и хранимых функций** — проверяется в базе, `pg_proc` и
`pg_trigger` по нашим схемам пустые. Следствия:

* `updated_at` проставляет приложение при каждом UPDATE;
* `current_category_id` у кандидата обновляется в одной транзакции со вставкой
  в `assess.grade_assignments`; источник истины — запись с `is_current = true`;
* смена грейда: `UPDATE ... SET is_current = false` → `INSERT` → обновление
  `current_category_id`, всё одной транзакцией (партиальный уникальный индекс
  не даст двум записям быть текущими);
* кулдаун проверяется сравнением `now()` с
  `grade_assignments.next_change_allowed_at` (интервал — из `ref.grades.cooldown_days`).

**Контакты кандидата** отдаются только при наличии живой строки в
`hire.contact_disclosures` (`revoked_at IS NULL`). Строка создаётся в той же
транзакции, что и принятие приглашения или создание отклика.

**Веса навыков не вводятся руками.** В справочнике `ref.specialization_skills`
только булево `is_core`; числовой вес считает `fspdb relevance` из наблюдаемого
спроса и кладёт в `match.skill_relevance`. Формула и обоснование —
в `api/app/db/relevance.py`.

**pgvector.** HNSW-индекс работает, только если вектор для сравнения —
константа, параметр запроса или скалярный подзапрос. Вектор из JOIN уводит
планировщик в seq scan **молча**, без ошибки:

```sql
SELECT candidate_id FROM match.candidate_embeddings
ORDER BY embedding <=> (SELECT embedding FROM match.need_embeddings WHERE need_id = :need)
LIMIT 40;
```

Размерность зафиксирована: `vector(1024)` под multilingual-e5-large / bge-m3.

**Другая предметная область.** Чтобы запустить платформу вне ИТ, меняются
только данные: строка в `ref.industries`, свои специализации, грейды, навыки и
свой `app/db/seeds/task_bank.py`. Модели не трогаются — это проверяется
заведённой в справочниках отраслью «Маркетинг».

## Если первый запуск не поднимается

Контейнер `api` ставит зависимости с PyPI при первом старте. Нет доступа к
PyPI из контейнера — venv останется пустым и uvicorn не запустится. Разовое
решение:

```bash
docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD/api":/app -w /app \
  python:3.13-slim bash -c 'test -x .venv-docker/bin/python || python -m venv .venv-docker; .venv-docker/bin/python -m pip install -e .'
```
