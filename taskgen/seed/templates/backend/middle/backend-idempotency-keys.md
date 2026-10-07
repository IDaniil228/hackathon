---
name: backend-idempotency-keys
description: >-
  Обработка повторных запросов по ключу идемпотентности: повтор с тем же телом, конфликт
  при другом теле, срок жизни записи, ошибки сервера не сохраняются
area: backend
grade: middle
skills: [python, http-api, caching]
kind: code
runtime: python
io: function
difficulty: 5
expected_minutes: 30
params:
  n_max: {type: int, min: 1200, max: 1500}
  domain:
    type: choice
    choices:
      - создание платежа в платёжном API
      - оформление заказа в интернет-магазине
      - перевод между счетами в банковском приложении
      - создание заявки на возврат товара
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 1, required: true}
---

# Ключи идемпотентности

## Генерация

Придумай легенду: {domain}. Клиент повторяет запрос при обрыве сети с тем же заголовком `Idempotency-Key`, и сервер не должен выполнить операцию дважды. В условии обязательно скажи:

- функция `idempotency_outcomes(requests, ttl)` получает список запросов в порядке поступления и целое `ttl` в секундах; запрос — `[t, key, body_hash, status]`: целое время в секундах (не убывает), ключ-строка, хэш тела запроса и HTTP-статус, который вернул бы обработчик, если операцию выполнить;
- сервер хранит для ключа запись о выполненной операции: время выполнения и хэш тела; запись живёт, пока `t − время_выполнения < ttl`;
- если для ключа есть живая запись, запрос с тем же хэшем тела получает `"replayed"`, а с другим — `"conflict"`; запись при этом не меняется и не продлевается;
- иначе операция выполняется — ответ `"executed"`; запись сохраняется, только если статус меньше 500: после ошибки сервера клиент вправе повторить запрос;
- пустой ключ значит «без идемпотентности»: всегда `"executed"`, ничего не хранится;
- вернуть список ответов в порядке запросов; запросов до {n_max}.

## Решения

### reference +

```python
def idempotency_outcomes(requests, ttl):
    stored = {}
    outcomes = []
    for t, key, body_hash, status in requests:
        record = stored.get(key) if key else None
        if record is not None and t - record[0] < ttl:
            outcomes.append("replayed" if record[1] == body_hash else "conflict")
            continue
        outcomes.append("executed")
        if key and status < 500:
            stored[key] = (t, body_hash)
    return outcomes
```

### ttl-inclusive -

Запись живёт ровно ttl включительно

```python
def idempotency_outcomes(requests, ttl):
    stored = {}
    outcomes = []
    for t, key, body_hash, status in requests:
        record = stored.get(key) if key else None
        if record is not None and t - record[0] <= ttl:
            outcomes.append("replayed" if record[1] == body_hash else "conflict")
            continue
        outcomes.append("executed")
        if key and status < 500:
            stored[key] = (t, body_hash)
    return outcomes
```

### errors-stored -

Ошибки сервера сохраняются

```python
def idempotency_outcomes(requests, ttl):
    stored = {}
    outcomes = []
    for t, key, body_hash, status in requests:
        record = stored.get(key) if key else None
        if record is not None and t - record[0] < ttl:
            outcomes.append("replayed" if record[1] == body_hash else "conflict")
            continue
        outcomes.append("executed")
        if key:
            stored[key] = (t, body_hash)
    return outcomes
```

## Сигнатура

```
idempotency_outcomes
```

## Тесты

```python
def scan(requests, ttl):
    # Для каждого запроса ищется назад последнее выполнение с тем же ключом,
    # которое сохранило запись.
    outcomes = []
    for i, (t, key, body_hash, _) in enumerate(requests):
        verdict = "executed"
        if key:
            for j in range(i - 1, -1, -1):
                t0, key0, body0, status0 = requests[j]
                if key0 == key and outcomes[j] == "executed" and status0 < 500:
                    if t - t0 < ttl:
                        verdict = "replayed" if body0 == body_hash else "conflict"
                    break
        outcomes.append(verdict)
    return outcomes


def case(requests, ttl):
    return {"input": [requests, ttl], "expected": scan(requests, ttl)}


def stream(rng, n, keys, bodies, max_gap):
    t = 0
    result = []
    for _ in range(n):
        t += rng.randint(0, max_gap)
        status = rng.choice([200, 201, 201, 400, 409, 500, 503])
        result.append([t, rng.choice(keys), rng.choice(bodies), status])
    return result


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case(
            [
                [0, "k1", "h1", 201],
                [5, "k1", "h1", 201],
                [6, "k1", "h2", 201],
                [100, "k1", "h2", 201],
            ],
            60,
        ),
        case([[0, "k1", "h1", 503], [2, "k1", "h1", 201], [3, "k1", "h1", 201]], 60),
        case([], 10),
        case([[0, "", "h1", 201], [1, "", "h1", 201]], 10),
        case([[0, "a", "h", 201], [10, "a", "h", 201]], 10),
        case([[0, "a", "h", 201], [9, "a", "h", 201]], 10),
        case(
            [[0, "a", "h", 201], [5, "a", "x", 201], [11, "a", "x", 201], [12, "a", "h", 201]], 10
        ),
        case([[0, "a", "h", 400], [1, "a", "h", 201], [1, "b", "h", 201], [2, "b", "h", 500]], 30),
        case([[0, "a", "h", 201], [0, "a", "h", 201], [0, "A", "h", 201]], 1),
    ]
    cases = grouped("manual", cases)
    for _ in range(6):
        keys = ["", "k1", "k2", "k3"][: rng.randint(2, 4)]
        requests = stream(rng, rng.randint(5, 60), keys, ["h1", "h2"], 15)
        cases.append(dict(case(requests, rng.randint(5, 60)), group="random"))
    keys = [""] + ["key-" + str(i) for i in range(40)]
    requests = stream(rng, params["n_max"], keys, ["h1", "h2", "h3"], 3)
    cases.append(dict(case(requests, rng.randint(30, 300)), group="stress"))
    return cases
```

## Рубрика

- lookup-order: Сначала проверяется живая запись по ключу, и только при её отсутствии операция выполняется
  - выполнение операции до проверки ключа с последующей перезаписью ответа
- ttl-boundary: Срок жизни проверяется как `t − время < ttl`: запись ровно через `ttl` секунд уже истекла
  - `t - record_time <= ttl`
- no-refresh: Повтор и конфликт не продлевают и не перезаписывают запись
  - `stored[key] = (t, body_hash)` в ветке повтора
- server-errors: Ответы со статусом 5xx не сохраняются, а клиентские ошибки 4xx сохраняются как результат операции
  + `if key and status < 500:`
- empty-key: Пустой ключ обрабатывается как запрос без идемпотентности и не попадает в хранилище
- real-world: В комментарии или структуре решения учтено, что в реальном сервисе нужна атомарность «проверить и записать» при параллельных запросах с одним ключом, например блокировка или уникальный индекс
