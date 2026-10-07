---
name: algo-cheapest-route-cpp
description: >-
  Самый дешёвый маршрут с ограничением на число пересадок на C++: Беллман — Форд по слоям
  или Дейкстра по состояниям, вторичный критерий — наименьшее число рейсов
area: algo
grade: senior
skills: [cpp, graphs, dynamic-programming]
kind: code
runtime: cpp
io: function
difficulty: 7
expected_minutes: 40
params:
  n_max: {type: int, min: 100, max: 150}
  m_max: {type: int, min: 1500, max: 2200}
  domain:
    type: choice
    choices:
      - перелёты между городами с ограничением на число пересадок
      - пересылка пакета между узлами сети с ограничением на число прыжков
      - доставка груза между складами с ограничением на число перегрузок
      - цепочка обменов между валютами с ограничением на число сделок
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 3, required: true}
---

# Дешёвый маршрут с ограничением пересадок

## Генерация

Придумай легенду: {domain}. В условии обязательно скажи:

- функция на C++ имеет вид `std::vector<long long> cheapest_route(int n, std::vector<std::vector<int>> edges, int src, int dst, int max_stops)`; вершины пронумерованы от 0 до n − 1, каждое ребро — тройка `{from, to, price}`, рёбра направленные, цена — целое от 0 до 10^4;
- маршрут — последовательность рёбер из `src` в `dst`, в которой не больше `max_stops` промежуточных остановок, то есть не больше `max_stops + 1` рёбер; вершины в маршруте могут повторяться;
- вернуть пару `{стоимость, число рёбер}`: наименьшую суммарную цену, а среди маршрутов с этой ценой — наименьшее число рёбер; если маршрута нет, вернуть `{-1, -1}`; если `src == dst`, ответ `{0, 0}`;
- между двумя вершинами может быть несколько рёбер, бывают петли; `max_stops` от 0 до n;
- вершин до {n_max}, рёбер до {m_max}; функцию `main` писать не нужно, стандартные заголовки уже подключены.

## Решения

### reference +

```cpp
std::vector<long long> cheapest_route(int n, std::vector<std::vector<int>> edges, int src,
                                      int dst, int max_stops) {
    const long long INF = std::numeric_limits<long long>::max() / 4;
    std::vector<long long> best(n, INF);
    best[src] = 0;
    long long answer = best[dst];
    int answer_edges = src == dst ? 0 : -1;
    for (int round = 1; round <= max_stops + 1; round++) {
        // Ослабление только от расстояний прошлого слоя: маршрут удлиняется ровно на ребро.
        std::vector<long long> next = best;
        for (const auto& e : edges) {
            if (best[e[0]] != INF) next[e[1]] = std::min(next[e[1]], best[e[0]] + e[2]);
        }
        best = std::move(next);
        if (best[dst] < answer) {
            answer = best[dst];
            answer_edges = round;
        }
    }
    if (answer == INF) return {-1, -1};
    return {answer, answer_edges};
}
```

### in-place-relax -

Ослабление на месте нарушает лимит рёбер

```cpp
std::vector<long long> cheapest_route(int n, std::vector<std::vector<int>> edges, int src,
                                      int dst, int max_stops) {
    const long long INF = std::numeric_limits<long long>::max() / 4;
    std::vector<long long> best(n, INF);
    best[src] = 0;
    long long answer = best[dst];
    int answer_edges = src == dst ? 0 : -1;
    for (int round = 1; round <= max_stops + 1; round++) {
        // Ослабление только от расстояний прошлого слоя: маршрут удлиняется ровно на ребро.
        std::vector<long long> next = best;
        for (const auto& e : edges) {
            if (best[e[0]] != INF) best[e[1]] = std::min(best[e[1]], best[e[0]] + e[2]);
        }
        best = std::move(next);
        if (best[dst] < answer) {
            answer = best[dst];
            answer_edges = round;
        }
    }
    if (answer == INF) return {-1, -1};
    return {answer, answer_edges};
}
```

### edges-not-minimal -

Число рёбер не минимально

```cpp
std::vector<long long> cheapest_route(int n, std::vector<std::vector<int>> edges, int src,
                                      int dst, int max_stops) {
    const long long INF = std::numeric_limits<long long>::max() / 4;
    std::vector<long long> best(n, INF);
    best[src] = 0;
    long long answer = best[dst];
    int answer_edges = src == dst ? 0 : -1;
    for (int round = 1; round <= max_stops + 1; round++) {
        // Ослабление только от расстояний прошлого слоя: маршрут удлиняется ровно на ребро.
        std::vector<long long> next = best;
        for (const auto& e : edges) {
            if (best[e[0]] != INF) next[e[1]] = std::min(next[e[1]], best[e[0]] + e[2]);
        }
        best = std::move(next);
        if (best[dst] < answer) {
            answer = best[dst];
            answer_edges = max_stops + 1;
        }
    }
    if (answer == INF) return {-1, -1};
    return {answer, answer_edges};
}
```

## Сигнатура

```
cheapest_route
```

## Тесты

```python
import heapq


def walks(n, edges, src, dst, max_edges):
    # Перебор всех маршрутов не длиннее max_edges рёбер; только для крошечных графов.
    best = None
    stack = [(src, 0, 0)]
    while stack:
        v, cost, used = stack.pop()
        if v == dst and (best is None or (cost, used) < best):
            best = (cost, used)
        if used == max_edges:
            continue
        for a, b, price in edges:
            if a == v:
                stack.append((b, cost + price, used + 1))
    return [-1, -1] if best is None else list(best)


def states(n, edges, src, dst, max_edges):
    # Дейкстра по состояниям (вершина, число рёбер) с ключом (цена, рёбра).
    out = [[] for _ in range(n)]
    for a, b, price in edges:
        out[a].append((b, price))
    seen = set()
    heap = [(0, 0, src)]
    while heap:
        cost, used, v = heapq.heappop(heap)
        if v == dst:
            return [cost, used]
        if (v, used) in seen:
            continue
        seen.add((v, used))
        if used == max_edges:
            continue
        for b, price in out[v]:
            if (b, used + 1) not in seen:
                heapq.heappush(heap, (cost + price, used + 1, b))
    return [-1, -1]


def case(n, edges, src, dst, max_stops):
    small = n <= 5 and len(edges) <= 8 and max_stops <= 4
    solve = walks if small else states
    expected = solve(n, edges, src, dst, max_stops + 1)
    return {"input": [n, [list(e) for e in edges], src, dst, max_stops], "expected": expected}


def random_graph(rng, n, m, top):
    return [(rng.randrange(n), rng.randrange(n), rng.randint(0, top)) for _ in range(m)]


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    manual = [
        case(4, [(0, 1, 100), (1, 2, 100), (2, 3, 100), (0, 2, 500), (0, 3, 900)], 0, 3, 1),
        case(3, [(0, 1, 100), (1, 2, 100), (0, 2, 200)], 0, 2, 1),
        case(3, [(0, 1, 5)], 0, 2, 3),
        case(2, [(0, 1, 7)], 1, 1, 0),
        case(3, [(0, 1, 1), (1, 2, 1)], 0, 2, 0),
        case(2, [(0, 1, 9), (0, 1, 4), (0, 0, 0)], 0, 1, 0),
        case(3, [(0, 1, 0), (1, 0, 0), (1, 2, 0), (0, 2, 0)], 0, 2, 2),
        case(4, [(0, 1, 1), (1, 2, 1), (2, 3, 1), (0, 3, 3)], 0, 3, 2),
        case(4, [(0, 1, 10000), (1, 2, 10000), (2, 3, 10000)], 0, 3, 4),
    ]
    randoms = []
    for _ in range(6):
        n = rng.randint(2, 5)
        edges = random_graph(rng, n, rng.randint(1, 8), 9)
        randoms.append(case(n, edges, rng.randrange(n), rng.randrange(n), rng.randint(0, 4)))
    for _ in range(3):
        n = rng.randint(10, 40)
        edges = random_graph(rng, n, rng.randint(4 * n, 8 * n), 50)
        randoms.append(case(n, edges, 0, n - 1, rng.randint(1, 6)))
    n = params["n_max"]
    edges = random_graph(rng, n, params["m_max"], 10000)
    stress = [case(n, edges, 0, n - 1, rng.randint(15, 30))]
    return grouped("manual", manual) + grouped("random", randoms) + grouped("stress", stress)
```

## Рубрика

- layered: Ограничение на число рёбер соблюдено точно: на каждом шаге ослабляются расстояния только из предыдущего слоя, либо состояние поиска включает число использованных рёбер
  + `std::vector<long long> next = best;` и чтение из `best` при записи в `next`
  - ослабление «на месте» в одном массиве, из-за которого за один проход маршрут удлиняется на несколько рёбер
- tie-break: Вторичный критерий учтён корректно: число рёбер берётся у первого слоя, на котором достигнута итоговая минимальная цена, или сравнивается пара (цена, рёбра)
  + `if (best[dst] < answer) { answer = best[dst]; answer_edges = round; }`
  - возвращается `max_stops + 1` или число рёбер последнего улучшения любой вершины
- why-not-plain-dijkstra: В решении или пояснении сказано, почему обычный Дейкстра по вершинам не подходит: более дорогой путь с меньшим числом рёбер может понадобиться позже, поэтому вершину нельзя закрыть один раз
- overflow: Нет переполнения: сумма цен хранится в `long long`, а «бесконечность» не участвует в сложении
  + `if (best[e[0]] != INF)` перед сложением
  - `INT_MAX + price`
- edge-cases: Случаи `src == dst`, недостижимой цели и `max_stops = 0` обрабатываются без особых веток, ломающих общую логику
- complexity: Сложность O((max_stops + 1) · (n + m)) по времени и O(n) по памяти на слой, без перебора маршрутов
