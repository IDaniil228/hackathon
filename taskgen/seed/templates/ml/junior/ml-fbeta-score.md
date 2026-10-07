---
name: ml-fbeta-score
description: >-
  Реализация F-beta меры для бинарной классификации на NumPy: матрица ошибок, вес полноты
  против точности, деление на ноль при отсутствии положительных примеров
area: ml
grade: junior
skills: [python, numpy, ml-metrics]
kind: code
runtime: python-ml
difficulty: 3
expected_minutes: 20
params:
  n_max: {type: int, min: 3000, max: 5000}
  domain:
    type: choice
    choices:
      - детектор мошеннических транзакций
      - фильтр спама в почте
      - модель, которая находит брак на конвейере по фото
      - классификатор заявок, которые нужно передать старшему специалисту
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 1, required: true}
---

# F-beta мера

## Генерация

Придумай легенду: {domain}. Команда сравнивает модели по F-beta мере и хочет свою реализацию без scikit-learn. В условии обязательно скажи:

- функция `fbeta_score(y_true, y_pred, beta)` получает два списка одинаковой длины из 0 и 1 — истинные метки и предсказания, где 1 — положительный класс, — и положительное вещественное `beta`;
- вернуть F-beta меру положительного класса как число с плавающей точкой: `(1 + beta²) · P · R / (beta² · P + R)`, где P — точность, R — полнота;
- если истинно положительных предсказаний нет, вернуть 0.0, в том числе когда списки пусты или положительных нет вовсе;
- поясни в легенде, что значит beta больше и меньше единицы для этой задачи;
- длина списков до {n_max}; можно использовать NumPy, но не готовые метрики из scikit-learn.

## Эталон

```python
import numpy as np


def fbeta_score(y_true, y_pred, beta):
    truth = np.asarray(y_true, dtype=bool)
    predicted = np.asarray(y_pred, dtype=bool)
    tp = np.count_nonzero(truth & predicted)
    fp = np.count_nonzero(~truth & predicted)
    fn = np.count_nonzero(truth & ~predicted)
    if tp == 0:
        return 0.0
    # Форма через матрицу ошибок равна (1 + b²)PR / (b²P + R) и не делит на ноль при tp > 0.
    b2 = beta * beta
    return float((1 + b2) * tp / ((1 + b2) * tp + b2 * fn + fp))
```

## Сигнатура

```
fbeta_score
```

## Тесты

```python
def reference_free(y_true, y_pred, beta):
    tp = fp = fn = 0
    for t, p in zip(y_true, y_pred):
        if t and p:
            tp += 1
        elif p:
            fp += 1
        elif t:
            fn += 1
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    b2 = beta * beta
    return (1 + b2) * precision * recall / (b2 * precision + recall)


def case(y_true, y_pred, beta):
    return {"input": [y_true, y_pred, beta], "expected": reference_free(y_true, y_pred, beta)}


def noisy(rng, n, positive_share, error_share):
    truth = [1 if rng.random() < positive_share else 0 for _ in range(n)]
    predicted = [1 - t if rng.random() < error_share else t for t in truth]
    return truth, predicted


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case([1, 0, 1, 1, 0, 0], [1, 1, 0, 1, 0, 0], 1.0),
        case([1, 0, 1, 1, 0, 0], [1, 1, 1, 1, 0, 0], 2.0),
        case([], [], 1.0),
        case([0, 0, 0], [0, 0, 0], 1.0),
        case([1, 1, 0], [0, 0, 1], 0.5),
        case([1, 1, 1], [1, 1, 1], 3.0),
        case([0, 0, 1], [1, 1, 1], 0.5),
        case([1, 1, 1, 1], [1, 0, 0, 0], 0.25),
    ]
    cases = grouped("manual", cases)
    for beta in (0.5, 1.0, 2.0):
        truth, predicted = noisy(rng, rng.randint(10, 60), rng.uniform(0.1, 0.6), 0.25)
        cases.append(dict(case(truth, predicted, beta), group="random"))
    truth, predicted = noisy(rng, params["n_max"], 0.05, 0.03)
    cases.append(dict(case(truth, predicted, rng.choice([0.5, 2.0])), group="stress"))
    truth, predicted = noisy(rng, 500, 0.5, 0.4)
    cases.append(dict(case(truth, predicted, round(rng.uniform(0.1, 4.0), 2)), group="random"))
    return cases
```

## Рубрика

- confusion: TP, FP и FN считаются верно и векторно: логические маски или сравнения массивов, без цикла Python по элементам
  + `np.count_nonzero(truth & predicted)`
  - `for i in range(len(y_true)): if y_true[i] == 1 and y_pred[i] == 1: ...`
- zero-division: Случай без истинно положительных обработан явно и возвращает 0.0 без предупреждений NumPy о делении на ноль и без `nan`
  + `if tp == 0: return 0.0`
  - `precision = tp / (tp + fp)` до проверки знаменателя
- return-type: Возвращается число с плавающей точкой Python или скаляр NumPy, а не массив и не целое число
  - `return (1 + b2) * tp // denominator`
- no-sklearn: Метрика реализована самостоятельно, без `sklearn.metrics.fbeta_score` и других готовых метрик
