---
name: ml-standardize-features
description: >-
  Стандартизация признаков на NumPy: статистики только по обучающей выборке, выбор ddof,
  постоянные признаки и пустая тестовая выборка без утечки данных
area: ml
grade: junior
skills: [python, numpy, data-preprocessing]
kind: code
runtime: python-ml
difficulty: 3
expected_minutes: 20
params:
  rows_max: {type: int, min: 150, max: 250}
  domain:
    type: choice
    choices:
      - признаки квартир для модели оценки стоимости
      - показатели датчиков станка для модели предсказания поломки
      - медицинские показатели пациентов для модели риска
      - поведение пользователей приложения для модели оттока
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 1, required: true}
---

# Стандартизация признаков

## Генерация

Придумай легенду: {domain}. Нужно подготовить признаки так, как это делает `StandardScaler`, но своей функцией. В условии обязательно скажи:

- функция `standardize(train, test, ddof)` получает обучающую выборку `train` и тестовую `test` — списки строк одинаковой длины с числами, — и целое `ddof`, равное 0 или 1;
- для каждого столбца посчитать среднее и стандартное отклонение только по `train`, с поправкой `ddof` в знаменателе дисперсии (`n − ddof`), и преобразовать строки `test` по формуле `(x − среднее) / отклонение`;
- если отклонение столбца меньше `1e-12`, столбец считается постоянным, и все его значения в ответе равны 0.0;
- вернуть преобразованный `test` списком списков чисел; если в `test` нет строк, вернуть пустой список;
- в `train` не меньше двух строк и не больше {rows_max}; вычисления — в float64; можно использовать NumPy, но не scikit-learn.

Объясни в легенде, почему статистики нельзя считать по тестовой выборке.

## Решения

### reference +

```python
import numpy as np


def standardize(train, test, ddof):
    train = np.asarray(train, dtype=np.float64)
    # reshape нужен для пустого test: np.asarray([]) даёт одномерный массив.
    test = np.asarray(test, dtype=np.float64).reshape(-1, train.shape[1])
    mean = train.mean(axis=0)
    std = train.std(axis=0, ddof=ddof)
    constant = std < 1e-12
    scaled = (test - mean) / np.where(constant, 1.0, std)
    scaled[:, constant] = 0.0
    return scaled.tolist()
```

### ddof-ignored -

`ddof` проигнорирован

```python
import numpy as np


def standardize(train, test, ddof):
    train = np.asarray(train, dtype=np.float64)
    # reshape нужен для пустого test: np.asarray([]) даёт одномерный массив.
    test = np.asarray(test, dtype=np.float64).reshape(-1, train.shape[1])
    mean = train.mean(axis=0)
    std = train.std(axis=0, ddof=0)
    constant = std < 1e-12
    scaled = (test - mean) / np.where(constant, 1.0, std)
    scaled[:, constant] = 0.0
    return scaled.tolist()
```

### constant-column -

Постоянный столбец не обнулён

```python
import numpy as np


def standardize(train, test, ddof):
    train = np.asarray(train, dtype=np.float64)
    # reshape нужен для пустого test: np.asarray([]) даёт одномерный массив.
    test = np.asarray(test, dtype=np.float64).reshape(-1, train.shape[1])
    mean = train.mean(axis=0)
    std = train.std(axis=0, ddof=ddof)
    constant = std < 1e-12
    scaled = (test - mean) / np.where(constant, 1.0, std)
    return scaled.tolist()
```

## Сигнатура

```
standardize
```

## Тесты

```python
import math


def by_columns(train, test, ddof):
    columns = len(train[0])
    result = [[0.0] * columns for _ in test]
    for c in range(columns):
        values = [row[c] for row in train]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - ddof)
        std = math.sqrt(variance)
        if std < 1e-12:
            continue
        for r, row in enumerate(test):
            result[r][c] = (row[c] - mean) / std
    return result


def case(train, test, ddof):
    return {"input": [train, test, ddof], "expected": by_columns(train, test, ddof)}


def matrix(rng, rows, columns, constant_columns=()):
    data = []
    for _ in range(rows):
        row = []
        for c in range(columns):
            if c in constant_columns:
                row.append(7)
            else:
                row.append(round(rng.gauss(50 * (c + 1), 10 * (c + 1)), 3))
        data.append(row)
    return data


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case([[1, 10], [3, 30], [5, 50]], [[3, 20], [7, 70]], 0),
        case([[1, 10], [3, 30], [5, 50]], [[3, 20], [7, 70]], 1),
        case([[2, 5], [4, 5]], [[3, 5], [10, 6]], 0),
        case([[1.5], [2.5]], [], 1),
        case([[0, 0], [0, 0], [0, 0]], [[1, -1]], 1),
        case([[1e6, -3], [1e6 + 2, 3]], [[1e6 + 1, 0]], 0),
        case([[-1], [1]], [[0], [2], [-2]], 1),
    ]
    cases = grouped("manual", cases)
    for ddof in (0, 1):
        columns = rng.randint(2, 5)
        constant = {rng.randrange(columns)}
        train = matrix(rng, rng.randint(2, 20), columns, constant)
        test = matrix(rng, rng.randint(1, 10), columns)
        cases.append(dict(case(train, test, ddof), group="random"))
    columns = rng.randint(3, 5)
    train = matrix(rng, params["rows_max"], columns)
    test = matrix(rng, 40, columns)
    cases.append(dict(case(train, test, rng.choice([0, 1])), group="stress"))
    return cases
```

## Рубрика

- train-only: Среднее и отклонение считаются только по обучающей выборке, а тестовая лишь преобразуется
  - `np.vstack([train, test]).mean(axis=0)`
- ddof: Поправка `ddof` передаётся в расчёт отклонения, а не игнорируется и не заменяется константой
  + `train.std(axis=0, ddof=ddof)`
- constant-columns: Постоянные столбцы обработаны без деления на ноль, предупреждений и `nan` в ответе
  + `np.where(constant, 1.0, std)` и обнуление столбца
  - `(test - mean) / std` с последующей заменой `nan`
- vectorized: Вычисления векторные по осям массива, без вложенных циклов Python по строкам и столбцам
  + `mean(axis=0)`
- empty-test: Пустая тестовая выборка даёт пустой ответ без исключения из-за формы массива
  + `.reshape(-1, train.shape[1])`
