-- Роль matcher и её права в базе api на общем сервере Postgres из db/.
-- Запускает суперпользователь в контейнере matcher-db-init при каждом старте, поэтому
-- скрипт идемпотентен. Запускать после миграций api: гранты на существующие таблицы
-- выдаются здесь, на будущие — правилами по умолчанию для роли-владельца таблиц api.
-- Переменные psql: role, password, db — база api, owner — роль, которая создаёт таблицы api
-- своими миграциями. Таблицу api, созданную другой ролью, правило по умолчанию не покрывает.

SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec

ALTER ROLE :"role" WITH LOGIN PASSWORD :'password';

\c :"db"

GRANT CONNECT ON DATABASE :"db" TO :"role";

-- Данные api матчер только читает. Справочники открыты целиком, включая будущие таблицы.
GRANT USAGE ON SCHEMA ref, profile, assess, hire TO :"role";
GRANT SELECT ON ALL TABLES IN SCHEMA ref TO :"role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA ref GRANT SELECT ON TABLES TO :"role";

-- В profile, assess и hire лежат персональные данные: контакты, тексты резюме, токены.
-- Поэтому здесь явный список того, что читает код сервиса, а не вся схема. Сначала права
-- отзываются, чтобы повторный запуск задавал полное состояние, а не добавлял к старому.
REVOKE SELECT ON ALL TABLES IN SCHEMA profile, assess, hire FROM :"role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA profile, assess, hire
    REVOKE SELECT ON TABLES FROM :"role";
GRANT SELECT (user_id, city, relocation, work_format, experience_months, about,
              current_category_id,
              is_open_to_offers, profile_strength, profile_refreshed_at, created_at, updated_at)
    ON profile.candidates TO :"role";
GRANT SELECT ON profile.candidate_preferences, profile.candidate_privacy,
                profile.candidate_skills, profile.fsp_achievements, profile.fsp_events,
                profile.github_repos,
                assess.grade_assignments,
                hire.employer_needs, hire.need_skills, hire.vacancies
    TO :"role";

-- Пишет он только в match: прогоны, выдачу, векторы.
GRANT USAGE ON SCHEMA match TO :"role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA match TO :"role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA match TO :"role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA match
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA match
    GRANT USAGE, SELECT ON SEQUENCES TO :"role";

-- Свои таблицы сервиса и таблица версий его alembic.
CREATE SCHEMA IF NOT EXISTS matcher AUTHORIZATION :"role";
