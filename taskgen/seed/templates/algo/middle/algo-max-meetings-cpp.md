---
name: algo-max-meetings-cpp
description: >-
  Максимальное число непересекающихся полуинтервалов на C++: жадный выбор по раннему
  окончанию, касание концов, доказательство корректности жадного шага
area: algo
grade: middle
skills: [cpp, greedy, sorting]
kind: code
runtime: cpp
difficulty: 5
expected_minutes: 25
params:
  n_max: {type: int, min: 2000, max: 3000}
  domain:
    type: choice
    choices:
      - бронирование одной переговорной комнаты
      - расписание одного станка на заводе
      - эфирная сетка одного телеканала
      - запись к одному врачу на день
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 3, required: true}
---

# Наибольшее число встреч

## Генерация

Придумай легенду: {domain}. Есть заявки на время, ресурс один, и нужно удовлетворить как можно больше заявок. В условии обязательно скажи:

- функция на C++ имеет вид `int max_meetings(std::vector<std::pair<int, int>> meetings)`; заявка `{start, end}` занимает полуинтервал `[start, end)`, всегда `start < end`;
- две заявки совместимы, если их полуинтервалы не пересекаются; заявка, которая заканчивается ровно в момент начала другой, с ней совместима;
- вернуть наибольшее число попарно совместимых заявок;
- заявки могут совпадать и идти в любом порядке, время — целые числа от 0 до 10^6, заявок до {n_max}, список может быть пустым;
- функцию `main` писать не нужно, стандартные заголовки уже подключены.

## Эталон

```cpp
int max_meetings(std::vector<std::pair<int, int>> meetings) {
    std::sort(meetings.begin(), meetings.end(),
              [](const std::pair<int, int>& a, const std::pair<int, int>& b) {
                  return a.second < b.second;
              });
    int count = 0;
    long long free_from = std::numeric_limits<long long>::min();
    for (const auto& [start, end] : meetings) {
        if (start >= free_from) {
            count++;
            free_from = end;
        }
    }
    return count;
}
```

## Сигнатура

```
max_meetings
```

## Тесты

```python
from itertools import combinations


def compatible(chosen):
    ordered = sorted(chosen)
    return all(ordered[i][1] <= ordered[i + 1][0] for i in range(len(ordered) - 1))


def brute(meetings):
    for size in range(len(meetings), 0, -1):
        if any(compatible(c) for c in combinations(meetings, size)):
            return size
    return 0


def timeline(meetings):
    # Динамика по отсортированным моментам времени: best[t] — ответ для заявок,
    # которые заканчиваются не позже t. Жадный выбор здесь не используется.
    points = sorted({t for m in meetings for t in m})
    index = {t: i for i, t in enumerate(points)}
    ending = {}
    for start, end in meetings:
        ending.setdefault(end, []).append(start)
    best = [0] * len(points)
    for i, t in enumerate(points):
        best[i] = best[i - 1] if i else 0
        for start in ending.get(t, []):
            best[i] = max(best[i], best[index[start]] + 1)
    return best[-1] if points else 0


def case(meetings):
    expected = brute(meetings) if len(meetings) <= 10 else timeline(meetings)
    return {"input": [[list(m) for m in meetings]], "expected": expected}


def random_meetings(rng, n, horizon, longest):
    result = []
    for _ in range(n):
        start = rng.randint(0, horizon - 1)
        result.append((start, min(horizon, start + rng.randint(1, longest))))
    return result


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case([(9, 11), (10, 12), (11, 13), (13, 15)]),
        case([(1, 10), (2, 3), (4, 5), (6, 7)]),
        case([]),
        case([(5, 6)]),
        case([(1, 2), (2, 3), (3, 4)]),
        case([(1, 5), (1, 5), (1, 5)]),
        case([(0, 1000000), (0, 1), (999999, 1000000)]),
        case([(3, 8), (1, 4), (7, 9), (2, 3), (8, 10)]),
    ]
    cases = grouped("manual", cases)
    for _ in range(6):
        cases.append(dict(case(random_meetings(rng, rng.randint(2, 10), 20, 6)), group="random"))
    for _ in range(3):
        meetings = random_meetings(rng, rng.randint(50, 300), 1000, 60)
        cases.append(dict(case(meetings), group="random"))
    n = params["n_max"]
    cases.append(dict(case(random_meetings(rng, n, 10**6, 5000)), group="stress"))
    return cases
```

## Рубрика

- greedy-key: Заявки упорядочены по времени окончания, и каждая следующая выбирается, если начинается не раньше окончания последней выбранной
  + `sort` по `second`, затем `if (start >= free_from)`
  - сортировка по началу или по длительности заявки
- greedy-proof: В решении или пояснении есть довод, почему жадный выбор по раннему окончанию оптимален: замена первой заявки оптимального ответа на заявку с самым ранним концом не ухудшает ответ
- touching: Касание концов считается совместимостью: сравнение `start >= end_last`, а не строгое
  - `if (start > free_from)`
- initial-state: Начальное значение «свободно с» не отсекает заявки с началом 0 и не опирается на фиктивную заявку
  + `long long free_from = std::numeric_limits<long long>::min();` или флаг первой заявки
  - `int free_from = 0;` с условием `start > free_from`
- complexity: Сложность O(n log n) за счёт сортировки и одного прохода; нет перебора подмножеств и квадратичной динамики
- idiom: Используются средства стандартной библиотеки и структурные привязки или понятные имена полей вместо `first` и `second` в логике выбора
  + `for (const auto& [start, end] : meetings)`
