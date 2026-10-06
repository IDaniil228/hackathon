# Слой хранения

PostgreSQL 18 + pgvector. Схема и справочники разворачиваются автоматически при
первом старте контейнера.

## Запуск

```bash
cp .env.example .env     # пароль поменять
docker compose up -d postgres
```

Готовность: `docker compose ps` — статус `healthy`. Подключение:

```bash
docker compose exec postgres psql -U fsp -d fsp
```

Веб-клиент для демо (http://localhost:8081, система PostgreSQL, сервер `postgres`):

```bash
docker compose --profile tools up -d adminer
```

## Как устроен деплой схемы

Файлы `db/init/*.sql` монтируются в `/docker-entrypoint-initdb.d` и выполняются
по возрастанию имени **только при пустом томе**:

| Файл | Содержимое |
|---|---|
| `00_extensions.sql` | `vector`, `citext`, `pg_trgm` |
| `01_schemas.sql` | схемы `core`, `ref`, `profile`, `assess`, `hire`, `match` |
| `10_core.sql` | пользователи, токены, сессии, согласия 152-ФЗ |
| `20_ref.sql` | справочники предметной области и описание EAV-атрибутов |
| `30_profile.sql` | анкета кандидата, резюме, ФСП, GitHub |
| `40_assess.sql` | опрос, банк заданий, тестирование, история грейдов |
| `50_hire.sql` | компании, вакансии, потребности, приглашения, отклики |
| `60_match.sql` | эмбеддинги, подборки, объяснения, чат |
| `70_indexes.sql` | индексы |
| `90_seed_ref.sql` | справочники: отрасли, специализации, грейды, навыки |
| `91_seed_task_bank.sql` | банк шаблонов заданий (сменная часть системы) |
| `95_seed_demo.sql` | демо-данные под сквозной сценарий |

Изменили схему — пересоздайте том:

```bash
docker compose down -v && docker compose up -d postgres
```

> Том именованный (`fsp-pgdata`), `down` без `-v` его не трогает. `down -v`
> удаляет данные безвозвратно.

Когда разработка стабилизируется, поверх этой схемы ставится Alembic:
`alembic stamp head` на развёрнутой базе, дальше изменения идут ревизиями.

## Что должен знать бэкенд

**Схемы.** Таблицы не лежат в `public`. В SQLAlchemy указывайте схему явно:

```python
class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = {"schema": "profile"}
```

ENUM-типы тоже живут в схемах своих модулей: `ENUM(..., schema="hire", name="invitation_status")`.

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
`skills` и собственный файл банка заданий вместо `91_seed_task_bank.sql`.
DDL при этом не трогается — это проверяется заведённой в сидах отраслью
«Маркетинг».

## Проверка развёрнутой базы

```bash
docker compose exec postgres psql -U fsp -d fsp -c '\dn'
docker compose exec postgres psql -U fsp -d fsp -c '\dt profile.*'
docker compose logs postgres | grep -i error    # пусто после чистого старта
```
