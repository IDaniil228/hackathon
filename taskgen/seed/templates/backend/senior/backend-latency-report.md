---
name: backend-latency-report
description: >-
  Отчёт по журналу доступа: строгий разбор строк с пропуском битых, нормализация маршрутов
  с идентификаторами, доля ошибок 5xx и перцентиль задержки методом ближайшего ранга
area: backend
grade: senior
skills: [python, observability, http-api]
kind: code
runtime: python
difficulty: 6
expected_minutes: 40
params:
  n_max: {type: int, min: 500, max: 650}
  domain:
    type: choice
    choices:
      - API сервиса доставки еды
      - бэкенд мобильного банка
      - API онлайн-кинотеатра
      - сервис бронирования отелей
---

# Отчёт о задержках по журналу доступа

## Генерация

Придумай легенду: {domain}. Дежурному инженеру нужен отчёт по маршрутам из сырого журнала. В условии обязательно скажи:

- функция `latency_report(lines, percentile)` получает список строк журнала и целое `percentile` от 1 до 100;
- корректная строка — ровно пять непустых полей через одиночный пробел: время (любой непустой токен), метод из `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, цель запроса, начинающаяся с `/`, статус — три цифры от 100 до 599, и длительность — цифры с суффиксом `ms`, например `2026-10-06T12:00:01Z GET /users/42?full=1 200 35ms`; цифры — только 0–9;
- некорректные строки пропускаются молча;
- маршрут — метод, пробел и путь цели без строки запроса (всё от `?`); один завершающий `/` убирается, кроме корня `/`; каждый сегмент пути только из цифр заменяется на `{{id}}`; например `GET /users/42/` даёт маршрут `GET /users/{{id}}`;
- вернуть список `[маршрут, число запросов, доля ответов со статусом 500 и выше, перцентиль длительности]` по маршрутам в порядке возрастания строки маршрута; перцентиль p считается методом ближайшего ранга: наименьшая длительность, не меньше которой — по порядку — хотя бы p % запросов маршрута, то есть элемент с номером `ceil(p · n / 100)` в отсортированном списке, считая с единицы;
- строк до {n_max}, но в реальном журнале их миллионы: решение должно быть линейным по числу строк с сортировкой внутри маршрутов.

## Эталон

```python
from collections import defaultdict

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _digits(text):
    return bool(text) and all(ch in "0123456789" for ch in text)


def _route(method, target):
    path = target.split("?", 1)[0]
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    segments = ["{id}" if _digits(part) else part for part in path.split("/")]
    return f"{method} {'/'.join(segments)}"


def _parse(line):
    fields = line.split(" ")
    if len(fields) != 5 or not fields[0]:
        return None
    _, method, target, status, duration = fields
    if method not in METHODS or not target.startswith("/"):
        return None
    if len(status) != 3 or not _digits(status) or not 100 <= int(status) <= 599:
        return None
    if not duration.endswith("ms") or not _digits(duration[:-2]):
        return None
    return _route(method, target), int(status), int(duration[:-2])


def latency_report(lines, percentile):
    groups = defaultdict(list)
    for line in lines:
        parsed = _parse(line)
        if parsed is not None:
            route, status, duration = parsed
            groups[route].append((status, duration))
    report = []
    for route in sorted(groups):
        records = groups[route]
        durations = sorted(duration for _, duration in records)
        rank = -(-percentile * len(durations) // 100)  # ceil без вещественной арифметики
        errors = sum(1 for status, _ in records if status >= 500)
        report.append([route, len(records), errors / len(records), durations[rank - 1]])
    return report
```

## Сигнатура

```
latency_report
```

## Тесты

```python
TEMPLATES = [
    "/",
    "/health",
    "/users",
    "/users/{id}",
    "/users/{id}/orders",
    "/orders/{id}/items/{id}",
    "/v2/search",
    "/api/2fa/verify",
]
METHODS = ["GET", "GET", "GET", "POST", "PUT", "PATCH", "DELETE"]
STATUSES = [200, 200, 200, 201, 204, 301, 400, 404, 500, 502, 503]
BROKEN = [
    "",
    "GET /users 200 12ms",
    "2026-10-06T10:00:00Z FETCH /users 200 12ms",
    "2026-10-06T10:00:00Z GET /users 2OO 12ms",
    "2026-10-06T10:00:00Z GET /users 200 12",
    "2026-10-06T10:00:00Z GET /users 200 1.5ms",
    "2026-10-06T10:00:00Z GET users 200 12ms",
    "2026-10-06T10:00:00Z GET  /users 200 12ms",
    "2026-10-06T10:00:00Z GET /users 099 12ms",
    "2026-10-06T10:00:00Z GET /users 600 12ms",
    "2026-10-06T10:00:00Z get /users 200 12ms",
    "2026-10-06T10:00:00Z GET /users 200 -5ms",
    "2026-10-06T10:00:00Z GET /users 200 12ms extra",
    " GET /users 200 12ms",
    "2026-10-06T10:00:00Z GET /users 2000 12ms",
    "2026-10-06T10:00:00Z GET /users 200 ms",
    "2026-10-06T10:00:00Z GET /users 200 ٣ms",
]


def render(rng, template):
    path = "/".join(str(rng.randint(0, 99999)) if s == "{id}" else s for s in template.split("/"))
    if path != "/" and rng.random() < 0.2:
        path += "/"
    if rng.random() < 0.25:
        path += rng.choice(["?page=2", "?q=1/2", "?id=7&sort=asc"])
    return path


def nearest_rank(durations, percentile):
    ordered = sorted(durations)
    for i, value in enumerate(ordered, start=1):
        if 100 * i >= percentile * len(ordered):
            return value
    return None


def summarize(records, percentile):
    groups = {}
    for route, status, duration in records:
        groups.setdefault(route, []).append((status, duration))
    report = []
    for route in sorted(groups):
        items = groups[route]
        errors = len([s for s, _ in items if s >= 500])
        report.append(
            [
                route,
                len(items),
                errors / len(items),
                nearest_rank([d for _, d in items], percentile),
            ]
        )
    return report


def synthetic(rng, n, broken_share):
    lines, records = [], []
    for k in range(n):
        if rng.random() < broken_share:
            lines.append(rng.choice(BROKEN))
            continue
        method, template = rng.choice(METHODS), rng.choice(TEMPLATES)
        status, duration = rng.choice(STATUSES), rng.randint(0, 3000)
        stamp = f"2026-10-06T10:{k // 60 % 60:02d}:{k % 60:02d}Z"
        lines.append(f"{stamp} {method} {render(rng, template)} {status} {duration}ms")
        records.append((f"{method} {template}", status, duration))
    return lines, records


def case(lines, records, percentile):
    return {"input": [lines, percentile], "expected": summarize(records, percentile)}


def generate(params, rng):
    t = "2026-10-06T12:00:00Z"
    example = [
        f"{t} GET /users/42 200 30ms",
        f"{t} GET /users/7/ 200 10ms",
        f"{t} GET /users/9?full=1 503 50ms",
        f"{t} POST /users 201 80ms",
        "broken line",
    ]
    example_records = [
        ("GET /users/{id}", 200, 30),
        ("GET /users/{id}", 200, 10),
        ("GET /users/{id}", 503, 50),
        ("POST /users", 201, 80),
    ]
    cases = [
        case(example, example_records, 50),
        case(example, example_records, 100),
        case([], [], 95),
        case(BROKEN, [], 95),
        case(
            [f"{t} GET / 200 5ms", f"{t} GET /?x=1 500 7ms", f"{t} GET /health/ 200 1ms"],
            [("GET /", 200, 5), ("GET /", 500, 7), ("GET /health", 200, 1)],
            50,
        ),
        case(
            [f"{t} DELETE /orders/1/items/2 502 0ms", f"{t} DELETE /orders/a1/items/2 200 3ms"],
            [("DELETE /orders/{id}/items/{id}", 502, 0), ("DELETE /orders/a1/items/{id}", 200, 3)],
            1,
        ),
        case(
            [f"{t} GET /v2/search 200 {d}ms" for d in range(1, 21)],
            [("GET /v2/search", 200, d) for d in range(1, 21)],
            95,
        ),
        case(
            [f"{t} GET /v2/search 200 {d}ms" for d in range(1, 21)],
            [("GET /v2/search", 200, d) for d in range(1, 21)],
            96,
        ),
    ]
    for p in (50, 90, 99):
        lines, records = synthetic(rng, rng.randint(5, 60), 0.2)
        cases.append(case(lines, records, p))
    lines, records = synthetic(rng, params["n_max"], 0.1)
    cases.append(case(lines, records, rng.choice([90, 95, 99])))
    return cases
```

## Рубрика

- strict-parse: Разбор строгий и не падает на мусоре: проверяется число полей, метод из списка, формат статуса и длительности; исключения не используются как основной способ проверки
  - `int(fields[3])` в `try/except`, который принимает `"+200"` и цифры других письменностей
- ascii-digits: Цифры проверяются по набору 0–9, а не через `str.isdigit`, который пропускает `٣` и `²`
- route-normalize: Нормализация маршрута отбрасывает строку запроса до замены сегментов, убирает один завершающий `/`, но не у корня, и заменяет только сегменты целиком из цифр
  - `re.sub(r"\d+", "{id}", path)`, который портит `/v2/search`
- nearest-rank: Перцентиль считается методом ближайшего ранга с целочисленным `ceil`, а не интерполяцией и не через `round`
  + `rank = -(-p * n // 100)`
  - `numpy.percentile(durations, p)` с интерполяцией по умолчанию
- grouping: Записи группируются за один проход по строкам, сортировка выполняется внутри маршрута; общая сложность O(n log n)
- structure: Разбор строки, нормализация маршрута и агрегация разделены на функции, каждую можно протестировать отдельно
- streaming-note: В комментарии или структуре учтено, что для миллионов строк точный перцентиль требует хранить все длительности, и названа альтернатива: гистограмма с фиксированными корзинами или скетч вроде t-digest
