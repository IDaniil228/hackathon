# Настройки сервиса matcher

> [!NOTE]
> Файл собран командой `uv run python scripts/gen_docs.py` из каталога `matcher/`. Не правьте его руками: правьте источник и пересоберите файл. Источник — модель `Settings` в [`config.py`](../../../matcher/src/matcher/config.py) и общая часть настроек LLM в [`llm_config.py`](../../../matcher/src/matcher/llm/llm_config.py), копии taskgen

Каждое поле задаётся переменной окружения из таблицы или строкой в файле `.env` в рабочем каталоге процесса. Вложенное поле отделяется `__`, регистр имени переменной не важен

## Общие

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `database_url` | `MATCHER_DATABASE_URL` | string | `"postgresql+asyncpg://matcher:matcher@localhost:5432/fsp"` | Адрес базы api в формате SQLAlchemy asyncpg под ролью matcher: чтение ref, profile, assess и hire, запись в match, свои таблицы в схеме matcher |
| `bootstrap_admin_key` | `MATCHER_BOOTSTRAP_ADMIN_KEY` | string или null | `null` | Первый admin-ключ; остальные ключи создаются через API и хранятся хэшами |

## llm

Провайдеры моделей и роли матчера. Роль называет модель строкой провайдер/модель. Модели по умолчанию нет: каждый вызов матчера называет модель своей роли

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `llm.providers` | `MATCHER_LLM__PROVIDERS` | словарь `имя` → объект |  | Провайдеры по имени. Поле провайдера из окружения дополняет провайдера по умолчанию, а новое имя добавляет провайдера, и тогда base_url обязателен |
| `llm.providers.ollama.base_url` | `MATCHER_LLM__PROVIDERS__OLLAMA__BASE_URL` | string | `"http://localhost:11434/v1"` | Адрес OpenAI-совместимого API: /chat/completions и /embeddings |
| `llm.providers.ollama.api_key` | `MATCHER_LLM__PROVIDERS__OLLAMA__API_KEY` | string (password) или null | `null` | Ключ провайдера, уходит в заголовке Authorization: Bearer. Задаётся только переменной окружения и не попадает ни в журнал, ни в документацию; null — заголовок не передаётся |
| `llm.providers.ollama.requires_key` | `MATCHER_LLM__PROVIDERS__OLLAMA__REQUIRES_KEY` | boolean | `false` | Провайдеру нужен ключ. Без ключа сервис всё равно стартует: он пишет предупреждение в журнал, а вызов роли на этом провайдере завершается ошибкой с именем переменной ключа и не уходит в сеть |
| `llm.providers.ollama.timeout_seconds` | `MATCHER_LLM__PROVIDERS__OLLAMA__TIMEOUT_SECONDS` | number, > 0 | `120.0` | Таймаут одного HTTP-запроса к провайдеру, секунды |
| `llm.providers.ollama.concurrency` | `MATCHER_LLM__PROVIDERS__OLLAMA__CONCURRENCY` | integer, ≥ 1 | `1` | Сколько запросов к провайдеру идут одновременно, общий лимит генерации и оценки на этом провайдере. Локальная модель отвечает по очереди; облачному провайдеру можно больше |
| `llm.providers.ollama.structured_output` | `MATCHER_LLM__PROVIDERS__OLLAMA__STRUCTURED_OUTPUT` | одно из `"json_schema"`, `"json_object"`, `"text"` | `"json_schema"` | Как просить у провайдера ответ по схеме. json_schema — response_format со схемой, а при отказе 4xx или ответе не по схеме — повтор со схемой в тексте запроса. json_object и text — сразу схема в тексте запроса и одна попытка; json_object добавляет response_format {"type": "json_object"}, и провайдер отвечает только валидным JSON. DeepSeek отвечает 400 на json_schema, а json_object принимает |
| `llm.providers.deepseek.base_url` | `MATCHER_LLM__PROVIDERS__DEEPSEEK__BASE_URL` | string | `"https://api.deepseek.com"` | Адрес OpenAI-совместимого API: /chat/completions и /embeddings |
| `llm.providers.deepseek.api_key` | `MATCHER_LLM__PROVIDERS__DEEPSEEK__API_KEY` | string (password) или null | `null` | Ключ провайдера, уходит в заголовке Authorization: Bearer. Задаётся только переменной окружения и не попадает ни в журнал, ни в документацию; null — заголовок не передаётся |
| `llm.providers.deepseek.requires_key` | `MATCHER_LLM__PROVIDERS__DEEPSEEK__REQUIRES_KEY` | boolean | `true` | Провайдеру нужен ключ. Без ключа сервис всё равно стартует: он пишет предупреждение в журнал, а вызов роли на этом провайдере завершается ошибкой с именем переменной ключа и не уходит в сеть |
| `llm.providers.deepseek.timeout_seconds` | `MATCHER_LLM__PROVIDERS__DEEPSEEK__TIMEOUT_SECONDS` | number, > 0 | `120.0` | Таймаут одного HTTP-запроса к провайдеру, секунды |
| `llm.providers.deepseek.concurrency` | `MATCHER_LLM__PROVIDERS__DEEPSEEK__CONCURRENCY` | integer, ≥ 1 | `4` | Сколько запросов к провайдеру идут одновременно, общий лимит генерации и оценки на этом провайдере. Локальная модель отвечает по очереди; облачному провайдеру можно больше |
| `llm.providers.deepseek.structured_output` | `MATCHER_LLM__PROVIDERS__DEEPSEEK__STRUCTURED_OUTPUT` | одно из `"json_schema"`, `"json_object"`, `"text"` | `"json_object"` | Как просить у провайдера ответ по схеме. json_schema — response_format со схемой, а при отказе 4xx или ответе не по схеме — повтор со схемой в тексте запроса. json_object и text — сразу схема в тексте запроса и одна попытка; json_object добавляет response_format {"type": "json_object"}, и провайдер отвечает только валидным JSON. DeepSeek отвечает 400 на json_schema, а json_object принимает |
| `llm.rewriter` | `MATCHER_LLM__REWRITER` | `ChatRole` |  | Переписывает потребность в карточку идеального кандидата по шаблону карточки (HyDE); вектор этой карточки ищет кандидатов |
| `llm.rewriter.model` | `MATCHER_LLM__REWRITER__MODEL` | string | `"ollama/hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:q4_K_M"` | Модель в форме провайдер/модель: часть до первого / — имя провайдера из llm.providers, остальное — имя модели у провайдера, и в нём бывают свои / |
| `llm.rewriter.reasoning_effort` | `MATCHER_LLM__REWRITER__REASONING_EFFORT` | одно из `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"` или null | `"none"` | Поле reasoning_effort запроса к модели роли; null — поле не передаётся, и модель рассуждает как обычно. "none" сокращает ответ в разы: рассуждение qwen3.5:4b на простой запрос занимало тысячи токенов и иногда не оставляло места самому ответу. Модель без рассуждений, как GigaChat 3.1 в Ollama, принимает только "none", а на другое значение отвечает HTTP 400 "does not support thinking" |
| `llm.ranker` | `MATCHER_LLM__RANKER` | `ChatRole` |  | Точечно оценивает кандидатов короткого списка: потребность с негативным промтом, карточка, machine summary, негативный промт кандидата и строки факторов |
| `llm.ranker.model` | `MATCHER_LLM__RANKER__MODEL` | string | `"deepseek/deepseek-flash"` | Модель в форме провайдер/модель: часть до первого / — имя провайдера из llm.providers, остальное — имя модели у провайдера, и в нём бывают свои / |
| `llm.ranker.reasoning_effort` | `MATCHER_LLM__RANKER__REASONING_EFFORT` | одно из `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"` или null | `"none"` | Поле reasoning_effort запроса к модели роли; null — поле не передаётся, и модель рассуждает как обычно. "none" сокращает ответ в разы: рассуждение qwen3.5:4b на простой запрос занимало тысячи токенов и иногда не оставляло места самому ответу. Модель без рассуждений, как GigaChat 3.1 в Ollama, принимает только "none", а на другое значение отвечает HTTP 400 "does not support thinking" |
| `llm.checker` | `MATCHER_LLM__CHECKER` | `ChatRole` |  | Сверяет объяснение выдачи и обзор прогона со строками факторов |
| `llm.checker.model` | `MATCHER_LLM__CHECKER__MODEL` | string | `"deepseek/deepseek-flash"` | Модель в форме провайдер/модель: часть до первого / — имя провайдера из llm.providers, остальное — имя модели у провайдера, и в нём бывают свои / |
| `llm.checker.reasoning_effort` | `MATCHER_LLM__CHECKER__REASONING_EFFORT` | одно из `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"` или null | `"none"` | Поле reasoning_effort запроса к модели роли; null — поле не передаётся, и модель рассуждает как обычно. "none" сокращает ответ в разы: рассуждение qwen3.5:4b на простой запрос занимало тысячи токенов и иногда не оставляло места самому ответу. Модель без рассуждений, как GigaChat 3.1 в Ollama, принимает только "none", а на другое значение отвечает HTTP 400 "does not support thinking" |
| `llm.embedding` | `MATCHER_LLM__EMBEDDING` | `ModelRole` |  | Модель эмбеддингов карточек кандидатов и потребностей. Векторы в БД помечены именем модели без провайдера, как в taskgen: смена провайдера той же модели не требует пересчёта. Размерность вектора задана типом колонки в схеме api (match.candidate_embeddings), и сервис сверяет её с моделью при старте |
| `llm.embedding.model` | `MATCHER_LLM__EMBEDDING__MODEL` | string | `"ollama/bge-m3"` | Модель в форме провайдер/модель: часть до первого / — имя провайдера из llm.providers, остальное — имя модели у провайдера, и в нём бывают свои / |

## evidence

Чем модель подтверждает вердикт: один способ для судьи ответа и для LLM-проверяльщика ситуации эссе, кейса задачки, темы легенды и machine summary. Механика — taskgen/evaluation/evidence.py

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `evidence.mode` | `MATCHER_EVIDENCE__MODE` | одно из `"quotes"`, `"spans"` | `"spans"` | Способ доказательства: quotes — дословные цитаты текста, spans — номера его участков, которые модели показаны с номерами: строк кода или предложений. Судья так подтверждает вердикты yes и partial, проверяльщик — найденный факт, запрещённое понятие, тему или предложение machine summary; у summary участок — строка фактов с путём и значением. spans по умолчанию: судья и проверяльщик по умолчанию — deepseek-flash, и на нём ссылки не уступают цитатам. GigaChat с цитатами терял больше половины верных вердиктов судьи, но со ссылками засчитывает и самоутверждения кандидата. Замеры — docs/taskgen/validation.md |
| `evidence.repairs` | `MATCHER_EVIDENCE__REPAIRS` | integer, ≥ 0 | `2` | Сколько раз переспросить модель с перечнем ошибок, если её ответ не по схеме, без вердикта по пункту или ссылается на текст, которого нет: цитату не дословно или номер участка, которого нет. Каждый повтор — ещё один вызов модели. 0 выключает повторы |
| `evidence.max_spans` | `MATCHER_EVIDENCE__MAX_SPANS` | integer, ≥ 1 или null | `null` | Сколько участков самое большее подтверждают один вердикт в режиме spans; вердикт с большим числом участков не засчитывается. Ссылка на весь текст не доказывает ничего и обходит проверку смысла цитат. null — без предела, и по умолчанию предела нет: на наборе ложных срабатываний судьи честные ссылки доходили до 8 участков, а атаки до 5, и число не отделяет одни от других |

## vector

Векторная стадия: косинусная близость карточки запроса к карточкам кандидатов пула

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `vector.top_k` | `MATCHER_VECTOR__TOP_K` | integer, ≥ 1 | `30` | Сколько ближайших кандидатов пула идут дальше. Вектор отвечает за полноту, а порядок задают следующие стадии. Если пул после префильтра не больше top_k, векторная стадия пропускается и в params прогона пишется vector_skipped |

## exploration

Разведка: малая фиксированная доля профилей вне векторного top_k в коротком списке. Она даёт шанс новым профилям, у которых ещё нет истории, и показывает в валидации, что вектор отсекает. Случайный выбор воспроизводим: генератор берёт seed от id прогона

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `exploration.fresh` | `MATCHER_EXPLORATION__FRESH` | integer, ≥ 0 | `3` | Сколько самых новых профилей пула вне top_k добавить |
| `exploration.random` | `MATCHER_EXPLORATION__RANDOM` | integer, ≥ 0 | `3` | Сколько случайных профилей из оставшихся вне top_k добавить |

## llm_stage

LLM-стадия: HyDE-карточка запроса и точечная оценка короткого списка

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `llm_stage.enabled` | `MATCHER_LLM_STAGE__ENABLED` | boolean | `false` | Включает LLM-стадию. Выключенная стадия даёт порядок по факторам и объяснения по шаблону из строк факторов |
| `llm_stage.batch_size` | `MATCHER_LLM_STAGE__BATCH_SIZE` | integer, ≥ 1 | `8` | Сколько кандидатов оценивает один вызов ранжировщика |

## explanation

Объяснение строки выдачи для работодателя

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `explanation.rewrites` | `MATCHER_EXPLANATION__REWRITES` | integer, ≥ 0 | `2` | Сколько раз LLM переписывает объяснение, которое отклонили барьер или проверяльщик; после этого остаётся объяснение по шаблону из строк факторов |

## taskgen

Сервис taskgen, источник machine summary кандидата для его карточки

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `taskgen.base_url` | `MATCHER_TASKGEN__BASE_URL` | string или null | `null` | Адрес taskgen, например http://taskgen:8000; null — карточки без machine summary. candidate_ref в taskgen равен profile.candidates.user_id |
| `taskgen.api_key` | `MATCHER_TASKGEN__API_KEY` | string (password) или null | `null` | Ключ роли client в taskgen, уходит в Authorization: Bearer. Задаётся только переменной окружения |
| `taskgen.timeout_seconds` | `MATCHER_TASKGEN__TIMEOUT_SECONDS` | number, > 0 | `10.0` | Таймаут одного запроса к taskgen, секунды |
| `taskgen.concurrency` | `MATCHER_TASKGEN__CONCURRENCY` | integer, ≥ 1 | `8` | Сколько запросов summary к taskgen идут одновременно |

## embeddings

Кэш векторов карточек в match.candidate_embeddings и match.need_embeddings

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `embeddings.batch_size` | `MATCHER_EMBEDDINGS__BATCH_SIZE` | integer, ≥ 1 | `32` | Сколько карточек уходит в один запрос к эмбеддеру |
| `embeddings.startup_check_seconds` | `MATCHER_EMBEDDINGS__STARTUP_CHECK_SECONDS` | number, > 0 | `10.0` | Сколько секунд старт ждёт пробный вектор для сверки размерности. Таймаут провайдера рассчитан на генерацию и намного длиннее; без своего предела недоступный эмбеддер держал бы старт, и healthcheck считал бы сервис мёртвым |

## results

Что прогон пишет в match.search_results

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `results.store_pool` | `MATCHER_RESULTS__STORE_POOL` | boolean | `true` | Писать и кандидатов пула, которые не прошли векторную стадию, со stage prefilter и vector_score, без ранга. Это след для валидации полноты вектора |
