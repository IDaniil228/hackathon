---
name: ml-softmax-cross-entropy
description: >-
  Численно устойчивые softmax и кросс-энтропия с label smoothing и ignore_index на NumPy
  или PyTorch: значение функции потерь и аналитический градиент по логитам
area: ml
grade: senior
skills: [python, numpy, pytorch, optimization]
kind: code
runtime: python-ml
io: function
difficulty: 7
expected_minutes: 40
params:
  batch_max: {type: int, min: 6, max: 8}
  domain:
    type: choice
    choices:
      - классификатор тематики обращений в поддержку
      - модель распознавания жестов по данным акселерометра
      - классификатор сортов растений по снимкам листьев
      - модель, предсказывающая следующий токен в небольшой языковой модели
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 1, required: true}
---

# Кросс-энтропия с label smoothing

## Генерация

Придумай легенду: {domain}. Команда пишет свою функцию потерь, потому что стандартная не подходит по формату логирования. В условии обязательно скажи:

- функция `softmax_cross_entropy(logits, targets, smoothing)` получает логиты `logits` — матрицу B × C чисел, — метки `targets` — список из B целых от 0 до C − 1 или −1, — и `smoothing` от 0 до 1;
- целевое распределение объекта — `(1 − smoothing)` на верном классе плюс `smoothing / C` на каждом классе; потеря объекта — кросс-энтропия между этим распределением и softmax от его логитов;
- объекты с меткой −1 игнорируются: они не входят в среднее и получают нулевой градиент; итоговая потеря — среднее по остальным объектам;
- вернуть пару `[потеря, градиент]`, где градиент — матрица B × C производных итоговой потери по логитам;
- если все метки равны −1, потеря 0.0 и градиент из нулей;
- логиты по модулю бывают до 1000, поэтому наивный `exp` переполнится; вычисления — в float64; объектов до {batch_max}, классов до 6;
- можно использовать NumPy или PyTorch; результат должен совпадать с `torch.nn.functional.cross_entropy(logits, targets, ignore_index=-1, label_smoothing=smoothing)`.

## Решения

### reference +

```python
import numpy as np


def softmax_cross_entropy(logits, targets, smoothing):
    targets = np.asarray(targets, dtype=np.int64)
    logits = np.asarray(logits, dtype=np.float64).reshape(len(targets), -1)
    classes = logits.shape[1]
    keep = targets != -1
    grad = np.zeros_like(logits)
    if not keep.any():
        return [0.0, grad.tolist()]
    shifted = logits - logits.max(axis=1, keepdims=True)
    log_probs = shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))
    target = np.full_like(logits, smoothing / classes)
    rows = np.flatnonzero(keep)
    target[rows, targets[rows]] += 1.0 - smoothing
    count = rows.size
    loss = -(target[rows] * log_probs[rows]).sum() / count
    grad[rows] = (np.exp(log_probs[rows]) - target[rows]) / count
    return [float(loss), grad.tolist()]
```

### smoothing-wrong-only -

Сглаживание только по неверным классам

```python
import numpy as np


def softmax_cross_entropy(logits, targets, smoothing):
    targets = np.asarray(targets, dtype=np.int64)
    logits = np.asarray(logits, dtype=np.float64).reshape(len(targets), -1)
    classes = logits.shape[1]
    keep = targets != -1
    grad = np.zeros_like(logits)
    if not keep.any():
        return [0.0, grad.tolist()]
    shifted = logits - logits.max(axis=1, keepdims=True)
    log_probs = shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))
    target = np.full_like(logits, smoothing / max(classes - 1, 1))
    rows = np.flatnonzero(keep)
    target[rows, targets[rows]] += 1.0 - smoothing
    count = rows.size
    loss = -(target[rows] * log_probs[rows]).sum() / count
    grad[rows] = (np.exp(log_probs[rows]) - target[rows]) / count
    return [float(loss), grad.tolist()]
```

### mean-over-batch -

Среднее по всему батчу, включая пропущенные строки

```python
import numpy as np


def softmax_cross_entropy(logits, targets, smoothing):
    targets = np.asarray(targets, dtype=np.int64)
    logits = np.asarray(logits, dtype=np.float64).reshape(len(targets), -1)
    classes = logits.shape[1]
    keep = targets != -1
    grad = np.zeros_like(logits)
    if not keep.any():
        return [0.0, grad.tolist()]
    shifted = logits - logits.max(axis=1, keepdims=True)
    log_probs = shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))
    target = np.full_like(logits, smoothing / classes)
    rows = np.flatnonzero(keep)
    target[rows, targets[rows]] += 1.0 - smoothing
    count = len(targets)
    loss = -(target[rows] * log_probs[rows]).sum() / count
    grad[rows] = (np.exp(log_probs[rows]) - target[rows]) / count
    return [float(loss), grad.tolist()]
```

## Сигнатура

```
softmax_cross_entropy
```

## Тесты

```python
import cmath

STEP = 1e-30


def total_loss(logits, targets, smoothing):
    # Работает и с комплексными логитами: сдвиг на вещественный максимум строки
    # не меняет log-sum-exp, поэтому комплексный шаг даёт точную производную.
    kept = [i for i, t in enumerate(targets) if t != -1]
    if not kept:
        return 0
    classes = len(logits[0])
    total = 0
    for i in kept:
        row = logits[i]
        top = max(v.real for v in row)
        lse = top + cmath.log(sum(cmath.exp(v - top) for v in row))
        for c, v in enumerate(row):
            weight = smoothing / classes + (1 - smoothing if c == targets[i] else 0)
            total -= weight * (v - lse)
    return total / len(kept)


def expected_for(logits, targets, smoothing):
    loss = total_loss(logits, targets, smoothing)
    grad = []
    for i, row in enumerate(logits):
        grad_row = []
        for c in range(len(row)):
            shifted = [list(r) for r in logits]
            shifted[i][c] = shifted[i][c] + 1j * STEP
            grad_row.append(total_loss(shifted, targets, smoothing).imag / STEP)
        grad.append(grad_row)
    return [float(complex(loss).real), grad]


def case(logits, targets, smoothing):
    return {
        "input": [logits, targets, smoothing],
        "expected": expected_for(logits, targets, smoothing),
    }


def random_case(rng, batch, classes, scale, ignore_share):
    logits = [[round(rng.uniform(-scale, scale), 2) for _ in range(classes)] for _ in range(batch)]
    targets = [-1 if rng.random() < ignore_share else rng.randrange(classes) for _ in range(batch)]
    return case(logits, targets, rng.choice([0.0, 0.05, 0.1, 0.2]))


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case([[2.0, 1.0, 0.1], [0.5, 2.5, 0.3]], [0, 1], 0.0),
        case([[2.0, 1.0, 0.1], [0.5, 2.5, 0.3]], [0, -1], 0.1),
        case([[1000.0, -1000.0], [-1000.0, 1000.0]], [0, 0], 0.0),
        case([[1000.0, 999.0, -1000.0]], [2], 0.1),
        case([[0.0, 0.0], [1.0, 2.0]], [-1, -1], 0.2),
        case([[5.0]], [0], 0.3),
        case([[0.0, 0.0, 0.0, 0.0]], [3], 1.0),
        case([[-800.0, -800.5, -799.0]], [1], 0.0),
    ]
    cases = grouped("manual", cases)
    for _ in range(4):
        random_one = random_case(rng, rng.randint(1, 5), rng.randint(2, 6), 10, 0.2)
        cases.append(dict(random_one, group="random"))
    cases.append(dict(random_case(rng, params["batch_max"], 6, 1000, 0.25), group="stress"))
    return cases
```

## Рубрика

- log-sum-exp: Логарифм softmax считается устойчиво: вычитание максимума строки и `log-sum-exp`, либо `log_softmax`, без вычисления вероятностей и последующего `log`
  + `shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))`
  - `np.log(np.exp(z) / np.exp(z).sum())`
- gradient: Градиент выведен аналитически: `(softmax − q) / N` для учитываемых объектов, где q — сглаженное целевое распределение, N — их число
  + `(np.exp(log_probs) - target) / count`
  - градиент через конечные разности или с делением на B вместо числа учитываемых объектов
- smoothing: Сглаживание распределено по всем C классам, включая верный: верный класс получает `1 − ε + ε / C`, как в PyTorch
  - `ε / (C − 1)` на неверные классы
- ignore-index: Объекты с меткой −1 исключены и из числителя, и из знаменателя среднего, их строки градиента нулевые; случай «все игнорируются» не делит на ноль
- vectorized: Вычисления векторные по батчу и классам, индексация верных классов — через продвинутую индексацию, а не циклы Python
  + `target[rows, targets[rows]] += 1.0 - smoothing`
- float64-torch: Если используется PyTorch, тензоры создаются в float64, а градиент берётся из autograd или формулы без смешивания с float32
  - `torch.tensor(logits)` без `dtype=torch.float64`
