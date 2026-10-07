# taskgen

Сервис платформы ФСП, который проверяет ИТ-кандидата тестом на заявленный грейд. Он собирает каждому кандидату свой набор заданий, проверяет задания до выдачи, оценивает ответы и присваивает грейд

## Что умеет

- Отбирает задания из банка шаблонов под область, грейд, навыки и резюме кандидата и объясняет работодателю, почему выпали именно они
- Генерирует из шаблона уникальный экземпляр: данные и тесты выбирает код, LLM пишет только текст условия, а задание с кодом выдаётся лишь после того, как эталонное решение прошло все его тесты в песочнице
- Даёт задания с кодом на Python, C++ и Bash, задачи машинного обучения с NumPy, scikit-learn и PyTorch и развёрнутые ответы
- Оценивает код скрытыми тестами, статическими анализаторами и LLM-судьёй по рубрике с проверкой доказательств, а грейд — по уровням
- Пишет machine summary для рекрутера: только факты из результатов, без выводов о честности кандидата
- Работает с любым OpenAI-совместимым провайдером LLM, от локальной Ollama до облачного шлюза

Как это устроено и почему задания трудно распространить между кандидатами — в [документации сервиса](../docs/taskgen/README.md)

## Запуск

Нужны Docker с Compose и [uv](https://docs.astral.sh/uv/)

```sh
cd taskgen
echo "TASKGEN_BOOTSTRAP_ADMIN_KEY=$(openssl rand -hex 32)" > .env
docker compose up -d --build
sandbox/piston-packages.sh install
docker compose exec taskgen taskgen import seed/templates
```

Модель LLM, ключи и проверка запуска — в [инструкции по развёртыванию](../docs/taskgen/deployment.md). Запущенный сервис отдаёт Swagger UI по `/docs`

## Разработка

```sh
cd taskgen
uv sync
uv run pytest -q -m "not integration"
uv run ruff check . && uv run ruff format --check .
```

Тесты с меткой `integration` нужны Postgres и Piston из `docker-compose.yml`. Справочники API, настроек, библиотек и банка шаблонов в `docs/taskgen/generated` собирает `uv run python scripts/gen_docs.py`; формат шаблона задания — [TEMPLATE_FORMAT.md](TEMPLATE_FORMAT.md)
