---
name: algo-top-k-ranking-cpp
description: >-
  Отбор k лучших участников рейтинга на C++: сортировка по убыванию баллов с разрешением
  ничьих по имени, частичная сортировка и обработка k вне диапазона
area: algo
grade: junior
skills: [cpp, sorting]
kind: code
runtime: cpp
io: function
difficulty: 3
expected_minutes: 20
params:
  n_max: {type: int, min: 1500, max: 2500}
  domain:
    type: choice
    choices:
      - таблица лидеров онлайн-игры
      - рейтинг участников олимпиады по программированию
      - список продавцов маркетплейса по числу продаж за месяц
      - рейтинг авторов статей по числу лайков
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 3, required: true}
---

# Лучшие k участников

## Генерация

Придумай легенду, где нужно показать первых k строк рейтинга; область — {domain}. В условии обязательно скажи:

- функция на C++ имеет вид `std::vector<std::string> top_k(std::vector<std::string> names, std::vector<int> scores, int k)`; `names[i]` и `scores[i]` относятся к одному участнику;
- вернуть имена не более чем k участников в порядке рейтинга: по убыванию баллов, а при равных баллах — по возрастанию имени в лексикографическом порядке;
- если k не больше нуля, вернуть пустой список; если k больше числа участников, вернуть всех;
- имена состоят из строчных латинских букв, могут повторяться; баллы — целые от -10^6 до 10^6;
- участников до {n_max}; функцию `main` писать не нужно, стандартные заголовки уже подключены.

## Решения

### reference +

```cpp
std::vector<std::string> top_k(std::vector<std::string> names, std::vector<int> scores, int k) {
    std::vector<int> order(names.size());
    std::iota(order.begin(), order.end(), 0);
    auto ranks_higher = [&](int a, int b) {
        if (scores[a] != scores[b]) return scores[a] > scores[b];
        return names[a] < names[b];
    };
    size_t take = std::min(static_cast<size_t>(std::max(k, 0)), order.size());
    // Упорядочиваются только первые take позиций: O(n log k) вместо полной сортировки.
    std::partial_sort(order.begin(), order.begin() + take, order.end(), ranks_higher);
    std::vector<std::string> result;
    result.reserve(take);
    for (size_t i = 0; i < take; i++) result.push_back(names[order[i]]);
    return result;
}
```

### ties-unordered -

Ничьи не упорядочены по имени

```cpp
std::vector<std::string> top_k(std::vector<std::string> names, std::vector<int> scores, int k) {
    std::vector<int> order(names.size());
    std::iota(order.begin(), order.end(), 0);
    auto ranks_higher = [&](int a, int b) {
        if (scores[a] != scores[b]) return scores[a] > scores[b];
        return false;
    };
    size_t take = std::min(static_cast<size_t>(std::max(k, 0)), order.size());
    // Упорядочиваются только первые take позиций: O(n log k) вместо полной сортировки.
    std::partial_sort(order.begin(), order.begin() + take, order.end(), ranks_higher);
    std::vector<std::string> result;
    result.reserve(take);
    for (size_t i = 0; i < take; i++) result.push_back(names[order[i]]);
    return result;
}
```

### negative-k -

Отрицательное k не отсечено

```cpp
std::vector<std::string> top_k(std::vector<std::string> names, std::vector<int> scores, int k) {
    std::vector<int> order(names.size());
    std::iota(order.begin(), order.end(), 0);
    auto ranks_higher = [&](int a, int b) {
        if (scores[a] != scores[b]) return scores[a] > scores[b];
        return names[a] < names[b];
    };
    size_t take = std::min(static_cast<size_t>(k), order.size());
    // Упорядочиваются только первые take позиций: O(n log k) вместо полной сортировки.
    std::partial_sort(order.begin(), order.begin() + take, order.end(), ranks_higher);
    std::vector<std::string> result;
    result.reserve(take);
    for (size_t i = 0; i < take; i++) result.push_back(names[order[i]]);
    return result;
}
```

## Сигнатура

```
top_k
```

## Тесты

```python
LETTERS = "abcdefghijklmnopqrstuvwxyz"


def select_top(names, scores, k):
    # Выбор лучшего из оставшихся k раз подряд, без сортировки.
    left = list(range(len(names)))
    chosen = []
    for _ in range(max(0, min(k, len(names)))):
        best = left[0]
        for i in left[1:]:
            if (scores[i], names[best]) > (scores[best], names[i]):
                best = i
        left.remove(best)
        chosen.append(names[best])
    return chosen


def case(names, scores, k):
    if len(names) <= 200 or k <= 30:
        expected = select_top(names, scores, k)
    else:
        order = sorted(range(len(names)), key=lambda i: (-scores[i], names[i]))
        expected = [names[i] for i in order[:k]]
    return {"input": [names, scores, k], "expected": expected}


def word(rng, low=3, high=8):
    return "".join(rng.choice(LETTERS) for _ in range(rng.randint(low, high)))


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case(["anna", "boris", "vera", "gleb"], [70, 95, 70, 88], 3),
        case(["max", "lea", "kim"], [10, 10, 10], 2),
        case([], [], 3),
        case(["solo"], [5], 0),
        case(["solo"], [5], -2),
        case(["b", "a", "c"], [1, 2, 3], 10),
        case(["ab", "a", "abc", "b"], [7, 7, 7, 7], 4),
        case(["neg", "zero", "pos"], [-1000000, 0, 1000000], 2),
        case(["same", "same", "other"], [3, 3, 3], 3),
    ]
    cases = grouped("manual", cases)
    for _ in range(6):
        n = rng.randint(1, 40)
        names = [word(rng, 1, 3) for _ in range(n)]
        scores = [rng.randint(-5, 5) for _ in range(n)]
        cases.append(dict(case(names, scores, rng.randint(-1, n + 2)), group="random"))
    n = params["n_max"]
    names = [word(rng) for _ in range(n)]
    scores = [rng.randint(-100, 100) for _ in range(n)]
    cases.append(dict(case(names, scores, rng.randint(1, 25)), group="stress"))
    cases.append(dict(case(names[:600], scores[:600], 600), group="stress"))
    return cases
```

## Рубрика

- comparator: Компаратор задаёт строгий порядок: сначала баллы по убыванию, при равенстве имя по возрастанию, и не возвращает true для равных элементов
  + `if (scores[a] != scores[b]) return scores[a] > scores[b]; return names[a] < names[b];`
  - `return scores[a] >= scores[b];` — нарушает строгий слабый порядок и даёт неопределённое поведение `std::sort`
- pairing: Имя и балл участника переставляются вместе: сортируются индексы или пары, а не два массива по отдельности
  + сортировка вектора индексов или `std::vector<std::pair<int, std::string>>`
  - отдельные `std::sort(scores...)` и `std::sort(names...)`
- k-bounds: Значения k меньше единицы и больше числа участников обрабатываются без выхода за границы и без приведения отрицательного int к size_t
  + `std::min(static_cast<size_t>(std::max(k, 0)), order.size())`
  - `names.begin() + k` без проверки k
- partial: Решение не делает лишней работы: частичная сортировка, `nth_element` или куча для малых k, либо полная сортировка с обоснованием
  + `std::partial_sort(order.begin(), order.begin() + take, order.end(), cmp)`
- copies: Большие объекты не копируются без нужды: лямбда захватывает по ссылке, строки в цикле не копируются по значению
  + `[&](int a, int b)`
  - `[=](int a, int b)` копирует оба вектора в лямбду, а сортировка копирует и сам компаратор
