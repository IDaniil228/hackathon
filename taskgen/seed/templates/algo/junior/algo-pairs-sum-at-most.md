---
name: algo-pairs-sum-at-most
description: >-
  Подсчёт пар элементов отсортированного массива, сумма которых не превышает порог,
  быстрее квадратичного перебора: два указателя или бинарный поиск
area: algo
grade: junior
skills: [python, two-pointers]
kind: code
runtime: python
difficulty: 3
expected_minutes: 20
params:
  n_max: {type: int, min: 5000, max: 7000}
  domain:
    type: choice
    choices:
      - цены товаров в корзине интернет-магазина
      - веса посылок, которые курьер может взять парой
      - длительности песен для двухтрекового сета
      - время выполнения задач, которые запускают парами на одном сервере
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 3, required: true}
---

# Пары с суммой не больше порога

## Генерация

Придумай короткую легенду, где массив чисел — это {domain}, а порог — ограничение из той же легенды. В условии обязательно скажи:

- функция получает список целых чисел `values`, упорядоченный по неубыванию, и целое число `limit`;
- нужно вернуть количество пар индексов `i < j`, для которых `values[i] + values[j] <= limit`; пары считаются по индексам, поэтому одинаковые значения на разных позициях дают разные пары;
- числа могут повторяться и быть отрицательными, по модулю не больше 10^9; `limit` тоже может быть отрицательным;
- длина списка — от 0 до {n_max}; ожидается решение быстрее перебора всех пар;
- входной список изменять нельзя.

## Решения

### reference +

```python
def count_pairs(values, limit):
    left, right = 0, len(values) - 1
    count = 0
    while left < right:
        if values[left] + values[right] <= limit:
            # С values[left] подходит любой элемент между left и right включительно.
            count += right - left
            left += 1
        else:
            right -= 1
    return count
```

### strict-limit -

Строгое сравнение с порогом

```python
def count_pairs(values, limit):
    left, right = 0, len(values) - 1
    count = 0
    while left < right:
        if values[left] + values[right] < limit:
            # С values[left] подходит любой элемент между left и right включительно.
            count += right - left
            left += 1
        else:
            right -= 1
    return count
```

### count-one-pair -

Пары считаются по одной

```python
def count_pairs(values, limit):
    left, right = 0, len(values) - 1
    count = 0
    while left < right:
        if values[left] + values[right] <= limit:
            # С values[left] подходит любой элемент между left и right включительно.
            count += 1
            left += 1
        else:
            right -= 1
    return count
```

## Сигнатура

```
count_pairs
```

## Тесты

```python
import bisect

SMALL = 60


def brute(values, limit):
    n = len(values)
    return sum(1 for i in range(n) for j in range(i + 1, n) if values[i] + values[j] <= limit)


def by_bisect(values, limit):
    # Для каждого i — число j > i со значением не больше limit - values[i].
    total = 0
    for i, value in enumerate(values):
        total += max(0, bisect.bisect_right(values, limit - value, i + 1) - (i + 1))
    return total


def case(values, limit):
    values = sorted(values)
    expected = brute(values, limit) if len(values) <= SMALL else by_bisect(values, limit)
    return {"input": [values, limit], "expected": expected}


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case([1, 2, 3, 4, 5], 6),
        case([-3, -1, 0, 2, 2, 7], 1),
        case([], 10),
        case([5], 10),
        case([4, 4, 4, 4], 8),
        case([4, 4, 4, 4], 7),
        case([1, 2, 3], 100),
        case([-(10**9), -(10**9), 10**9, 10**9], -2 * 10**9),
        case([-5, -5, -5], -11),
    ]
    cases = grouped("manual", cases)
    for _ in range(8):
        n = rng.randint(2, SMALL)
        values = [rng.randint(-50, 50) for _ in range(n)]
        cases.append(dict(case(values, rng.randint(-60, 60)), group="random"))
    big = [rng.randint(-(10**4), 10**4) for _ in range(params["n_max"])]
    cases.append(dict(case(big, rng.randint(-5000, 5000)), group="stress"))
    return cases
```

## Рубрика

- complexity: Решение работает за O(n) или O(n log n) и не перебирает все пары индексов
  + два указателя `left`, `right`, сходящиеся к середине, или `bisect` для каждого элемента
  - вложенный цикл `for j in range(i + 1, n)` по всем парам
- batch-count: Когда пара `left`, `right` подходит, решение засчитывает сразу все пары с текущим левым элементом, а не по одной
  + `count += right - left`
  - `count += 1` с последующим сдвигом одного указателя, из-за которого часть пар теряется или считается повторно
- uses-order: Решение опирается на упорядоченность входа и не сортирует его заново и не меняет на месте
  - `values.sort()` внутри функции
- edges: Пустой список и список из одного элемента обрабатываются без особых случаев с выходом за границы индексов
  + условие цикла `while left < right` само даёт 0 для длины 0 и 1
  - обращение к `values[0]` до проверки длины
- naming: Имена переменных говорят о смысле, а не о типе: указатели и счётчик понятны без комментария
  + `left`, `right`, `count`
  - `a`, `b`, `c`, `tmp`
