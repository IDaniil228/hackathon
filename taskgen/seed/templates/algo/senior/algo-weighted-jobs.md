---
name: algo-weighted-jobs
description: >-
  Взвешенный выбор непересекающихся работ с обязательным перерывом между ними:
  динамика по отсортированным работам и бинарный поиск предшественника за O(n log n)
area: algo
grade: senior
skills: [python, dynamic-programming, binary-search]
kind: code
runtime: python
io: function
difficulty: 7
expected_minutes: 35
params:
  n_max: {type: int, min: 1500, max: 2000}
  domain:
    type: choice
    choices:
      - заказы фрилансера, между которыми нужен отдых
      - рекламные слоты на одном экране с паузой между роликами
      - аренда одного автомобиля с обязательной мойкой между клиентами
      - задания для одного GPU-сервера с перезагрузкой между запусками
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 3, required: true}
---

# Самый выгодный набор работ

## Генерация

Придумай легенду: {domain}. В условии обязательно скажи:

- функция `max_profit(jobs, gap)` получает список работ `jobs`, где каждая работа — `[start, end, profit]` с целыми `0 <= start < end <= 10^6` и `1 <= profit <= 10^6`, и целое `gap` от 0 до 1000;
- исполнитель один и выполняет работы целиком; работа B может идти после работы A, только если `B.start >= A.end + gap`;
- вернуть наибольшую суммарную прибыль набора работ, которые можно выполнить; для пустого списка ответ 0;
- работы идут в произвольном порядке и могут совпадать; их до {n_max}, но решение должно быть рассчитано на 10^5 работ, поэтому нужна сложность O(n log n).

## Решения

### reference +

```python
import bisect


def max_profit(jobs, gap):
    ordered = sorted(jobs, key=lambda job: job[1])
    # Момент, с которого после работы можно начинать следующую; растёт вместе с end.
    free_at = [end + gap for _, end, _ in ordered]
    best = [0] * (len(ordered) + 1)
    for i, (start, _, profit) in enumerate(ordered):
        compatible = bisect.bisect_right(free_at, start, 0, i)
        best[i + 1] = max(best[i], best[compatible] + profit)
    return best[-1]
```

### boundary-dropped -

Работа на границе перерыва отброшена

```python
import bisect


def max_profit(jobs, gap):
    ordered = sorted(jobs, key=lambda job: job[1])
    # Момент, с которого после работы можно начинать следующую; растёт вместе с end.
    free_at = [end + gap for _, end, _ in ordered]
    best = [0] * (len(ordered) + 1)
    for i, (start, _, profit) in enumerate(ordered):
        compatible = bisect.bisect_left(free_at, start, 0, i)
        best[i + 1] = max(best[i], best[compatible] + profit)
    return best[-1]
```

### gap-ignored -

Перерыв не учтён

```python
import bisect


def max_profit(jobs, gap):
    ordered = sorted(jobs, key=lambda job: job[1])
    # Момент, с которого после работы можно начинать следующую; растёт вместе с end.
    free_at = [end for _, end, _ in ordered]
    best = [0] * (len(ordered) + 1)
    for i, (start, _, profit) in enumerate(ordered):
        compatible = bisect.bisect_right(free_at, start, 0, i)
        best[i + 1] = max(best[i], best[compatible] + profit)
    return best[-1]
```

## Сигнатура

```
max_profit
```

## Тесты

```python
def brute(jobs, gap):
    best = 0
    n = len(jobs)
    for mask in range(1 << n):
        chosen = sorted(jobs[i] for i in range(n) if mask >> i & 1)
        if all(chosen[k + 1][0] >= chosen[k][1] + gap for k in range(len(chosen) - 1)):
            best = max(best, sum(job[2] for job in chosen))
    return best


def timeline(jobs, gap):
    # Динамика по моментам времени: best[t] — лучшая прибыль, если исполнитель
    # освобождается не позже t. Сортировки работ и бинарного поиска здесь нет.
    points = sorted({t for s, e, _ in jobs for t in (s, e + gap)})
    index = {t: i for i, t in enumerate(points)}
    finishing = {}
    for start, end, profit in jobs:
        finishing.setdefault(end + gap, []).append((start, profit))
    best = [0] * len(points)
    for i, t in enumerate(points):
        best[i] = best[i - 1] if i else 0
        for start, profit in finishing.get(t, []):
            best[i] = max(best[i], best[index[start]] + profit)
    return best[-1] if points else 0


def case(jobs, gap):
    expected = brute(jobs, gap) if len(jobs) <= 12 else timeline(jobs, gap)
    return {"input": [[list(job) for job in jobs], gap], "expected": expected}


def random_jobs(rng, n, horizon, longest, top):
    jobs = []
    for _ in range(n):
        start = rng.randint(0, horizon - 1)
        end = min(horizon, start + rng.randint(1, longest))
        jobs.append((start, end, rng.randint(1, top)))
    return jobs


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    manual = [
        case([(1, 3, 50), (2, 5, 20), (4, 6, 70), (6, 7, 60)], 0),
        case([(1, 3, 50), (2, 5, 20), (4, 6, 70), (6, 7, 60)], 1),
        case([], 5),
        case([(0, 1000000, 1000000)], 1000),
        case([(0, 2, 5), (2, 4, 5)], 0),
        case([(0, 2, 5), (2, 4, 5)], 1),
        case([(0, 10, 100), (0, 3, 40), (4, 7, 40), (7, 10, 40)], 0),
        case([(5, 9, 7), (5, 9, 7), (5, 9, 3)], 0),
        case([(0, 1, 1), (1, 2, 1), (2, 3, 1), (0, 3, 2)], 0),
    ]
    randoms = []
    for _ in range(7):
        jobs = random_jobs(rng, rng.randint(2, 12), 30, 8, 20)
        randoms.append(case(jobs, rng.randint(0, 3)))
    # Уже 300 работ не по силам перебору подмножеств.
    stress = [case(random_jobs(rng, 300, 5000, 200, 1000), rng.randint(0, 50))]
    n = params["n_max"]
    stress.append(case(random_jobs(rng, n, 10**6, 20000, 10**6), rng.randint(0, 1000)))
    return grouped("manual", manual) + grouped("random", randoms) + grouped("stress", stress)
```

## Рубрика

- recurrence: Сформулирована верная рекуррентность: лучший ответ для первых i работ по времени окончания — максимум из «пропустить i-ю» и «взять i-ю плюс лучший ответ для работ, совместимых с ней»
  + `best[i + 1] = max(best[i], best[compatible] + profit)`
- predecessor-search: Совместимый префикс ищется бинарным поиском по отсортированным моментам освобождения, а не линейным проходом назад
  + `bisect.bisect_right(free_at, start, 0, i)`
  - цикл `for j in range(i - 1, -1, -1)` в поиске последней совместимой работы
- gap-boundary: Перерыв учтён на правильной границе: сравнивается `start` следующей работы с `end + gap` предыдущей, и равенство допустимо
  - `bisect_left` или строгое сравнение, из-за которого теряется работа, начинающаяся ровно в момент освобождения
- greedy-rejected: Решение не опирается на жадный выбор по раннему окончанию или по максимальной прибыли, который для весов неверен
- complexity: Итоговая сложность O(n log n) по времени и O(n) по памяти; нет рекурсии глубиной n без мемоизации и перебора подмножеств
- clarity: Структура понятна: сортировка, вспомогательный массив границ и одномерная таблица динамики с ясным смыслом индекса
  + комментарий или имя, объясняющее смысл `best[i]`
  - таблица `dp[i][j]` без объяснения второго измерения
