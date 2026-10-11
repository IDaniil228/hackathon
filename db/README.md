<picture><source media="(prefers-color-scheme: dark)" srcset="../brand/assets/logo/logo-short-on-dark.svg"><img src="../brand/assets/logo/logo-short-on-light.svg" width="120" alt="ФСП"></picture>

# Хранение данных Сцепки

[![assets](https://img.shields.io/badge/docs-assets-555?style=flat)](../brand/ASSETS.md)

PostgreSQL с pgvector хранит аккаунты, профили, подтверждения, найм и результаты подбора. Миграции разворачивают предметную схему, а роли сервисов ограничивают доступ к данным

- [Устройство и обслуживание базы](../docs/database.md)
- [Предметные таблицы и связи](../docs/Структура%20БД.md)
- [Запуск API и миграций](../docs/api/deployment.md)
- [Синтетические данные через Faker](../docs/frontend/development.md#демоданные)
- [Работа сервисов подбора и тестирования](../README.md#компоненты)

Файлы `backup/` — производные выгрузки для просмотра и переноса данных. Источник рабочей схемы — модели и миграции API
