---
name: llm-provider-compat
description: Российские LLM подключаются через OpenAI-совместимый клиент, но structured output у них может не работать
metadata:
  type: reference
---

YandexGPT отдаёт OpenAI-совместимый API на `https://ai.api.cloud.yandex.net/v1`, совместимость с Completions заявлена частичная. GigaChat совместим напрямую или через прокси `gpt2giga` от Сбера. Источники: документация Yandex Cloud (ai-studio/concepts/openai-compatibility) и developers.sber.ru (gigachat/guides/compatible-openai)

**Why:** сервис taskgen ходит к LLM одним клиентом с `response_format: json_schema`, а на проде ожидаются российские модели

**How to apply:** у генератора и судьи должен быть запасной разбор JSON из обычного текста ответа на случай, если провайдер не поддерживает json_schema. Связано с [[taskgen-decisions]]
