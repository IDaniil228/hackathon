---
name: algo-grid-weighted-path
description: >-
  Кратчайший путь по клетчатому полю с двумя ценами клеток и стенами: алгоритм Дейкстры
  или его вариант для малых целых весов, недостижимая цель
area: algo
grade: middle
skills: [python, graphs]
kind: code
runtime: python
difficulty: 5
expected_minutes: 30
params:
  side_max: {type: int, min: 120, max: 160}
  domain:
    type: choice
    choices:
      - робот-пылесос в квартире, где ковры замедляют движение
      - курьер в парке, где газоны проходить дольше, чем дорожки
      - персонаж игры на карте с болотами
      - марсоход на равнине с участками рыхлого грунта
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
  stress: {weight: 3, required: true}
---

# Путь по полю с медленными клетками

## Генерация

Придумай легенду: {domain}. В условии обязательно скажи:

- функция `shortest_path(grid, start, finish, slow_cost)` получает поле `grid` — список строк одинаковой длины из символов `.` (обычная клетка), `~` (медленная клетка) и `#` (стена), а также координаты `start` и `finish` в виде `[строка, столбец]` и целое `slow_cost` от 2 до 9;
- ходить можно в соседние по стороне клетки, не выходя за поле и не заходя в стены;
- вход в обычную клетку стоит 1, в медленную — `slow_cost`; стартовая клетка не оплачивается;
- вернуть минимальную стоимость пути до `finish` или -1, если дойти нельзя; если `start` совпадает с `finish`, ответ 0;
- `start` и `finish` никогда не стоят на стене; сторона поля — до {side_max} клеток.

## Эталон

```python
import heapq


def shortest_path(grid, start, finish, slow_cost):
    rows, cols = len(grid), len(grid[0])
    target = (finish[0], finish[1])
    dist = {(start[0], start[1]): 0}
    heap = [(0, start[0], start[1])]
    while heap:
        cost, r, c = heapq.heappop(heap)
        if (r, c) == target:
            return cost
        if cost > dist[(r, c)]:
            continue  # Устаревшая запись: клетку уже достали дешевле.
        for nr, nc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] != "#":
                step = slow_cost if grid[nr][nc] == "~" else 1
                new_cost = cost + step
                if new_cost < dist.get((nr, nc), new_cost + 1):
                    dist[(nr, nc)] = new_cost
                    heapq.heappush(heap, (new_cost, nr, nc))
    return -1
```

## Сигнатура

```
shortest_path
```

## Тесты

```python
def neighbours(grid, r, c):
    for nr, nc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
        if 0 <= nr < len(grid) and 0 <= nc < len(grid[0]) and grid[nr][nc] != "#":
            yield nr, nc


def price(grid, r, c, slow_cost):
    return slow_cost if grid[r][c] == "~" else 1


def relaxation(grid, start, finish, slow_cost):
    # Беллман — Форд: повторять ослабление рёбер, пока расстояния меняются.
    inf = float("inf")
    dist = {(r, c): inf for r in range(len(grid)) for c in range(len(grid[0]))}
    dist[tuple(start)] = 0
    changed = True
    while changed:
        changed = False
        for (r, c), d in list(dist.items()):
            if d == inf or grid[r][c] == "#":
                continue
            for nr, nc in neighbours(grid, r, c):
                if d + price(grid, nr, nc, slow_cost) < dist[(nr, nc)]:
                    dist[(nr, nc)] = d + price(grid, nr, nc, slow_cost)
                    changed = True
    answer = dist[tuple(finish)]
    return -1 if answer == inf else answer


def buckets(grid, start, finish, slow_cost):
    # Алгоритм Дайала: очередь из корзин по стоимости, веса малы и целы.
    best = {tuple(start): 0}
    queue = {0: [tuple(start)]}
    cost = 0
    limit = len(grid) * len(grid[0]) * slow_cost
    while cost <= limit:
        for cell in queue.pop(cost, []):
            if best[cell] != cost:
                continue
            if cell == tuple(finish):
                return cost
            for nxt in neighbours(grid, *cell):
                new_cost = cost + price(grid, *nxt, slow_cost)
                if new_cost < best.get(nxt, new_cost + 1):
                    best[nxt] = new_cost
                    queue.setdefault(new_cost, []).append(nxt)
        cost += 1
    return -1


def random_grid(rng, rows, cols, walls, slow):
    grid = []
    for _ in range(rows):
        row = []
        for _ in range(cols):
            x = rng.random()
            row.append("#" if x < walls else "~" if x < walls + slow else ".")
        grid.append(row)
    return grid


def case(grid, start, finish, slow_cost):
    grid = [list(row) for row in grid]
    for r, c in (start, finish):
        if grid[r][c] == "#":
            grid[r][c] = "."
    grid = ["".join(row) for row in grid]
    small = len(grid) * len(grid[0]) <= 150
    solve = relaxation if small else buckets
    expected = solve(grid, start, finish, slow_cost)
    return {"input": [grid, list(start), list(finish), slow_cost], "expected": expected}


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    cases = [
        case(["..~", ".#~", "..."], [0, 0], [2, 2], 3),
        case([".~~.", ".##.", "...."], [0, 0], [0, 3], 2),
        case(["."], [0, 0], [0, 0], 5),
        case([".#."], [0, 0], [0, 2], 2),
        case(["~~~", "~~~"], [0, 0], [1, 2], 9),
        case([".~.", ".#.", "..."], [0, 0], [0, 2], 5),
        case(["....", "####", "...."], [2, 3], [0, 0], 4),
    ]
    cases = grouped("manual", cases)
    for _ in range(6):
        rows, cols = rng.randint(1, 10), rng.randint(1, 12)
        while rows * cols > 150:
            cols -= 1
        grid = random_grid(rng, rows, cols, 0.25, 0.3)
        start = [rng.randrange(rows), rng.randrange(cols)]
        finish = [rng.randrange(rows), rng.randrange(cols)]
        cases.append(dict(case(grid, start, finish, rng.randint(2, 9)), group="random"))
    side = params["side_max"]
    # Большое поле проверяет скорость, поэтому цель на нём должна быть достижима.
    for _ in range(10):
        grid = random_grid(rng, side, side, 0.2, 0.35)
        big = case(grid, [0, 0], [side - 1, side - 1], rng.randint(2, 9))
        if big["expected"] != -1:
            break
    cases.append(dict(big, group="stress"))
    return cases
```

## Рубрика

- algorithm: Выбран алгоритм для неотрицательных весов: Дейкстра с кучей, очередь Дайала по корзинам или иной с обоснованием; обычный BFS без учёта весов не годится
  + `heapq.heappush(heap, (new_cost, nr, nc))`
  - обход в ширину через `deque`, который считает число шагов, а не стоимость
- stale-entries: Устаревшие записи кучи пропускаются или не создаются: клетка обрабатывается один раз с окончательной стоимостью
  + `if cost > dist[(r, c)]: continue`
  - повторная обработка соседей для каждой извлечённой копии клетки
- early-exit: Поиск останавливается, когда из очереди извлечена цель, и корректно возвращает -1, если очередь опустела
  + `if (r, c) == target: return cost`
- cost-model: Стоимость берётся за вход в клетку, а стартовая клетка не оплачивается, в том числе если старт медленный
  - к ответу прибавлена цена стартовой клетки
- complexity: Оценка O(V log V) для поля из V клеток или O(V · slow_cost) для корзин; нет перебора путей и пересчёта всего поля на каждом шаге
- structure: Соседи, проверка границ и цена клетки выражены явно и без дублирования четырёх почти одинаковых веток
  + цикл по списку смещений `((1, 0), (-1, 0), (0, 1), (0, -1))`
  - четыре скопированных блока `if r + 1 < rows ...`
