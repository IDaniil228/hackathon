---
name: backend-sliding-rate-limiter
description: >-
  Ограничитель частоты запросов со скользящим окном по журналу принятых запросов:
  границы окна, одинаковые метки времени, отклонённые запросы не расходуют лимит
area: backend
grade: middle
skills: [python, http-api]
kind: code
runtime: python
difficulty: 5
expected_minutes: 30
params:
  n_max: {type: int, min: 2000, max: 2500}
  domain:
    type: choice
    choices:
      - публичный API погоды с лимитом на API-ключ
      - отправка SMS-кодов подтверждения на номер телефона
      - попытки входа в аккаунт с одного IP-адреса
      - запросы к платному API генерации изображений от одного клиента
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 1, required: true}
---

# Ограничитель частоты со скользящим окном

## Генерация

Придумай легенду: {domain}. Нужно смоделировать решения ограничителя по журналу запросов. В условии обязательно скажи:

- функция `rate_limit(requests, limit, window)` получает список запросов `requests`, где каждый запрос — `[t, client]`: целая метка времени в миллисекундах и строка-идентификатор клиента, — а также целые `limit >= 1` и `window >= 1`;
- запросы идут в порядке неубывания `t`, метки могут совпадать; запросы с одинаковой меткой обрабатываются в порядке списка;
- запрос клиента принимается, если среди его ранее принятых запросов меньше `limit` таких, у которых метка лежит в полуинтервале `(t − window, t]`; иначе он отклоняется;
- отклонённые запросы не расходуют лимит; клиенты не влияют друг на друга;
- вернуть список булевых значений — решение по каждому запросу в исходном порядке;
- запросов до {n_max}, но решение должно выдерживать 10^6 запросов: время обработки одного запроса не должно расти с длиной всего журнала.

## Эталон

```python
from collections import defaultdict, deque


def rate_limit(requests, limit, window):
    accepted = defaultdict(deque)
    verdicts = []
    for t, client in requests:
        recent = accepted[client]
        # Запрос с меткой t - window уже вне окна (t - window, t].
        while recent and recent[0] <= t - window:
            recent.popleft()
        allowed = len(recent) < limit
        if allowed:
            recent.append(t)
        verdicts.append(allowed)
    return verdicts
```

## Сигнатура

```
rate_limit
```

## Тесты

```python
import bisect


def brute(requests, limit, window):
    verdicts = []
    for i, (t, client) in enumerate(requests):
        in_window = sum(
            1
            for j in range(i)
            if verdicts[j] and requests[j][1] == client and requests[j][0] > t - window
        )
        verdicts.append(in_window < limit)
    return verdicts


def by_bisect(requests, limit, window):
    accepted = {}
    verdicts = []
    for t, client in requests:
        times = accepted.setdefault(client, [])
        in_window = len(times) - bisect.bisect_right(times, t - window)
        verdicts.append(in_window < limit)
        if verdicts[-1]:
            times.append(t)
    return verdicts


def case(requests, limit, window):
    solve = brute if len(requests) <= 200 else by_bisect
    return {"input": [requests, limit, window], "expected": solve(requests, limit, window)}


def stream(rng, n, clients, max_gap):
    t = rng.randint(0, 1000)
    result = []
    for _ in range(n):
        t += rng.choice([0, 0, rng.randint(0, max_gap)])
        result.append([t, rng.choice(clients)])
    return result


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case([[0, "a"], [100, "a"], [200, "a"], [1000, "a"], [1100, "a"]], 2, 1000),
        case([[0, "a"], [0, "b"], [10, "a"], [10, "b"], [20, "a"]], 1, 15),
        case([], 3, 1000),
        case([[5, "x"]], 1, 1),
        case([[0, "a"], [1000, "a"]], 1, 1000),
        case([[0, "a"], [999, "a"]], 1, 1000),
        case([[0, "a"], [0, "a"], [0, "a"], [1, "a"]], 2, 1),
        case([[0, "a"], [10, "a"], [20, "a"], [30, "a"], [40, "a"]], 2, 25),
        case([[0, "a"], [1, "a"], [2, "a"], [3, "a"], [3, "a"]], 10, 5),
    ]
    cases = grouped("manual", cases)
    for _ in range(6):
        clients = ["k" + str(i) for i in range(rng.randint(1, 4))]
        requests = stream(rng, rng.randint(5, 120), clients, 50)
        cases.append(dict(case(requests, rng.randint(1, 5), rng.randint(1, 120)), group="random"))
    requests = stream(rng, params["n_max"], ["k1", "k2", "k3"], 30)
    cases.append(dict(case(requests, rng.randint(3, 20), rng.randint(100, 2000)), group="stress"))
    return cases
```

## Рубрика

- sliding-log: Для каждого клиента хранятся метки только принятых запросов в окне, а устаревшие удаляются с начала очереди
  + `deque` с `popleft()` для меток `<= t - window`
  - пересчёт по всему журналу на каждый запрос
- boundary: Граница окна соответствует полуинтервалу `(t − window, t]`: запрос с меткой ровно `t − window` уже не учитывается
  - `while recent and recent[0] < t - window`
- rejected-not-counted: Отклонённые запросы не попадают в счётчик окна
  - `recent.append(t)` до проверки лимита
- per-client: Состояние разделено по клиентам через словарь, клиенты не влияют друг на друга
  + `defaultdict(deque)`
- complexity: Амортизированно O(1) на запрос и память O(limit) на клиента, без сортировок и проходов по всей истории клиента
- memory-growth: Учтено, что словарь клиентов растёт без ограничения: пустые очереди удаляются, или в комментарии сказано, где хранить состояние с TTL в реальном сервисе
