---
name: backend-paginate
description: >-
  Постраничная выдача списка по параметрам запроса: разбор строковых page и per_page,
  значения по умолчанию, ограничение размера страницы, метаданные страниц
area: backend
grade: junior
skills: [python, http-api]
kind: code
runtime: python
difficulty: 3
expected_minutes: 20
params:
  default_per_page: {type: choice, choices: [10, 20, 25]}
  max_per_page: {type: choice, choices: [50, 100]}
  domain:
    type: choice
    choices:
      - лента заказов в личном кабинете
      - каталог книг в API библиотеки
      - список комментариев к посту
      - журнал платежей в банковском API
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
---

# Пагинация списка

## Генерация

Придумай легенду: {domain}. Эндпоинт получает параметры `page` и `per_page` из строки запроса как строки и возвращает одну страницу. В условии обязательно скажи:

- функция `paginate(items, page, per_page, default_per_page, max_per_page)` получает список `items`, строки `page` и `per_page` (параметр, которого нет в запросе, приходит пустой строкой) и два целых числа; в этой задаче `default_per_page` = {default_per_page}, `max_per_page` = {max_per_page};
- строка считается числом, только если она непустая и состоит из цифр 0–9; иначе используется значение по умолчанию: 1 для `page` и `default_per_page` для `per_page`;
- `page` меньше 1 превращается в 1; `per_page` ограничивается отрезком от 1 до `max_per_page`;
- вернуть словарь с ключами `items` (элементы страницы), `page`, `per_page` (итоговые числа), `total` (длина списка), `pages` (число страниц, 0 для пустого списка), `has_prev`, `has_next`;
- страница с номером больше `pages` пуста; `has_prev` истинно при `page > 1`, `has_next` — при `page < pages`.

## Решения

### reference +

```python
def _number(raw, default):
    # isdigit пропускает «²» и цифры других письменностей, поэтому набор задан явно.
    if raw and all(ch in "0123456789" for ch in raw):
        return int(raw)
    return default


def paginate(items, page, per_page, default_per_page, max_per_page):
    page = max(1, _number(page, 1))
    per_page = min(max(1, _number(per_page, default_per_page)), max_per_page)
    total = len(items)
    pages = -(-total // per_page)
    start = (page - 1) * per_page
    return {
        "items": items[start : start + per_page],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
    }
```

### isdigit -

`isdigit` вместо цифр 0–9

```python
def _number(raw, default):
    # isdigit пропускает «²» и цифры других письменностей, поэтому набор задан явно.
    if raw.strip().isdigit():
        return int(raw)
    return default


def paginate(items, page, per_page, default_per_page, max_per_page):
    page = max(1, _number(page, 1))
    per_page = min(max(1, _number(per_page, default_per_page)), max_per_page)
    total = len(items)
    pages = -(-total // per_page)
    start = (page - 1) * per_page
    return {
        "items": items[start : start + per_page],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
    }
```

### page-below-one -

Страница меньше 1 не поднята

```python
def _number(raw, default):
    # isdigit пропускает «²» и цифры других письменностей, поэтому набор задан явно.
    if raw and all(ch in "0123456789" for ch in raw):
        return int(raw)
    return default


def paginate(items, page, per_page, default_per_page, max_per_page):
    page = _number(page, 1)
    per_page = min(max(1, _number(per_page, default_per_page)), max_per_page)
    total = len(items)
    pages = -(-total // per_page)
    start = (page - 1) * per_page
    return {
        "items": items[start : start + per_page],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
    }
```

## Сигнатура

```
paginate
```

## Тесты

```python
def parse(raw, default):
    digits = "0123456789"
    if raw == "":
        return default
    for ch in raw:
        if ch not in digits:
            return default
    value = 0
    for ch in raw:
        value = value * 10 + digits.index(ch)
    return value


def expected_page(items, page_raw, per_page_raw, default_per_page, max_per_page):
    page = parse(page_raw, 1)
    if page < 1:
        page = 1
    per_page = parse(per_page_raw, default_per_page)
    per_page = 1 if per_page < 1 else per_page
    per_page = max_per_page if per_page > max_per_page else per_page
    pages = 0
    while pages * per_page < len(items):
        pages += 1
    chosen = [item for index, item in enumerate(items) if (index // per_page) + 1 == page]
    return {
        "items": chosen,
        "page": page,
        "per_page": per_page,
        "total": len(items),
        "pages": pages,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


def case(items, page, per_page, params):
    args = [items, page, per_page, params["default_per_page"], params["max_per_page"]]
    return {"input": args, "expected": expected_page(*args)}


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    top = params["max_per_page"]
    numbers = list(range(1, 48))
    cases = [
        case(numbers, "2", "10", params),
        case(["a", "b", "c"], "", "", params),
        case([], "1", "10", params),
        case([], "", "", params),
        case(numbers, "0", "0", params),
        case(numbers, "-1", "-5", params),
        case(numbers, "abc", "1.5", params),
        case(numbers, " 2", "10 ", params),
        case(numbers, "²", "٣", params),
        case(numbers, "007", "05", params),
        case(numbers, "5", "10", params),
        case(numbers, "6", "10", params),
        case(numbers, "99999999999999999999", "10", params),
        case(numbers, "1", str(top + 1), params),
        case(numbers, "1", str(top), params),
        case(list(range(top * 2)), "2", "1000", params),
        case([{"id": 1}, {"id": 2}, {"id": 3}], "2", "2", params),
    ]
    cases = grouped("manual", cases)
    for _ in range(10):
        items = [rng.randint(0, 999) for _ in range(rng.randint(0, 120))]
        page = rng.choice(["", "0", str(rng.randint(1, 15)), "x"])
        per_page = rng.choice(["", "0", str(rng.randint(1, top + 20)), "-3"])
        cases.append(dict(case(items, page, per_page, params), group="random"))
    return cases
```

## Рубрика

- strict-number: Строка признаётся числом только из цифр 0–9: пробелы, знак, точка и «цифры» вроде `²` дают значение по умолчанию, а не исключение
  - `int(raw)` в `try/except`, который принимает `" 2"` и `"+5"`, или `raw.isdigit()`, который пропускает `"²"`
- clamping: Нижняя граница `page` и обе границы `per_page` применяются после разбора, порядок «разобрать → ограничить» соблюдён
  + `min(max(1, value), max_per_page)`
- pages-math: Число страниц считается целочисленно, без `math.ceil` от деления с плавающей точкой на больших числах
  + `-(-total // per_page)` или `(total + per_page - 1) // per_page`
- slice: Элементы страницы берутся срезом, страница за концом списка пуста без особой ветки
  + `items[start : start + per_page]`
- flags: `has_prev` и `has_next` вычисляются из итоговых `page` и `pages`, в том числе для пустого списка
- helper: Разбор параметра вынесен во вспомогательную функцию и не дублируется для `page` и `per_page`
