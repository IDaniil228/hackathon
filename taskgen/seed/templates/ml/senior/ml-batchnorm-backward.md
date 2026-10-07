---
name: ml-batchnorm-backward
description: >-
  Обратный проход батч-нормализации в режиме обучения на NumPy: градиенты по входу,
  масштабу и сдвигу, вывод через вычислительный граф, постоянные признаки и батч из одного объекта
area: ml
grade: senior
skills: [python, numpy, optimization]
kind: code
runtime: python-ml
difficulty: 7
expected_minutes: 40
params:
  batch_max: {type: int, min: 12, max: 16}
  domain:
    type: choice
    choices:
      - собственный фреймворк для обучения на микроконтроллере
      - учебная библиотека автоматического дифференцирования
      - слой нормализации для модели прогноза спроса
      - нестандартный слой для модели распознавания речи
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 1, required: true}
---

# Обратный проход батч-нормализации

## Генерация

Придумай легенду: {domain}. Прямой проход уже написан, нужен обратный. В условии обязательно скажи:

- прямой проход в режиме обучения: для каждого признака j по батчу из N объектов считаются среднее `μ_j` и смещённая дисперсия `σ²_j` (деление на N), затем `x̂ = (x − μ) / sqrt(σ² + eps)` и `y = gamma · x̂ + beta`;
- функция `batchnorm_backward(x, gamma, dout, eps)` получает вход `x` — матрицу N × D, — вектор `gamma` длины D, градиент по выходу `dout` той же формы, что `x`, и `eps > 0`;
- вернуть тройку `[dx, dgamma, dbeta]`: градиенты по `x` (N × D), по `gamma` и по `beta` (по D чисел);
- учитывать, что `μ` и `σ²` зависят от `x`; признак может быть постоянным по батчу, N может быть равно 1;
- вычисления — в float64, сравнение с точностью до относительной погрешности 1e-6; N до {batch_max}, D до 6; можно использовать NumPy или PyTorch, но если используется autograd, градиент нужно уметь объяснить формулой.

## Решения

### reference +

```python
import numpy as np


def batchnorm_backward(x, gamma, dout, eps):
    x = np.asarray(x, dtype=np.float64)
    gamma = np.asarray(gamma, dtype=np.float64)
    dout = np.asarray(dout, dtype=np.float64).reshape(x.shape)
    n = x.shape[0]
    inv_std = 1.0 / np.sqrt(x.var(axis=0) + eps)
    x_hat = (x - x.mean(axis=0)) * inv_std
    dbeta = dout.sum(axis=0)
    dgamma = (dout * x_hat).sum(axis=0)
    dx_hat = dout * gamma
    # Сокращённая форма: вклады через μ и σ² сведены к двум суммам по батчу.
    dx = inv_std / n * (n * dx_hat - dx_hat.sum(axis=0) - x_hat * (dx_hat * x_hat).sum(axis=0))
    return [dx.tolist(), dgamma.tolist(), dbeta.tolist()]
```

### unbiased-var -

Несмещённая дисперсия

```python
import numpy as np


def batchnorm_backward(x, gamma, dout, eps):
    x = np.asarray(x, dtype=np.float64)
    gamma = np.asarray(gamma, dtype=np.float64)
    dout = np.asarray(dout, dtype=np.float64).reshape(x.shape)
    n = x.shape[0]
    inv_std = 1.0 / np.sqrt(x.var(axis=0, ddof=1) + eps)
    x_hat = (x - x.mean(axis=0)) * inv_std
    dbeta = dout.sum(axis=0)
    dgamma = (dout * x_hat).sum(axis=0)
    dx_hat = dout * gamma
    # Сокращённая форма: вклады через μ и σ² сведены к двум суммам по батчу.
    dx = inv_std / n * (n * dx_hat - dx_hat.sum(axis=0) - x_hat * (dx_hat * x_hat).sum(axis=0))
    return [dx.tolist(), dgamma.tolist(), dbeta.tolist()]
```

### no-mean-var-terms -

Нет вкладов через среднее и дисперсию

```python
import numpy as np


def batchnorm_backward(x, gamma, dout, eps):
    x = np.asarray(x, dtype=np.float64)
    gamma = np.asarray(gamma, dtype=np.float64)
    dout = np.asarray(dout, dtype=np.float64).reshape(x.shape)
    n = x.shape[0]
    inv_std = 1.0 / np.sqrt(x.var(axis=0) + eps)
    x_hat = (x - x.mean(axis=0)) * inv_std
    dbeta = dout.sum(axis=0)
    dgamma = (dout * x_hat).sum(axis=0)
    dx_hat = dout * gamma
    dx = inv_std / n * (n * dx_hat)
    return [dx.tolist(), dgamma.tolist(), dbeta.tolist()]
```

## Сигнатура

```
batchnorm_backward
```

## Тесты

```python
import cmath

STEP = 1e-30


def objective(x, gamma, beta, dout, eps):
    # Скалярная функция Σ dout · y: её производные по x, gamma и beta — искомые градиенты.
    # Только аналитические операции, поэтому она верна и для комплексного шага.
    n, d = len(x), len(x[0])
    total = 0
    for j in range(d):
        column = [x[i][j] for i in range(n)]
        mean = sum(column) / n
        variance = sum((v - mean) * (v - mean) for v in column) / n
        scale = 1 / cmath.sqrt(variance + eps)
        for i in range(n):
            total += dout[i][j] * (gamma[j] * (column[i] - mean) * scale + beta[j])
    return total


def expected_for(x, gamma, dout, eps):
    n, d = len(x), len(x[0])
    beta = [0.0] * d

    def bumped(values, index):
        copy = list(values)
        copy[index] = copy[index] + 1j * STEP
        return copy

    dx = []
    for i in range(n):
        row = []
        for j in range(d):
            shifted = [bumped(x[k], j) if k == i else x[k] for k in range(n)]
            row.append(objective(shifted, gamma, beta, dout, eps).imag / STEP)
        dx.append(row)
    dgamma = [objective(x, bumped(gamma, j), beta, dout, eps).imag / STEP for j in range(d)]
    dbeta = [objective(x, gamma, bumped(beta, j), dout, eps).imag / STEP for j in range(d)]
    return [dx, dgamma, dbeta]


def case(x, gamma, dout, eps):
    return {"input": [x, gamma, dout, eps], "expected": expected_for(x, gamma, dout, eps)}


def matrix(rng, n, d, scale):
    return [[round(rng.uniform(-scale, scale), 3) for _ in range(d)] for _ in range(n)]


def random_case(rng, n, d):
    x = matrix(rng, n, d, rng.choice([1, 10, 100]))
    if d > 1 and rng.random() < 0.5:
        constant = rng.randrange(d)
        for row in x:
            row[constant] = 2.5
    gamma = [round(rng.uniform(0.5, 2.0), 3) for _ in range(d)]
    return case(x, gamma, matrix(rng, n, d, 1), rng.choice([1e-5, 1e-3, 0.1]))


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case(
            [[1.0, 2.0], [3.0, 6.0], [5.0, 4.0]],
            [1.0, 0.5],
            [[0.1, -0.2], [0.3, 0.1], [-0.5, 0.4]],
            1e-5,
        ),
        case([[1.0], [2.0]], [2.0], [[1.0], [0.0]], 0.1),
        case([[4.0, -1.0, 7.0]], [1.0, 1.0, 1.0], [[0.5, -0.5, 2.0]], 1e-5),
        case([[3.0], [3.0], [3.0]], [1.5], [[1.0], [2.0], [-0.5]], 1e-3),
        case([[0.0, 1.0], [0.0, -1.0]], [1.0, 1.0], [[1.0, 1.0], [1.0, 1.0]], 1e-5),
        case(
            [[100.0, 0.001], [-100.0, 0.002], [50.0, 0.003]],
            [0.1, 3.0],
            [[1.0, -1.0], [2.0, 0.5], [0.0, 0.25]],
            1e-5,
        ),
    ]
    cases = grouped("manual", cases)
    for _ in range(5):
        cases.append(dict(random_case(rng, rng.randint(2, 8), rng.randint(1, 4)), group="random"))
    cases.append(dict(random_case(rng, params["batch_max"], 6), group="stress"))
    return cases
```

## Рубрика

- full-graph: Учтены все три пути влияния x на выход: напрямую, через среднее и через дисперсию; итоговая формула эквивалентна `(1/N)·inv_std·(N·dx̂ − Σdx̂ − x̂·Σ(dx̂·x̂))`
  - градиент `dout * gamma * inv_std` без вкладов через μ и σ²
- param-grads: Градиенты по параметрам верны: `dbeta = Σ dout`, `dgamma = Σ dout · x̂` по оси батча
- biased-variance: Используется смещённая дисперсия с делением на N, согласованная с прямым проходом
  - `x.var(axis=0, ddof=1)`
- stability: Постоянный признак и N = 1 не дают деления на ноль: `eps` добавлен под корень до деления, лишних делений на дисперсию нет
- vectorized: Вычисления векторные по батчу, промежуточные величины прямого прохода переиспользуются
  + `x_hat`, `inv_std` посчитаны один раз и входят в оба градиента
- derivation: Вывод формулы объяснён в комментариях или коротком пояснении через цепное правило, а не только получен autograd
