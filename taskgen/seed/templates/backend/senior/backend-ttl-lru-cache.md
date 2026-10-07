---
name: backend-ttl-lru-cache
description: >-
  Кэш с ограничением ёмкости и сроком жизни записей: вытеснение давно не использованных,
  ленивое и плановое удаление просроченных, логарифмическая стоимость операций
area: backend
grade: senior
skills: [python, caching]
kind: code
runtime: python
difficulty: 7
expected_minutes: 40
params:
  n_max: {type: int, min: 1200, max: 1500}
  domain:
    type: choice
    choices:
      - кэш профилей пользователей перед медленным сервисом аккаунтов
      - кэш курсов валют для платёжного шлюза
      - кэш результатов поиска билетов
      - кэш ответов внешнего API геокодирования
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 1, required: true}
---

# Кэш с TTL и вытеснением LRU

## Генерация

Придумай легенду: {domain}. Нужно реализовать логику кэша и проверить её на журнале операций. В условии обязательно скажи:

- функция `simulate_cache(capacity, operations)` получает целую ёмкость `capacity >= 1` и список операций; время `t` — целое и не убывает от операции к операции;
- операции: `["put", t, key, value, ttl]` — записать значение с целым сроком жизни `ttl >= 1`; `["get", t, key]` — прочитать; `["delete", t, key]` — удалить, если есть;
- запись, сделанная в момент `t0`, живёт, пока текущее время меньше `t0 + ttl`; просроченная запись для кэша не существует и места не занимает;
- `get` живой записи возвращает её значение и делает её самой недавно использованной; иначе возвращает `None`;
- `put` существующей живой записи заменяет значение и срок жизни и делает её самой недавно использованной; `put` новой записи при заполненном кэше — `capacity` живых записей — сначала вытесняет живую запись, которой дольше всех не пользовались; «использование» — это `put` или успешный `get`;
- вернуть список результатов всех `get` по порядку;
- операций до {n_max}, но каждая операция должна выполняться за O(log n) или амортизированно за O(1); полный перебор записей на операцию не подходит.

## Эталон

```python
import heapq
from collections import OrderedDict


def simulate_cache(capacity, operations):
    entries = OrderedDict()  # key -> (value, expires_at); порядок — от давно использованных
    deadlines = []  # куча (expires_at, key); устаревшие элементы пропускаются при извлечении
    results = []

    def purge(now):
        while deadlines and deadlines[0][0] <= now:
            expires_at, key = heapq.heappop(deadlines)
            if key in entries and entries[key][1] == expires_at:
                del entries[key]

    for op in operations:
        kind, now, key = op[0], op[1], op[2]
        purge(now)
        if kind == "get":
            if key in entries:
                entries.move_to_end(key)
                results.append(entries[key][0])
            else:
                results.append(None)
        elif kind == "delete":
            entries.pop(key, None)
        else:
            value, ttl = op[3], op[4]
            if key not in entries and len(entries) >= capacity:
                entries.popitem(last=False)
            entries[key] = (value, now + ttl)
            entries.move_to_end(key)
            heapq.heappush(deadlines, (now + ttl, key))
    return results
```

## Сигнатура

```
simulate_cache
```

## Тесты

```python
def replay(capacity, operations):
    # Список записей и счётчик обращений; каждая операция просматривает все записи.
    records = []
    tick = 0
    results = []
    for op in operations:
        kind, now, key = op[0], op[1], op[2]
        tick += 1
        records = [r for r in records if r["expires"] > now]
        found = [r for r in records if r["key"] == key]
        if kind == "get":
            if found:
                found[0]["used"] = tick
                results.append(found[0]["value"])
            else:
                results.append(None)
        elif kind == "delete":
            records = [r for r in records if r["key"] != key]
        elif found:
            found[0].update(value=op[3], expires=now + op[4], used=tick)
        else:
            if len(records) >= capacity:
                oldest = min(records, key=lambda r: r["used"])
                records.remove(oldest)
            records.append({"key": key, "value": op[3], "expires": now + op[4], "used": tick})
    return results


def case(capacity, operations):
    return {"input": [capacity, operations], "expected": replay(capacity, operations)}


def journal(rng, n, keys, max_gap, max_ttl):
    t = 0
    ops = []
    for _ in range(n):
        t += rng.randint(0, max_gap)
        key = rng.choice(keys)
        roll = rng.random()
        if roll < 0.45:
            ops.append(["get", t, key])
        elif roll < 0.55:
            ops.append(["delete", t, key])
        else:
            ops.append(["put", t, key, rng.randint(0, 999), rng.randint(1, max_ttl)])
    return ops


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case(
            2,
            [
                ["put", 0, "a", 1, 10],
                ["put", 1, "b", 2, 10],
                ["get", 2, "a"],
                ["put", 3, "c", 3, 10],
                ["get", 4, "b"],
                ["get", 5, "a"],
                ["get", 6, "c"],
            ],
        ),
        case(2, [["put", 0, "a", 1, 5], ["get", 4, "a"], ["get", 5, "a"]]),
        case(1, []),
        case(1, [["get", 0, "x"], ["delete", 0, "x"]]),
        case(
            2,
            [
                ["put", 0, "a", 1, 3],
                ["put", 0, "b", 2, 100],
                ["put", 3, "c", 3, 100],
                ["get", 3, "b"],
                ["get", 3, "c"],
                ["get", 3, "a"],
            ],
        ),
        case(
            2,
            [
                ["put", 0, "a", 1, 10],
                ["put", 1, "b", 2, 10],
                ["put", 2, "a", 7, 1],
                ["put", 2, "c", 3, 10],
                ["get", 2, "a"],
                ["get", 2, "b"],
                ["get", 3, "a"],
            ],
        ),
        case(
            2,
            [
                ["put", 0, "a", 1, 10],
                ["put", 1, "b", 2, 10],
                ["put", 2, "b", 9, 10],
                ["get", 3, "a"],
                ["get", 3, "b"],
            ],
        ),
        case(
            3,
            [
                ["put", 0, "a", 1, 10],
                ["delete", 1, "a"],
                ["get", 1, "a"],
                ["put", 2, "a", 5, 10],
                ["get", 3, "a"],
            ],
        ),
        case(
            1,
            [
                ["put", 0, "a", 1, 10],
                ["get", 1, "missing"],
                ["put", 2, "b", 2, 10],
                ["get", 3, "a"],
                ["get", 3, "b"],
            ],
        ),
        case(
            2,
            [
                ["put", 0, "a", 1, 2],
                ["put", 1, "b", 2, 50],
                ["get", 1, "a"],
                ["put", 2, "c", 3, 50],
                ["put", 3, "d", 4, 50],
                ["get", 4, "a"],
                ["get", 4, "b"],
                ["get", 4, "c"],
                ["get", 4, "d"],
            ],
        ),
    ]
    cases = grouped("manual", cases)
    for _ in range(6):
        keys = ["k" + str(i) for i in range(rng.randint(2, 6))]
        operations = case(rng.randint(1, 4), journal(rng, rng.randint(10, 80), keys, 3, 30))
        cases.append(dict(operations, group="random"))
    keys = ["k" + str(i) for i in range(40)]
    operations = case(rng.randint(10, 30), journal(rng, params["n_max"], keys, 2, 400))
    cases.append(dict(operations, group="stress"))
    return cases
```

## Рубрика

- structures: Выбраны структуры с нужной стоимостью: упорядоченный словарь или словарь с двусвязным списком для LRU и куча или упорядоченная структура по сроку для просроченных записей
  + `OrderedDict` с `move_to_end` и `popitem(last=False)`
  - `min(entries, key=last_used)` на каждое вытеснение
- expiry-before-evict: Перед вытеснением по LRU удаляются просроченные записи, чтобы живую запись не вытеснили вместо мёртвой
- stale-heap: Устаревшие элементы кучи после перезаписи ключа или удаления распознаются и не удаляют новую запись
  + сравнение `entries[key][1] == expires_at` при извлечении из кучи
  - удаление ключа по первой найденной в куче записи без проверки срока
- ttl-boundary: Запись считается просроченной при `now >= t0 + ttl`
  - `deadlines[0][0] < now`
- recency-rules: Успешный `get` и `put` обновляют порядок использования, а промах `get` и `delete` его не трогают
- update-existing: `put` живого ключа не вызывает вытеснения, даже если кэш заполнен
  - вытеснение до проверки, что ключ уже есть
- complexity: Обоснована стоимость операций: O(log n) на запись в кучу и амортизированная очистка, память не растёт без ограничения или рост объяснён и ограничен
