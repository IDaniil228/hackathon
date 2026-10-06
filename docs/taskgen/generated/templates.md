# Банк шаблонов taskgen

> [!NOTE]
> Файл собран командой `uv run python scripts/gen_docs.py` из каталога `taskgen/`. Не правьте его руками: правьте источник и пересоберите файл. Источник — шаблоны [`seed/templates`](../../../taskgen/seed/templates), разобранные `parse_template`, и вакансии [`seed/vacancies`](../../../taskgen/seed/vacancies)

В банке 27 шаблонов. Формат шаблона описан в [`TEMPLATE_FORMAT.md`](../../../taskgen/TEMPLATE_FORMAT.md)

## Сводка по ячейкам

| Область | Грейд | С кодом | Эссе | Сложность | Время, мин |
|---|---|---|---|---|---|
| `algo` | `junior` | 2 | 1 | 3 | 15–20 |
| `algo` | `middle` | 2 | 1 | 5 | 20–30 |
| `algo` | `senior` | 2 | 1 | 7 | 30–40 |
| `backend` | `junior` | 2 | 1 | 3 | 20–25 |
| `backend` | `middle` | 2 | 1 | 5 | 25–30 |
| `backend` | `senior` | 2 | 1 | 6–7 | 35–40 |
| `ml` | `junior` | 2 | 1 | 3 | 15–20 |
| `ml` | `middle` | 2 | 1 | 5 | 20–30 |
| `ml` | `senior` | 2 | 1 | 7 | 30–40 |

## Область `algo`

| Шаблон | Грейд | Тип | Окружение | Сложность | Время, мин | Навыки | Пунктов рубрики | Описание |
|---|---|---|---|---|---|---|---|---|
| [`algo-pairs-sum-at-most`](../../../taskgen/seed/templates/algo/junior/algo-pairs-sum-at-most.md) | `junior` | code | `python` | 3 | 20 | `python`, `two-pointers` | 5 | Подсчёт пар элементов отсортированного массива, сумма которых не превышает порог, быстрее квадратичного перебора: два указателя или бинарный поиск |
| [`algo-sorting-stability-essay`](../../../taskgen/seed/templates/algo/junior/algo-sorting-stability-essay.md) | `junior` | essay |  | 3 | 15 | `sorting`, `python` | 6 | Вопрос о сортировках: устойчивость и сортировка по нескольким ключам, нижняя оценка сортировок сравнением и сортировка подсчётом, поведение встроенной сортировки |
| [`algo-top-k-ranking-cpp`](../../../taskgen/seed/templates/algo/junior/algo-top-k-ranking-cpp.md) | `junior` | code | `cpp` | 3 | 20 | `cpp`, `sorting` | 5 | Отбор k лучших участников рейтинга на C++: сортировка по убыванию баллов с разрешением ничьих по имени, частичная сортировка и обработка k вне диапазона |
| [`algo-grid-weighted-path`](../../../taskgen/seed/templates/algo/middle/algo-grid-weighted-path.md) | `middle` | code | `python` | 5 | 30 | `python`, `graphs` | 6 | Кратчайший путь по клетчатому полю с двумя ценами клеток и стенами: алгоритм Дейкстры или его вариант для малых целых весов, недостижимая цель |
| [`algo-max-meetings-cpp`](../../../taskgen/seed/templates/algo/middle/algo-max-meetings-cpp.md) | `middle` | code | `cpp` | 5 | 25 | `cpp`, `greedy`, `sorting` | 6 | Максимальное число непересекающихся полуинтервалов на C++: жадный выбор по раннему окончанию, касание концов, доказательство корректности жадного шага |
| [`algo-shortest-path-choice-essay`](../../../taskgen/seed/templates/algo/middle/algo-shortest-path-choice-essay.md) | `middle` | essay |  | 5 | 20 | `graphs`, `python` | 7 | Выбор алгоритма кратчайших путей по свойствам графа: BFS, 0-1 BFS, Дейкстра, Беллман — Форд, отрицательные веса и циклы, оценки сложности |
| [`algo-cheapest-route-cpp`](../../../taskgen/seed/templates/algo/senior/algo-cheapest-route-cpp.md) | `senior` | code | `cpp` | 7 | 40 | `cpp`, `graphs`, `dynamic-programming` | 6 | Самый дешёвый маршрут с ограничением на число пересадок на C++: Беллман — Форд по слоям или Дейкстра по состояниям, вторичный критерий — наименьшее число рейсов |
| [`algo-streaming-top-k-essay`](../../../taskgen/seed/templates/algo/senior/algo-streaming-top-k-essay.md) | `senior` | essay |  | 7 | 30 | `data-structures` | 8 | Проектирование подсчёта самых частых элементов потока при ограниченной памяти: точное решение с кучей, Misra — Gries и Count-Min Sketch, гарантии ошибки, распределённое слияние |
| [`algo-weighted-jobs`](../../../taskgen/seed/templates/algo/senior/algo-weighted-jobs.md) | `senior` | code | `python` | 7 | 35 | `python`, `dynamic-programming`, `binary-search` | 6 | Взвешенный выбор непересекающихся работ с обязательным перерывом между ними: динамика по отсортированным работам и бинарный поиск предшественника за O(n log n) |

## Область `backend`

| Шаблон | Грейд | Тип | Окружение | Сложность | Время, мин | Навыки | Пунктов рубрики | Описание |
|---|---|---|---|---|---|---|---|---|
| [`backend-dockerfile-review-essay`](../../../taskgen/seed/templates/backend/junior/backend-dockerfile-review-essay.md) | `junior` | essay |  | 3 | 20 | `docker`, `django`, `security` | 7 | Ревью Dockerfile для Django-приложения: кэш слоёв и порядок COPY, секреты в образе, запуск от root, runserver вместо WSGI-сервера, тег latest и .dockerignore |
| [`backend-paginate`](../../../taskgen/seed/templates/backend/junior/backend-paginate.md) | `junior` | code | `python` | 3 | 20 | `python`, `http-api` | 6 | Постраничная выдача списка по параметрам запроса: разбор строковых page и per_page, значения по умолчанию, ограничение размера страницы, метаданные страниц |
| [`backend-validate-signup`](../../../taskgen/seed/templates/backend/junior/backend-validate-signup.md) | `junior` | code | `python` | 3 | 25 | `python`, `http-api` | 6 | Валидация тела запроса регистрации: обязательные поля, типы JSON с ловушкой bool как int, правила для email, пароля, возраста и логина, сбор всех ошибок в стабильном порядке |
| [`backend-django-orm-performance-essay`](../../../taskgen/seed/templates/backend/middle/backend-django-orm-performance-essay.md) | `middle` | essay |  | 5 | 25 | `django`, `sql` | 8 | Отладка медленного эндпоинта Django: проблема N+1, select_related и prefetch_related, индексы и EXPLAIN, лишние поля и count, кэширование и проверка результата |
| [`backend-idempotency-keys`](../../../taskgen/seed/templates/backend/middle/backend-idempotency-keys.md) | `middle` | code | `python` | 5 | 30 | `python`, `http-api`, `caching` | 6 | Обработка повторных запросов по ключу идемпотентности: повтор с тем же телом, конфликт при другом теле, срок жизни записи, ошибки сервера не сохраняются |
| [`backend-sliding-rate-limiter`](../../../taskgen/seed/templates/backend/middle/backend-sliding-rate-limiter.md) | `middle` | code | `python` | 5 | 30 | `python`, `http-api` | 6 | Ограничитель частоты запросов со скользящим окном по журналу принятых запросов: границы окна, одинаковые метки времени, отклонённые запросы не расходуют лимит |
| [`backend-django-docker-production-essay`](../../../taskgen/seed/templates/backend/senior/backend-django-docker-production-essay.md) | `senior` | essay |  | 7 | 35 | `django`, `docker`, `security`, `sql` | 8 | Проектирование выката Django в контейнерах: миграции без простоя, секреты и настройки безопасности, проверки готовности, статика, воркеры и наблюдаемость |
| [`backend-latency-report`](../../../taskgen/seed/templates/backend/senior/backend-latency-report.md) | `senior` | code | `python` | 6 | 40 | `python`, `observability`, `http-api` | 7 | Отчёт по журналу доступа: строгий разбор строк с пропуском битых, нормализация маршрутов с идентификаторами, доля ошибок 5xx и перцентиль задержки методом ближайшего ранга |
| [`backend-ttl-lru-cache`](../../../taskgen/seed/templates/backend/senior/backend-ttl-lru-cache.md) | `senior` | code | `python` | 7 | 40 | `python`, `caching` | 7 | Кэш с ограничением ёмкости и сроком жизни записей: вытеснение давно не использованных, ленивое и плановое удаление просроченных, логарифмическая стоимость операций |

## Область `ml`

| Шаблон | Грейд | Тип | Окружение | Сложность | Время, мин | Навыки | Пунктов рубрики | Описание |
|---|---|---|---|---|---|---|---|---|
| [`ml-fbeta-score`](../../../taskgen/seed/templates/ml/junior/ml-fbeta-score.md) | `junior` | code | `python-ml` | 3 | 20 | `python`, `numpy`, `ml-metrics` | 4 | Реализация F-beta меры для бинарной классификации на NumPy: матрица ошибок, вес полноты против точности, деление на ноль при отсутствии положительных примеров |
| [`ml-standardize-features`](../../../taskgen/seed/templates/ml/junior/ml-standardize-features.md) | `junior` | code | `python-ml` | 3 | 20 | `python`, `numpy`, `data-preprocessing` | 5 | Стандартизация признаков на NumPy: статистики только по обучающей выборке, выбор ddof, постоянные признаки и пустая тестовая выборка без утечки данных |
| [`ml-validation-leakage-essay`](../../../taskgen/seed/templates/ml/junior/ml-validation-leakage-essay.md) | `junior` | essay |  | 3 | 15 | `scikit-learn`, `data-preprocessing` | 6 | Вопрос о проверке модели: переобучение, разбиение на обучающую, валидационную и тестовую выборки, кросс-валидация, утечка данных через предобработку и время |
| [`ml-imbalanced-threshold-essay`](../../../taskgen/seed/templates/ml/middle/ml-imbalanced-threshold-essay.md) | `middle` | essay |  | 5 | 20 | `scikit-learn`, `ml-metrics` | 7 | Вопрос о несбалансированной классификации: выбор метрики, PR-кривая против ROC, порог по стоимости ошибок, калибровка вероятностей и веса классов |
| [`ml-kmeans-step`](../../../taskgen/seed/templates/ml/middle/ml-kmeans-step.md) | `middle` | code | `python-ml` | 5 | 30 | `python`, `numpy`, `clustering` | 6 | Одна итерация алгоритма Ллойда для k-средних на NumPy: векторное назначение кластеров, ничьи по расстоянию, пустые кластеры и инерция |
| [`ml-logreg-gradient-step`](../../../taskgen/seed/templates/ml/middle/ml-logreg-gradient-step.md) | `middle` | code | `python-ml` | 5 | 30 | `python`, `numpy`, `optimization` | 6 | Один шаг градиентного спуска для логистической регрессии с L2-регуляризацией на NumPy: вывод градиента кросс-энтропии, свободный член без штрафа, усреднение по батчу |
| [`ml-batchnorm-backward`](../../../taskgen/seed/templates/ml/senior/ml-batchnorm-backward.md) | `senior` | code | `python-ml` | 7 | 40 | `python`, `numpy`, `optimization` | 6 | Обратный проход батч-нормализации в режиме обучения на NumPy: градиенты по входу, масштабу и сдвигу, вывод через вычислительный граф, постоянные признаки и батч из одного объекта |
| [`ml-production-degradation-essay`](../../../taskgen/seed/templates/ml/senior/ml-production-degradation-essay.md) | `senior` | essay |  | 7 | 30 | `scikit-learn`, `pytorch`, `ml-metrics` | 8 | Расследование падения качества модели в продакшене: расхождение обучения и инференса, сдвиг данных и концепции, утечка в офлайн-оценке, мониторинг и безопасный выкат |
| [`ml-softmax-cross-entropy`](../../../taskgen/seed/templates/ml/senior/ml-softmax-cross-entropy.md) | `senior` | code | `python-ml` | 7 | 40 | `python`, `numpy`, `pytorch`, `optimization` | 6 | Численно устойчивые softmax и кросс-энтропия с label smoothing и ignore_index на NumPy или PyTorch: значение функции потерь и аналитический градиент по логитам |

## Демонстрационные вакансии

Вакансия называет область, грейд и навыки кодами справочников, поэтому её можно подать в отбор шаблонов полями area, claimed_grade и skills запроса `POST /sessions`

| Вакансия | Область | Грейд | Навыки |
|---|---|---|---|
| [Ведущий разработчик алгоритмов маршрутизации на C++](../../../taskgen/seed/vacancies/algo-cpp-senior.md) | `algo` | `senior` | `cpp`, `graphs`, `dynamic-programming`, `greedy`, `data-structures` |
| [Python-разработчик бэкенда в платёжную команду](../../../taskgen/seed/vacancies/backend-python-middle.md) | `backend` | `middle` | `python`, `django`, `docker`, `sql`, `http-api`, `caching` |
| [Младший ML-инженер в команду антифрода](../../../taskgen/seed/vacancies/ml-engineer-junior.md) | `ml` | `junior` | `python`, `numpy`, `scikit-learn`, `ml-metrics`, `data-preprocessing` |
