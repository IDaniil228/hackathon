# Настройки сервиса taskgen

> [!NOTE]
> Файл собран командой `uv run python scripts/gen_docs.py` из каталога `taskgen/`. Не правьте его руками: правьте источник и пересоберите файл. Источник — модель `Settings` в [`config.py`](../../../taskgen/src/taskgen/config.py)

Каждое поле задаётся переменной окружения из таблицы или строкой в файле `.env` в рабочем каталоге процесса. Вложенное поле отделяется `__`, регистр имени переменной не важен

## Общие

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `database_url` | `TASKGEN_DATABASE_URL` | string | `"postgresql+asyncpg://taskgen:taskgen@localhost:5432/taskgen"` | Адрес Postgres с расширением pgvector в формате SQLAlchemy asyncpg |
| `bootstrap_admin_key` | `TASKGEN_BOOTSTRAP_ADMIN_KEY` | string или null | `null` | Первый admin-ключ; остальные ключи создаются через API и хранятся хэшами |
| `ai_detector_enabled` | `TASKGEN_AI_DETECTOR_ENABLED` | boolean | `false` | Включить детектор ИИ. Его сигнал хранится отдельно и не влияет ни на балл, ни на summary |

## llm

OpenAI-совместимый провайдер: Ollama, vLLM или облачный шлюз

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `llm.base_url` | `TASKGEN_LLM__BASE_URL` | string | `"http://localhost:11434/v1"` | Адрес OpenAI-совместимого API: /chat/completions и /embeddings |
| `llm.api_key` | `TASKGEN_LLM__API_KEY` | string | `"ollama"` | Ключ провайдера, уходит в заголовке Authorization: Bearer; Ollama его не проверяет |
| `llm.generator_model` | `TASKGEN_LLM__GENERATOR_MODEL` | string | `"qwen3.5:9b"` | Модель, которая пишет легенды, ситуации эссе и текст machine summary |
| `llm.judge_model` | `TASKGEN_LLM__JUDGE_MODEL` | string | `"qwen3.5:4b"` | Модель судьи по рубрике и детектора ИИ. Судья другой модели снижает завышение оценок собственного текста генератора |
| `llm.embedding_model` | `TASKGEN_LLM__EMBEDDING_MODEL` | string | `"bge-m3"` | Модель эмбеддингов описаний шаблонов и навыков кандидата |
| `llm.generator_reasoning_effort` | `TASKGEN_LLM__GENERATOR_REASONING_EFFORT` | одно из `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"` или null | `null` | Поле reasoning_effort запроса к модели генерации; null — поле не передаётся, и модель рассуждает как обычно. Значение зависит от модели: на слабой машине "none" сокращает ответ в разы, рассуждение qwen3.5:4b на простой запрос занимало тысячи токенов и иногда не оставляло места самому ответу |
| `llm.judge_reasoning_effort` | `TASKGEN_LLM__JUDGE_REASONING_EFFORT` | одно из `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"` или null | `null` | Поле reasoning_effort запроса к модели судьи и детектора ИИ; null — поле не передаётся, и модель рассуждает как обычно. Значение зависит от модели: на слабой машине "none" сокращает ответ в разы, рассуждение qwen3.5:4b на простой запрос занимало тысячи токенов и иногда не оставляло места самому ответу |
| `llm.checker_model` | `TASKGEN_LLM__CHECKER_MODEL` | string или null | `null` | Проверяльщик ситуации эссе: ищет в ней факты шаблона и цитирует их; null — та же модель, что пишет ситуацию |
| `llm.checker_reasoning_effort` | `TASKGEN_LLM__CHECKER_REASONING_EFFORT` | одно из `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"` или null | `null` | Поле reasoning_effort запроса к модели проверяльщика ситуации эссе; null — поле не передаётся, и модель рассуждает как обычно. Значение зависит от модели: на слабой машине "none" сокращает ответ в разы, рассуждение qwen3.5:4b на простой запрос занимало тысячи токенов и иногда не оставляло места самому ответу |
| `llm.solver_model` | `TASKGEN_LLM__SOLVER_MODEL` | string или null | `null` | Слепой решатель: решает экземпляр по тому, что увидит кандидат, до выдачи. null — модель проверяльщика, а без неё — модель генерации. Слабая модель решателя чаще отклоняет годные экземпляры |
| `llm.solver_reasoning_effort` | `TASKGEN_LLM__SOLVER_REASONING_EFFORT` | одно из `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"` или null | `null` | Поле reasoning_effort запроса к модели слепого решателя; null — поле не передаётся, и модель рассуждает как обычно. Значение зависит от модели: на слабой машине "none" сокращает ответ в разы, рассуждение qwen3.5:4b на простой запрос занимало тысячи токенов и иногда не оставляло места самому ответу |
| `llm.timeout_seconds` | `TASKGEN_LLM__TIMEOUT_SECONDS` | number | `120.0` | Таймаут одного HTTP-запроса к провайдеру, секунды |
| `llm.concurrency` | `TASKGEN_LLM__CONCURRENCY` | integer, ≥ 1 | `1` | Сколько запросов к модели идут одновременно, общий лимит генерации и оценки. Локальная модель отвечает по очереди; облачному провайдеру можно больше |

## selection

Отбор шаблонов: p ∝ exp(−d / T), d = alpha·|Δгрейд| + beta·(1 − сходство). |Δгрейд| — разность позиций грейдов в списке, упорядоченном по level, а не разность level. Значения по умолчанию; тест на категорию переопределяет их строкой test_configs. Шаблоны каждого типа отбираются отдельно, и первые min_claimed_per_kind заданий типа берутся только из шаблонов заявленного грейда

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `selection.temperature` | `TASKGEN_SELECTION__TEMPERATURE` | number, ≥ 0.0, ≤ 1.0 | `0.3` | Температура отбора t; шкала 0..1 переводится в T = t / (1 − t): 0 — только ближайшие шаблоны, 1 — равномерно |
| `selection.alpha` | `TASKGEN_SELECTION__ALPHA` | number, ≥ 0.0 | `1.0` | Вес разницы грейдов в расстоянии d |
| `selection.beta` | `TASKGEN_SELECTION__BETA` | number, ≥ 0.0 | `1.0` | Вес несходства навыков в расстоянии d |
| `selection.code_tasks` | `TASKGEN_SELECTION__CODE_TASKS` | integer, ≥ 0 | `3` | Число заданий с кодом в тесте. Вместе с эссе — пять заданий: на слабой машине каждое из них — минуты генерации и десятки минут работы кандидата |
| `selection.essay_tasks` | `TASKGEN_SELECTION__ESSAY_TASKS` | integer, ≥ 0 | `2` | Число эссе в тесте |
| `selection.quiz_tasks` | `TASKGEN_SELECTION__QUIZ_TASKS` | integer, ≥ 0 | `0` | Число тестовых задачек с выбором ответа. Задачка занимает у кандидата пару минут, поэтому их больше, чем остальных заданий |
| `selection.min_claimed_per_kind` | `TASKGEN_SELECTION__MIN_CLAIMED_PER_KIND` | integer, ≥ 0 | `1` | Квота: сколько первых заданий каждого типа выбираются только среди шаблонов заявленного грейда. Без квоты единственное задание типа при t = 0.3 и трёх шаблонах на грейд оказывается чужого грейда в 16 % тестов. Если шаблонов заявленного грейда меньше квоты, тест не собирается |

## scoring

Балл за задание с кодом — взвешенная сумма компонентов из диапазона 0..1

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `scoring.weight_tests` | `TASKGEN_SCORING__WEIGHT_TESTS` | number | `0.6` | Вес доли пройденных скрытых тестов |
| `scoring.weight_rubric` | `TASKGEN_SCORING__WEIGHT_RUBRIC` | number | `0.25` | Вес балла судьи по рубрике |
| `scoring.weight_quality` | `TASKGEN_SCORING__WEIGHT_QUALITY` | number | `0.1` | Вес качества кода по метрикам статических анализаторов |
| `scoring.weight_performance` | `TASKGEN_SCORING__WEIGHT_PERFORMANCE` | number | `0.05` | Вес скорости и памяти относительно эталона |
| `scoring.weight_quiz` | `TASKGEN_SCORING__WEIGHT_QUIZ` | number, ≥ 0.0, ≤ 1.0 | `0.1` | Вес блока тестовых задачек в общем балле сессии; остальное — средний балл заданий с кодом и эссе. Пять задачек — минуты работы против часов на практические задания, а угаданный ответ даёт 1 с вероятностью 1/4: при 0.1 одни задачки дают не больше 0.1 общего балла, а угадывание в среднем 0.025. В грейд по уровням задачки не входят |
| `scoring.pass_threshold` | `TASKGEN_SCORING__PASS_THRESHOLD` | number, ≥ 0.0, ≤ 1.0 | `0.6` | Доля решённых заданий уровня, начиная с которой уровень подтверждён |
| `scoring.solved_threshold` | `TASKGEN_SCORING__SOLVED_THRESHOLD` | number, ≥ 0.0, ≤ 1.0 | `0.7` | Балл, начиная с которого задание считается решённым. При весах по умолчанию 0.7 недостижим, если упала половина тестов: 0.6·0.5 + 0.4 = 0.7 только при идеальных рубрике, качестве и скорости. Все тесты без замечаний судьи дают 0.6 и тоже не проходят |
| `scoring.min_evidence_per_level` | `TASKGEN_SCORING__MIN_EVIDENCE_PER_LEVEL` | integer, ≥ 0 | `1` | Сколько заданий с кодом и эссе заявленного уровня нужно для вердикта о грейде; меньше — исход insufficient вместо случайного вердикта. Тестовые задачки не считаются |
| `scoring.judge_samples` | `TASKGEN_SCORING__JUDGE_SAMPLES` | integer, ≥ 1 | `3` | Сколько раз судья оценивает ответ; итог — нижняя медиана |
| `scoring.judge_temperature` | `TASKGEN_SCORING__JUDGE_TEMPERATURE` | number, ≥ 0.0, ≤ 2.0 | `0.7` | Температура судьи. Ненулевая нужна, чтобы сэмплы различались и разброс что-то значил |
| `scoring.analyzer_timeout_seconds` | `TASKGEN_SCORING__ANALYZER_TIMEOUT_SECONDS` | number, > 0.0 | `30.0` | Лимит времени одного статического анализатора, секунды |

## generation

Генерация экземпляров заданий и пул готовых экземпляров

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `generation.autostart` | `TASKGEN_GENERATION__AUTOSTART` | boolean | `true` | При старте сервиса подхватить незавершённые сессии и запустить пополнение пула |
| `generation.pool_size` | `TASKGEN_GENERATION__POOL_SIZE` | integer, ≥ 0 | `2` | Сколько стандартных экземпляров, проверенных эталоном и без персонализации, сервис держит на каждый текущий шаблон. Из пула выдаётся первое задание сессии, поэтому ответ на создание сессии не ждёт генерации. 0 выключает пул |
| `generation.pool_max_age_hours` | `TASKGEN_GENERATION__POOL_MAX_AGE_HOURS` | number, > 0 | `24.0` | Экземпляр пула старше этого не выдаётся и удаляется при пополнении: даже готовый экземпляр не должен месяцами ходить по кандидатам |
| `generation.pool_refill_interval_seconds` | `TASKGEN_GENERATION__POOL_REFILL_INTERVAL_SECONDS` | number, > 0 | `900.0` | Как часто пополнять пул без внешнего повода; ещё пул пополняется после импорта шаблонов |
| `generation.replacements_per_task` | `TASKGEN_GENERATION__REPLACEMENTS_PER_TASK` | integer, ≥ 0 | `3` | Сколько раз заменить шаблон задания, который не дал экземпляра, другим того же грейда. Каждая замена — ещё одна генерация, а на слабой машине это минуты |
| `generation.adjacent_topic_probability` | `TASKGEN_GENERATION__ADJACENT_TOPIC_PROBABILITY` | number, ≥ 0.0, ≤ 1.0 | `0.3` | Доля персональных заданий, чья легенда тематически опирается на навык профиля, которого нет в шаблоне. Выбор воспроизводим по seed задания; окружение задачи не меняется |
| `generation.instance_attempts` | `TASKGEN_GENERATION__INSTANCE_ATTEMPTS` | integer, ≥ 1 | `5` | Сколько seed перебрать, пока эталон не пройдёт все кейсы экземпляра |
| `generation.legend_attempts` | `TASKGEN_GENERATION__LEGEND_ATTEMPTS` | integer, ≥ 1 | `3` | Сколько раз попросить LLM переписать условие, которое не прошло проверки |
| `generation.legend_temperature` | `TASKGEN_GENERATION__LEGEND_TEMPERATURE` | number, ≥ 0.0, ≤ 2.0 | `0.7` | Температура легенды. Легенда — творческий текст: при нулевой температуре экземпляры звучат одинаково |
| `generation.solver_enabled` | `TASKGEN_GENERATION__SOLVER_ENABLED` | boolean | `false` | Проверять экземпляр слепым решателем до выдачи: задачку он решает по кейсу и вариантам, задание с кодом — по условию и примерам, а решение идёт на все кейсы. Без решателя не проверены полнота условия и отсутствие второго верного ответа в кейсе. Включать стоит с сильной моделью решателя, например облачной: на слабой машине это ещё один вызов модели на задание и ложные отказы |
| `generation.solver_attempts` | `TASKGEN_GENERATION__SOLVER_ATTEMPTS` | integer, ≥ 1 | `1` | Сколько раз решатель пробует решить один черновик, прежде чем признать его нерешаемым и отправить на переписывание |
| `generation.public_examples` | `TASKGEN_GENERATION__PUBLIC_EXAMPLES` | integer, ≥ 0 | `2` | Сколько первых кейсов задания с кодом показать кандидату примерами; если кейсов больше одного, хотя бы один остаётся скрытым |

## sandbox

Песочница Piston, где исполняются генераторы тестов, эталоны и решения

| Поле | Переменная окружения | Тип | По умолчанию | Описание |
|---|---|---|---|---|
| `sandbox.piston_url` | `TASKGEN_SANDBOX__PISTON_URL` | string | `"http://localhost:2000"` | Адрес Piston API |
| `sandbox.run_timeout_ms` | `TASKGEN_SANDBOX__RUN_TIMEOUT_MS` | integer | `3000` | Лимит времени одного запуска, миллисекунды; окружение может поднять его до своего минимума |
| `sandbox.run_memory_limit_bytes` | `TASKGEN_SANDBOX__RUN_MEMORY_LIMIT_BYTES` | integer | `268435456` | Лимит памяти одного запуска, байты; окружение может поднять его до своего минимума |
