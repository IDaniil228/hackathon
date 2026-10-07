# Сборка, развёртывание и локальный запуск taskgen

Все команды выполняются из каталога `taskgen/` репозитория. Состав контейнеров и порты задаёт [docker-compose.yml](../../taskgen/docker-compose.yml), настройки сервиса и их переменные окружения — [generated/settings.md](generated/settings.md), версии компонентов — [generated/libraries.md](generated/libraries.md)

## Что нужно

- Linux с cgroup v2 и Docker с Compose v2. Песочница Piston изолирует запуски через namespaces и cgroup, поэтому её контейнер работает в режиме `privileged`
- [uv](https://docs.astral.sh/uv/) и curl на хосте: на них работают скрипт установки пакетов песочницы и команды разработки
- OpenAI-совместимый провайдер LLM: Ollama на хосте, Ollama в контейнере из профиля `ollama` или облачный шлюз. Нужны три модели — генератор, судья и модель эмбеддингов, их имена задают настройки группы `llm`
- Свободные порты, которые публикует `docker-compose.yml`

## 1. Ключ администратора и модель

Создайте `taskgen/.env`: его читают и сервис в контейнере, и сервис, запущенный на хосте. Файл в `.gitignore`, ключ не попадёт в репозиторий:

```sh
cd taskgen
echo "TASKGEN_BOOTSTRAP_ADMIN_KEY=$(openssl rand -hex 32)" > .env
```

Этот ключ — первый ключ роли `admin`. Остальные ключи выпускает `POST /keys`, и сервис хранит их только хэшем

Модель по умолчанию — Ollama на хосте, сервис в контейнере обращается к ней через `host.docker.internal`. Ollama по умолчанию слушает только `127.0.0.1`, и из контейнера её не видно: запустите её с `OLLAMA_HOST=0.0.0.0:11434` и разрешите этот порт в межсетевом экране для сети Docker. Модели по умолчанию загружаются так:

```sh
ollama pull qwen3.5:9b
ollama pull qwen3.5:4b
ollama pull bge-m3
```

Ollama в контейнере вместо хоста поднимает профиль `ollama`. Тогда адрес модели — имя сервиса в сети Compose, а модели загружаются внутри контейнера:

```sh
echo "TASKGEN_LLM__BASE_URL=http://ollama:11434/v1" >> .env
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull qwen3.5:9b
```

Модели с рассуждениями на слабой машине могут истратить весь ответ на рассуждения и вернуть пустой текст. При проверке этой инструкции qwen3.5:9b с настройками по умолчанию не написал условие первого задания ни с одной попытки, и `POST /sessions` ответил 502; с `TASKGEN_LLM__GENERATOR_REASONING_EFFORT=none` та же сессия создалась за 18 секунд. Для судьи и проверяльщика эссе есть такие же настройки `TASKGEN_LLM__JUDGE_REASONING_EFFORT` и `TASKGEN_LLM__CHECKER_REASONING_EFFORT`

Окно контекста Ollama задаёт сервер, а не запрос: OpenAI-совместимый API Ollama не принимает `num_ctx`. При малой видеопамяти Ollama 0.32 берёт окно 4096 токенов. Запрос судьи к заданию с кодом — условие, рубрика, метрики анализаторов и сам код — занимал на проверке 3500–4000 токенов, и ответ обрывался на 4096 с `finish_reason: length`. Обрезанный JSON не проходит схему, а если так обрываются все сэмплы, сессия не оценивается и `POST /sessions/{id}/score` отвечает 502. С окном 8192 те же запросы разобрались все. Поэтому Ollama для сервиса запускается с `OLLAMA_CONTEXT_LENGTH=16384`, профиль `ollama` в `docker-compose.yml` уже задаёт это значение

Облачный провайдер подключается теми же настройками, без правки кода: `TASKGEN_LLM__BASE_URL` — адрес его OpenAI-совместимого API, `TASKGEN_LLM__API_KEY` — ключ, `TASKGEN_LLM__GENERATOR_MODEL`, `TASKGEN_LLM__JUDGE_MODEL` и `TASKGEN_LLM__EMBEDDING_MODEL` — имена моделей. Провайдер должен отдавать `/chat/completions` с `response_format` типа `json_schema` и `/embeddings`. YandexGPT и GigaChat с сервисом не проверялись: поддерживают ли их OpenAI-совместимые шлюзы оба эндпоинта и структурный ответ, нужно уточнить в документации провайдера. Облачному провайдеру можно поднять `TASKGEN_LLM__CONCURRENCY`

## 2. Контейнеры

```sh
docker compose up -d --build
```

Поднимаются Postgres с pgvector, Piston и сервис. Сервис при старте применяет миграции (`alembic upgrade head`), они же создают справочники областей, грейдов и навыков

## 3. Пакеты песочницы

Свежий Piston не умеет ни одного языка. Скрипт ставит пакеты всех окружений из реестра: официальные — через API Piston, локальный `python-ml` с NumPy, scikit-learn и PyTorch — сборкой из [sandbox/packages](../../taskgen/sandbox/packages). Первая сборка `python-ml` скачивает около гигабайта и идёт несколько минут; архив кэшируется и при повторной установке не собирается:

```sh
sandbox/piston-packages.sh install
```

По умолчанию скрипт ищет контейнер `taskgen-piston-1` и Piston на `localhost:2000`; для другого имени проекта Compose или порта есть флаги `-c` и `-u`, справка — `sandbox/piston-packages.sh --help`. Скрипт перезапускает контейнер Piston, потому что Piston читает локальные пакеты только при старте

## 4. Банк шаблонов

```sh
docker compose exec taskgen taskgen import seed/templates
```

Импорт проверяет все шаблоны и пишет их одной транзакцией: при любой ошибке не пишется ничего, а ошибки всех файлов выводятся сразу. Затем считаются эмбеддинги описаний. Если модель эмбеддингов недоступна, импорт всё равно проходит, а отбор шаблонов идёт по доле общих навыков; эмбеддинги досчитает следующий импорт. Ключ `--no-embeddings` пропускает их сразу. Тот же банк загружается через API: `POST /templates/bulk` с ключом `admin`

## 5. Проверка

```sh
curl localhost:8000/health
```

Ответ `{"status":"ok"}` значит, что сервис запущен. Swagger UI открывается по адресу `http://localhost:8000/docs`. Ключ роли `client` для основного бэкенда выпускает admin:

```sh
curl -X POST localhost:8000/keys \
  -H "Authorization: Bearer $(sed -n 's/^TASKGEN_BOOTSTRAP_ADMIN_KEY=//p' .env)" \
  -H 'Content-Type: application/json' \
  -d '{"name": "backend", "role": "client"}'
```

Ключ из ответа показывается один раз. С ним `GET /templates` возвращает метаданные банка. Перед первой сессией проверьте, что модель доступна из контейнера сервиса: при недоступной модели `POST /sessions` ждёт таймаута запроса к ней на каждой попытке и только потом отвечает ошибкой 502:

```sh
docker compose exec taskgen python -c \
  "import httpx; print(httpx.get('http://host.docker.internal:11434/v1/models', timeout=5).status_code)"
```

## Запуск сервиса на хосте

Для разработки сервис запускается из исходников, а Postgres и Piston — в контейнерах:

```sh
docker compose up -d db piston
sandbox/piston-packages.sh install
uv sync
uv run alembic upgrade head
uv run taskgen import seed/templates
uv run taskgen serve
```

Сервис на хосте берёт настройки из переменных окружения и из `.env` в рабочем каталоге; значения по умолчанию указывают на `localhost`, так что Ollama на хосте доступна без `OLLAMA_HOST`. При старте сервис подхватывает незавершённые сессии и пополняет пул готовых экземпляров: на слабой машине пополнение надолго занимает модель, `TASKGEN_GENERATION__POOL_SIZE=0` его выключает. Сервис рассчитан на один процесс на одну БД: не запускайте его одновременно на хосте и в контейнере с общей базой

## Тесты и линтеры

```sh
uv run pytest -q -m "not integration"
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

Первая команда не требует ничего, кроме Python. Полный прогон требует Postgres и Piston с пакетами: адрес БД задаёт `TASKGEN_DATABASE_URL`, адрес Piston — `TASKGEN_SANDBOX__PISTON_URL`. Тесты с настоящей моделью включает `TASKGEN_LLM_SMOKE=1`. Справочники документации пересобирает `uv run python scripts/gen_docs.py`, и тест `tests/test_docs_generated.py` падает, если их забыли пересобрать

В CI линтеры и тесты без метки `integration` запускает [taskgen.yml](../../.github/workflows/taskgen.yml), а тесты с меткой `integration` — [taskgen-integration.yml](../../.github/workflows/taskgen-integration.yml). Когда запускается каждый из них и почему, сказано в заголовке файла

## Ограничения образа

- Образ сервиса не ставит clang-tidy и cppcheck: метрики качества C++ из этих инструментов в контейнере не измеряются, и их вес переходит к остальным компонентам балла. lizard ставится как зависимость Python
- Образы Piston и Ollama, базовый образ uv в `Dockerfile` не закреплены тегом версии и берутся как `latest`
