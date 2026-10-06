---
name: backend-dockerfile-review-essay
description: >-
  Ревью Dockerfile для Django-приложения: кэш слоёв и порядок COPY, секреты в образе,
  запуск от root, runserver вместо WSGI-сервера, тег latest и .dockerignore
area: backend
grade: junior
skills: [docker, django, security]
kind: essay
difficulty: 3
expected_minutes: 20
params:
  domain:
    type: choice
    choices:
      - сервис записи к врачу
      - API школьного электронного дневника
      - бэкенд небольшого интернет-магазина цветов
      - сервис бронирования столиков в ресторанах
---

# Ревью Dockerfile

## Генерация

Придумай короткую легенду: команда делает {domain} и перед первым выкатом просит коллегу проверить Dockerfile. Сам Dockerfile покажут кандидату отдельно после твоего текста: не пиши его, не пересказывай его строки и не оценивай его. Опиши команду, сервис и содержимое корня репозитория.

## Вопросы

Dockerfile, который команда прислала на проверку:

```dockerfile
FROM python:latest

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

ENV DJANGO_SECRET_KEY="dj-insecure-7f3k2a9q"
ENV DATABASE_PASSWORD="Pa55word!"
ENV DJANGO_SETTINGS_MODULE=config.settings

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
```

1. Найдите проблемы этого Dockerfile и объясните, чем опасна каждая из них.
2. Пришлите исправленный Dockerfile и всё, что ещё нужно добавить в репозиторий.

## Факты

- django: сервис написан на Django
- production: команда собирается выкатить образ из этого Dockerfile в продакшен и просит проверить его перед выкатом
- repo-files: в корне репозитория рядом с Dockerfile лежат каталог .git, файл .env и локальная база db.sqlite3
- no-dockerignore: в тексте прямо сказано, что файла .dockerignore в репозитории нет

## Нельзя раскрывать

- кэш
- секрет
- root
- непривилегирован
- gunicorn
- uvicorn
- slim
- docker history
- воспроизводим

## Рубрика

- layer-cache: Объяснено, что слои кэшируются по порядку, и `requirements.txt` нужно копировать и устанавливать до копирования остального кода
  + `COPY requirements.txt .` → `RUN pip install --no-cache-dir -r requirements.txt` → `COPY . .`
  - "кэш не важен, сборка всё равно быстрая"
- secrets: Секреты убраны из образа: они видны в слоях и в `docker history`, их передают при запуске через переменные окружения оркестратора, secrets или файл вне образа
  - перенос секрета из `ENV` в `ARG`, который тоже остаётся в истории сборки
- non-root: Добавлен непривилегированный пользователь и `USER`, объяснено, что root в контейнере повышает ущерб при взломе приложения
- wsgi-server: `runserver` заменён на WSGI- или ASGI-сервер (gunicorn, uvicorn) с объяснением, что встроенный сервер Django не предназначен для продакшена
  + `CMD ["gunicorn", "project.wsgi:application", "--bind", "0.0.0.0:8000"]`
- pinned-base: Базовый образ зафиксирован по версии, а лучше slim-вариант, например `python:3.12-slim`; объяснено, что `latest` делает сборку невоспроизводимой
- dockerignore: Добавлен `.dockerignore` с `.git`, `.env`, `db.sqlite3`, `__pycache__`, и объяснено, что без него секреты и мусор попадают в контекст сборки и в образ
- fixed-dockerfile: Приведён исправленный Dockerfile, в котором устранены все найденные проблемы и нет новых
