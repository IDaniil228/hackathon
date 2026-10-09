-- Роль, база и расширение vector для taskgen на общем сервере Postgres из db/.
-- Запускает суперпользователь в контейнере taskgen-db-init при каждом старте, поэтому
-- скрипт идемпотентен. Расширение создаётся здесь: в pgvector оно не помечено trusted,
-- и владелец базы без прав суперпользователя создать его не может.
-- Переменные psql: role, password, db.

SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec

ALTER ROLE :"role" WITH LOGIN PASSWORD :'password';

SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db') \gexec

\c :"db"
CREATE EXTENSION IF NOT EXISTS vector;
