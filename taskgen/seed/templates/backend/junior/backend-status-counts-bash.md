---
name: backend-status-counts-bash
description: >-
  Скрипт на Bash, который считает запросы по статусам ответа в журнале доступа из stdin:
  пропуск битых строк, порог числа запросов, числовая сортировка по убыванию и ничьи
area: backend
grade: junior
skills: [bash, text-processing, observability]
kind: code
runtime: bash
io: stdio
difficulty: 3
expected_minutes: 20
params:
  domain:
    type: choice
    choices:
      - журнал nginx интернет-магазина
      - журнал API службы доставки
      - журнал шлюза мобильного банка
      - журнал сервиса онлайн-записи к врачу
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
---

# Статусы ответов в журнале доступа

## Генерация

Придумай легенду: {domain}. Дежурный инженер хочет быстро увидеть, какие статусы ответа встречаются в журнале чаще всего. В условии обязательно скажи:

- кандидат пишет скрипт на Bash; скрипт читает всё из stdin и печатает ответ в stdout;
- первая строка входа — целое неотрицательное число N, порог числа запросов; дальше строки журнала, их может не быть;
- корректная строка журнала — ровно пять полей через пробелы: время, метод, путь, статус и длительность, например `2026-10-07T10:00:01Z GET /api/orders 200 15ms`; статус корректной строки — три цифры, первая от 1 до 5; остальные поля не проверяются;
- некорректные строки пропускаются молча;
- для каждого статуса, который встретился в корректных строках не меньше N раз, напечатать строку `статус число`;
- строки упорядочены по числу запросов по убыванию, при равенстве — по статусу по возрастанию; если печатать нечего, вывод пуст.

## Решения

### reference +

```bash
read -r threshold
awk -v min="$threshold" '
  NF == 5 && $4 ~ /^[1-5][0-9][0-9]$/ { count[$4]++ }
  END { for (status in count) if (count[status] >= min) print status, count[status] }
' | sort -k2,2nr -k1,1n
```

### no-validation -

Считает статус в любой строке, не проверяя число полей и формат статуса

```bash
read -r threshold
awk -v min="$threshold" '
  { count[$4]++ }
  END { for (status in count) if (count[status] >= min) print status, count[status] }
' | sort -k2,2nr -k1,1n
```

### lexical-sort -

Сортирует число запросов как строку: 9 оказывается выше 10

```bash
read -r threshold
awk -v min="$threshold" '
  NF == 5 && $4 ~ /^[1-5][0-9][0-9]$/ { count[$4]++ }
  END { for (status in count) if (count[status] >= min) print status, count[status] }
' | sort -k2,2r -k1,1n
```

### strict-threshold -

Строгое сравнение с порогом: статус, который встретился ровно N раз, пропадает

```bash
read -r threshold
awk -v min="$threshold" '
  NF == 5 && $4 ~ /^[1-5][0-9][0-9]$/ { count[$4]++ }
  END { for (status in count) if (count[status] > min) print status, count[status] }
' | sort -k2,2nr -k1,1n
```

## Тесты

```python
METHODS = ["GET", "POST", "PUT", "DELETE"]
PATHS = ["/api/orders", "/api/users/42", "/health", "/api/cart?item=7", "/"]
STATUSES = ["200", "201", "204", "301", "304", "400", "401", "404", "429", "500", "502", "503"]


def line(rng, status):
    second = rng.randint(0, 59)
    return (
        f"2026-10-07T10:00:{second:02d}Z {rng.choice(METHODS)} {rng.choice(PATHS)} "
        f"{status} {rng.randint(1, 999)}ms"
    )


def valid_status(text):
    digits = "0123456789"
    return len(text) == 3 and text[0] in "12345" and text[1] in digits and text[2] in digits


def expected(threshold, lines):
    counts = {}
    for raw in lines:
        fields = raw.split()
        if len(fields) == 5 and valid_status(fields[3]):
            counts[fields[3]] = counts.get(fields[3], 0) + 1
    rows = [(status, n) for status, n in counts.items() if n >= threshold]
    rows.sort(key=lambda row: (-row[1], int(row[0])))
    return "".join(f"{status} {n}\n" for status, n in rows)


def case(threshold, lines, group):
    stdin = f"{threshold}\n" + "".join(raw + "\n" for raw in lines)
    return {"stdin": stdin, "expected": expected(threshold, lines), "group": group}


def generate(params, rng):
    def many(status, n):
        return [line(rng, status) for _ in range(n)]

    broken = [
        "garbage",
        "",
        "2026-10-07T10:00:01Z GET /api/orders 200",
        "2026-10-07T10:00:01Z GET /api/orders 200 15ms extra",
        "2026-10-07T10:00:01Z GET /api/orders 2OO 15ms",
        "2026-10-07T10:00:01Z GET /api/orders 700 15ms",
        "2026-10-07T10:00:01Z GET /api/orders 20 15ms",
        "2026-10-07T10:00:01Z GET /api/orders 2000 15ms",
    ]
    cases = [
        case(1, many("200", 3) + many("404", 1) + many("500", 2), "manual"),
        # 10 и 9 запросов: строковая сортировка ставит 9 выше 10.
        case(1, many("200", 9) + many("503", 10) + many("301", 2), "manual"),
        # Статус ровно с порогом остаётся в отчёте.
        case(3, many("200", 3) + many("404", 2) + many("500", 4), "manual"),
        case(2, many("201", 2) + broken + many("201", 1) + many("429", 2), "manual"),
        case(1, broken + many("204", 1), "manual"),
        # Ничья по числу запросов решается статусом по возрастанию.
        case(1, many("502", 2) + many("200", 2) + many("404", 2), "manual"),
        case(5, many("200", 4) + many("500", 1), "manual"),
        case(0, [], "manual"),
        case(1, many("304", 12), "manual"),
    ]
    for _ in range(8):
        lines = []
        for _ in range(rng.randint(0, 300)):
            if rng.random() < 0.1:
                lines.append(rng.choice(broken))
            else:
                lines.append(line(rng, rng.choice(STATUSES)))
        cases.append(case(rng.randint(0, 25), lines, "random"))
    return cases
```

## Рубрика

- validation: Корректность строки проверяется до подсчёта: ровно пять полей и статус из трёх цифр с первой от 1 до 5
  + `NF == 5 && $4 ~ /^[1-5][0-9][0-9]$/`
  - подсчёт `$4` в каждой строке без проверки
- threshold: Порог из первой строки читается отдельно от журнала и сравнивается нестрого
  + `read -r threshold`, затем `count >= min`
- numeric-sort: Сортировка числовая по убыванию числа запросов с ничьёй по статусу
  + `sort -k2,2nr -k1,1n`
  - `sort -r` без числового ключа
- single-pass: Журнал читается один раз одной утилитой, без цикла `while read` с вызовом внешних команд на каждую строку
- quoting: Переменные оболочки в кавычках, значение порога передаётся в `awk` через `-v`, а не подстановкой в текст программы
