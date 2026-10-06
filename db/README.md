# Слой хранения

PostgreSQL 18 + pgvector. Схема накатывается одним SQL-файлом явной командой —
никакой автоматики образа при создании тома.

| Файл | Что делает |
|---|---|
| `schema.sql` | расширения, шесть схем, 62 таблицы, 182 индекса, справочники, банк шаблонов заданий |
| `demo_data.sql` | демо-данные под сквозной сценарий из ТЗ |

## Запуск

```bash
cp .env.example .env     # пароль поменять
docker compose up -d postgres
```

Контейнер поднимается с пустой базой. Схема:

```bash
docker compose exec -T postgres psql -U fsp -d fsp -v ON_ERROR_STOP=1 -f /sql/schema.sql
```

Демо-данные (опционально):

```bash
docker compose exec -T postgres psql -U fsp -d fsp -v ON_ERROR_STOP=1 -f /sql/demo_data.sql
```

Каталог `db/` смонтирован в контейнер как `/sql` только для чтения. С хоста,
если установлен psql, то же самое без контейнера:

```bash
psql "postgresql://fsp:ПАРОЛЬ@localhost:5432/fsp" -v ON_ERROR_STOP=1 -f db/schema.sql
```

Подключиться руками:

```bash
docker compose exec postgres psql -U fsp -d fsp
```

Веб-клиент для демо (http://localhost:8081, система PostgreSQL, сервер `postgres`):

```bash
docker compose --profile tools up -d adminer
```

## Как устроен `schema.sql`

Файл **самодостаточен и применим повторно**: он начинается с
`DROP SCHEMA ... CASCADE` по своим шести схемам и создаёт их заново вместе со
справочниками. Поменяли схему — просто накатили файл ещё раз, пересоздавать
том не нужно.

Всё обёрнуто в одну транзакцию. DDL в PostgreSQL транзакционный, поэтому при
любой ошибке посреди файла база остаётся ровно в прежнем состоянии: не будет
«половины схемы». Проверено специально сломанным прогоном.

> [!] `DROP SCHEMA ... CASCADE` сносит данные в этих схемах. Это не «миграция»,
> а пересоздание. Как только появятся данные, которые жалко, схема переезжает
> на Alembic: `alembic stamp head` на развёрнутой базе, дальше ревизиями.

Порядок разделов внутри файла: расширения → схемы → `core` → `ref` → `profile`
→ `assess` → `hire` → `match` → индексы → справочники → банк заданий →
настройка `search_path`.

`demo_data.sql` отдельной транзакцией и рассчитан на чистую схему: повторный
прогон поверх себя упрётся в уникальные ключи, перед ним нужен `schema.sql`.

## Что должен знать бэкенд

**Схемы.** Таблицы не в `public`. В SQLAlchemy указывайте схему явно:

```python
class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = {"schema": "profile"}
```

ENUM-типы тоже живут в схемах своих модулей:
`ENUM(..., schema="hire", name="invitation_status")`.

`schema.sql` прописывает роли `search_path = public, core, ref, profile, assess,
hire, match` — это для psql и ad-hoc запросов. На модели не полагаться: без
явной схемы ORM сгенерирует DDL мимо модуля.

**Никаких триггеров и хранимых функций.** Следствия:

* `updated_at` проставляет приложение при каждом UPDATE;
* `current_category_id` у кандидата обновляет бэкенд одновременно со вставкой в
  `assess.grade_assignments`; источник истины — запись с `is_current = true`;
* кулдаун смены грейда проверяется сравнением `now()` с
  `grade_assignments.next_change_allowed_at` (интервал берётся из
  `ref.grades.cooldown_days`);
* `search_vector` и UUID-дефолты — исключение: это генерируемые колонки и
  встроенные функции PostgreSQL, а не наш код в базе.

**Снятие флага `is_current`.** Партиальный уникальный индекс не даст двум
записям быть текущими одновременно, поэтому смена грейда — одна транзакция:
`UPDATE ... SET is_current = false WHERE candidate_id = ? AND is_current` затем
`INSERT`.

**Контакты кандидата** отдаются только при наличии живой строки в
`hire.contact_disclosures` (`revoked_at IS NULL`). Строка создаётся в той же
транзакции, что и принятие приглашения или создание отклика.

**pgvector.** HNSW-индекс работает, только если вектор для сравнения —
константа, параметр запроса или скалярный подзапрос. Если вектор берётся из
JOIN, планировщик уходит в seq scan:

```sql
-- индекс используется
SELECT candidate_id FROM match.candidate_embeddings
ORDER BY embedding <=> (SELECT embedding FROM match.need_embeddings WHERE need_id = :need)
LIMIT 40;
```

Размерность зафиксирована: `vector(1024)` под multilingual-e5-large / bge-m3.
Другая модель — другая размерность и миграция колонки.

**Другая предметная область.** Чтобы запустить платформу вне ИТ, меняются
только данные: строка в `ref.industries`, свои `specializations`, `grades`,
`skills` и свой банк заданий. DDL не трогается — это проверяется заведённой в
справочниках отраслью «Маркетинг».

## Проверка развёрнутой базы

```bash
docker compose exec postgres psql -U fsp -d fsp -c '\dn'
docker compose exec postgres psql -U fsp -d fsp -c '\dt profile.*'
docker compose exec postgres psql -U fsp -d fsp -c \
  "SELECT count(*) FROM pg_tables WHERE schemaname IN ('core','ref','profile','assess','hire','match')"
```
