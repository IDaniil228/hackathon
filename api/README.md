<picture><source media="(prefers-color-scheme: dark)" srcset="../brand/assets/logo/logo-short-on-dark.svg"><img src="../brand/assets/logo/logo-short-on-light.svg" width="120" alt="ФСП"></picture>

# API Сцепки

[![assets](https://img.shields.io/badge/docs-assets-555?style=flat)](../brand/ASSETS.md)

API связывает кабинеты кандидата и работодателя с сервисами тестирования и подбора. Кандидат управляет профилем и отвечает на предложения, работодатель описывает потребность и приглашает выбранных специалистов

![Предложение и переписка](../docs/frontend/screenshots/conversation-light.png)

- [Запуск и проверка](../docs/api/deployment.md)
- [Контракт для фронтенда](../docs/api/frontend.md)
- [Архитектура и ограничения](../docs/api/architecture.md)
- [Результаты проверки](../docs/api/validation.md)
- [Предметная схема](../docs/Структура%20БД.md)
- [Материалы оформления](../brand/ASSETS.md)
- [Интерфейс платформы](../frontend/README.md)
- [Наполнение через Faker](../docs/frontend/development.md#демоданные)

Запущенное приложение публикует Swagger по `/docs` и спецификацию по `/openapi.json`. Файл спецификации генерируется из приложения командой `uv run python -m app.export_api --output ../docs/api/openapi.json` из каталога `api/`
