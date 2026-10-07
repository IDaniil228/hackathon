---
name: ml-logreg-gradient-step
description: >-
  Один шаг градиентного спуска для логистической регрессии с L2-регуляризацией на NumPy:
  вывод градиента кросс-энтропии, свободный член без штрафа, усреднение по батчу
area: ml
grade: middle
skills: [python, numpy, optimization]
kind: code
runtime: python-ml
difficulty: 5
expected_minutes: 30
params:
  rows_max: {type: int, min: 150, max: 250}
  domain:
    type: choice
    choices:
      - модель отклика клиентов на рассылку
      - модель кликов по рекламному баннеру
      - модель одобрения заявки на кредит
      - модель обнаружения подозрительных входов в аккаунт
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 1, required: true}
---

# Шаг градиентного спуска логистической регрессии

## Генерация

Придумай легенду: {domain}. Команда пишет обучение логистической регрессии вручную и просит реализовать один шаг. В условии обязательно скажи:

- функция `logreg_step(X, y, w, b, lr, l2)` получает матрицу признаков `X` (n строк по d чисел), метки `y` из 0 и 1, веса `w` длины d, свободный член `b`, шаг `lr` и коэффициент регуляризации `l2`;
- функция потерь — средняя по n объектам бинарная кросс-энтропия от вероятностей `σ(X·w + b)` плюс `l2 / 2 · ||w||²`; свободный член не регуляризуется;
- нужно сделать один шаг градиентного спуска `w ← w − lr · ∂L/∂w`, `b ← b − lr · ∂L/∂b` и вернуть пару `[новые веса списком, новый свободный член]`;
- вычисления — в float64, ответ сравнивается с точностью до относительной погрешности 1e-6; строк до {rows_max}, признаков до 6;
- можно использовать NumPy и PyTorch, но не готовые модели scikit-learn.

## Эталон

```python
import numpy as np


def logreg_step(X, y, w, b, lr, l2):
    w = np.asarray(w, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    X = np.asarray(X, dtype=np.float64).reshape(len(y), len(w))
    z = X @ w + b
    p = 0.5 * (1.0 + np.tanh(0.5 * z))  # σ(z) без переполнения exp при больших |z|.
    error = p - y
    grad_w = X.T @ error / len(y) + l2 * w
    grad_b = error.mean()
    return [(w - lr * grad_w).tolist(), float(b - lr * grad_b)]
```

## Сигнатура

```
logreg_step
```

## Тесты

```python
import cmath

STEP = 1e-30


def loss(X, y, w, b, l2):
    # Работает и с комплексными весами: нужен для производной через комплексный шаг.
    total = 0
    for row, label in zip(X, y):
        z = sum(x * wj for x, wj in zip(row, w)) + b
        total += cmath.log(1 + cmath.exp(-z)) if label else cmath.log(1 + cmath.exp(z))
    return total / len(y) + l2 / 2 * sum(wj * wj for wj in w)


def derivative(f, point, j):
    # Комплексный шаг: Im f(x + ih·e_j) / h — производная без вычитания близких чисел.
    shifted = list(point)
    shifted[j] = shifted[j] + 1j * STEP
    return f(shifted).imag / STEP


def step(X, y, w, b, lr, l2):
    def by_params(params):
        return loss(X, y, params[:-1], params[-1], l2)

    point = list(w) + [b]
    grads = [derivative(by_params, point, j) for j in range(len(point))]
    return [[wj - lr * g for wj, g in zip(w, grads)], b - lr * grads[-1]]


def case(X, y, w, b, lr, l2):
    return {"input": [X, y, w, b, lr, l2], "expected": step(X, y, w, b, lr, l2)}


def random_case(rng, n, d):
    X = [[round(rng.uniform(-3, 3), 3) for _ in range(d)] for _ in range(n)]
    w = [round(rng.uniform(-1, 1), 3) for _ in range(d)]
    y = [1 if rng.random() < 0.5 else 0 for _ in range(n)]
    lr = rng.choice([0.01, 0.1, 0.5, 1.0])
    l2 = rng.choice([0.0, 0.001, 0.1, 1.0])
    return case(X, y, w, round(rng.uniform(-1, 1), 3), lr, l2)


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case([[1.0, 2.0], [-1.0, 0.5], [0.0, -1.0]], [1, 0, 1], [0.1, -0.2], 0.0, 0.5, 0.0),
        case([[1.0, 2.0], [-1.0, 0.5], [0.0, -1.0]], [1, 0, 1], [0.1, -0.2], 0.0, 0.5, 0.1),
        case([[0.0]], [1], [0.0], 0.0, 1.0, 0.0),
        case([[2.0, -1.0]], [0], [0.5, 0.5], 1.0, 0.1, 1.0),
        case([[1.0], [1.0]], [1, 0], [3.0], -3.0, 0.2, 0.5),
        case([[10.0, -10.0], [-10.0, 10.0]], [1, 1], [1.0, -1.0], 0.0, 0.01, 0.0),
        case([[0.0, 0.0, 0.0]], [0], [0.0, 0.0, 0.0], 0.0, 1.0, 0.0),
    ]
    cases = grouped("manual", cases)
    for _ in range(5):
        cases.append(dict(random_case(rng, rng.randint(1, 20), rng.randint(1, 6)), group="random"))
    cases.append(dict(random_case(rng, params["rows_max"], 5), group="stress"))
    return cases
```

## Рубрика

- gradient: Градиент выведен верно: `Xᵀ(σ(Xw + b) − y) / n + l2 · w` для весов и среднее `σ − y` для свободного члена
  + `X.T @ (p - y) / n + l2 * w`
  - градиент без деления на n или с `y - p` и знаком плюс в шаге
- bias-unregularized: Свободный член не штрафуется: `l2` добавляется только к градиенту весов
  - `grad_b = error.mean() + l2 * b`
- stable-sigmoid: Сигмоида вычисляется устойчиво: через `tanh`, `scipy.special.expit`, `torch.sigmoid` или ветвление по знаку, без переполнения `exp`
  - `1 / (1 + np.exp(-z))` без защиты от больших отрицательных `z`
- vectorized: Шаг посчитан матричными операциями без цикла Python по объектам
  + `X @ w + b`
- float64: Вычисления идут в float64; при использовании PyTorch тензоры создаются с `dtype=torch.float64`
  - `torch.tensor(X)` из списков float, который даёт float32
- output-shape: Возвращается пара `[список весов, число]`, без лишней вложенности и массивов формы (d, 1)
  - `w.reshape(-1, 1).tolist()`
