---
name: ml-kmeans-step
description: >-
  Одна итерация алгоритма Ллойда для k-средних на NumPy: векторное назначение кластеров,
  ничьи по расстоянию, пустые кластеры и инерция
area: ml
grade: middle
skills: [python, numpy, clustering]
kind: code
runtime: python-ml
difficulty: 5
expected_minutes: 30
params:
  points_max: {type: int, min: 1200, max: 1800}
  domain:
    type: choice
    choices:
      - сегментация покупателей по двум-трём поведенческим признакам
      - размещение пунктов выдачи заказов по координатам клиентов
      - группировка датчиков по средним показаниям
      - сжатие палитры изображения до k цветов
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 1, required: true}
---

# Итерация k-средних

## Генерация

Придумай легенду: {domain}. Нужно реализовать одну итерацию алгоритма k-средних. В условии обязательно скажи:

- функция `kmeans_step(points, centroids)` получает список точек `points` и список центров `centroids`; каждая точка и каждый центр — список из d целых чисел, d от 1 до 4;
- каждая точка назначается ближайшему центру по евклидову расстоянию; при равенстве расстояний — центру с меньшим индексом;
- новый центр кластера — среднее его точек; центр, которому не досталось ни одной точки, остаётся на месте;
- инерция — сумма квадратов расстояний от точек до назначенных им старых центров;
- вернуть пару `[новые центры списком списков чисел, инерция]`;
- точек от 1 до {points_max}, центров от 1 до 8, координаты по модулю не больше 1000; можно использовать NumPy, но не scikit-learn.

## Решения

### reference +

```python
import numpy as np


def kmeans_step(points, centroids):
    points = np.asarray(points, dtype=np.float64)
    centroids = np.asarray(centroids, dtype=np.float64)
    squared = ((points[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
    labels = squared.argmin(axis=1)  # argmin берёт первый минимум: ничья к меньшему индексу.
    inertia = squared[np.arange(len(points)), labels].sum()
    counts = np.bincount(labels, minlength=len(centroids))
    sums = np.zeros_like(centroids)
    np.add.at(sums, labels, points)
    filled = counts > 0
    updated = centroids.copy()
    updated[filled] = sums[filled] / counts[filled, None]
    return [updated.tolist(), float(inertia)]
```

### tie-to-last -

Ничья уходит к последнему центру

```python
import numpy as np


def kmeans_step(points, centroids):
    points = np.asarray(points, dtype=np.float64)
    centroids = np.asarray(centroids, dtype=np.float64)
    squared = ((points[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
    labels = len(centroids) - 1 - squared[:, ::-1].argmin(axis=1)
    inertia = squared[np.arange(len(points)), labels].sum()
    counts = np.bincount(labels, minlength=len(centroids))
    sums = np.zeros_like(centroids)
    np.add.at(sums, labels, points)
    filled = counts > 0
    updated = centroids.copy()
    updated[filled] = sums[filled] / counts[filled, None]
    return [updated.tolist(), float(inertia)]
```

### empty-zeroed -

Пустой кластер обнуляется

```python
import numpy as np


def kmeans_step(points, centroids):
    points = np.asarray(points, dtype=np.float64)
    centroids = np.asarray(centroids, dtype=np.float64)
    squared = ((points[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
    labels = squared.argmin(axis=1)  # argmin берёт первый минимум: ничья к меньшему индексу.
    inertia = squared[np.arange(len(points)), labels].sum()
    counts = np.bincount(labels, minlength=len(centroids))
    sums = np.zeros_like(centroids)
    np.add.at(sums, labels, points)
    updated = sums / np.maximum(counts, 1)[:, None]
    return [updated.tolist(), float(inertia)]
```

## Сигнатура

```
kmeans_step
```

## Тесты

```python
def lloyd(points, centroids):
    groups = [[] for _ in centroids]
    inertia = 0
    for p in points:
        best, best_distance = 0, None
        for k, c in enumerate(centroids):
            distance = sum((a - b) ** 2 for a, b in zip(p, c))
            if best_distance is None or distance < best_distance:
                best, best_distance = k, distance
        groups[best].append(p)
        inertia += best_distance
    updated = []
    for c, group in zip(centroids, groups):
        if group:
            updated.append([sum(p[i] for p in group) / len(group) for i in range(len(c))])
        else:
            updated.append([float(x) for x in c])
    return [updated, float(inertia)]


def case(points, centroids):
    return {"input": [points, centroids], "expected": lloyd(points, centroids)}


def cloud(rng, n, d, k, spread):
    centers = [[rng.randint(-800, 800) for _ in range(d)] for _ in range(k)]
    result = []
    for _ in range(n):
        base = rng.choice(centers)
        result.append([max(-1000, min(1000, x + rng.randint(-spread, spread))) for x in base])
    return result


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case([[0, 0], [1, 0], [9, 9], [10, 10]], [[0, 1], [10, 9]]),
        case([[1], [2], [3], [10]], [[0], [5], [100]]),
        case([[5, 5]], [[0, 0]]),
        case([[0, 0], [2, 0]], [[1, 1], [1, -1]]),
        case([[1, 1], [1, 1], [1, 1]], [[1, 1], [1, 1]]),
        case([[0], [4], [2]], [[1], [3]]),
        case([[0], [10]], [[5], [5], [-100]]),
        case([[-1000, 1000, -1000, 1000]], [[1000, -1000, 1000, -1000], [0, 0, 0, 0]]),
    ]
    cases = grouped("manual", cases)
    for _ in range(5):
        d, k = rng.randint(1, 4), rng.randint(1, 6)
        points = cloud(rng, rng.randint(1, 30), d, k, 50)
        cases.append(dict(case(points, cloud(rng, k, d, k, 300)), group="random"))
    d = rng.randint(2, 3)
    points = cloud(rng, params["points_max"], d, 6, 120)
    cases.append(dict(case(points, cloud(rng, 8, d, 8, 400)), group="stress"))
    return cases
```

## Рубрика

- vectorized-distances: Расстояния от всех точек до всех центров считаются одной операцией через broadcasting или `cdist`, без цикла Python по точкам
  + `((points[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)`
  - `for p in points: for c in centroids: ...`
- ties: Ничьи по расстоянию разрешаются в пользу меньшего индекса центра, и это осознанно: `argmin` или строгое сравнение
- empty-cluster: Пустой кластер сохраняет старый центр без деления на ноль и без `nan`
  + `updated[filled] = sums[filled] / counts[filled, None]`
  - `points[labels == k].mean(axis=0)` для пустой маски
- inertia: Инерция считается по старым центрам и по квадратам расстояний, без извлечения корня
  - `np.sqrt(squared).sum()`
- no-sklearn: Итерация реализована самостоятельно, без `sklearn.cluster.KMeans`
- memory-aware: Учтено, что матрица расстояний занимает n · k чисел, и при необходимости приведена оценка памяти или обработка блоками
