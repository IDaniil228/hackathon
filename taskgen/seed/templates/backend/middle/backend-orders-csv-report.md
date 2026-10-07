---
name: backend-orders-csv-report
description: >-
  Программа на Python, которая строит отчёт по CSV-выгрузке заказов из файла: разбор полей в
  кавычках, пропуск битых строк, фильтр по статусу из stdin, суммы по клиентам и сортировка
area: backend
grade: middle
skills: [python, text-processing]
kind: code
runtime: python
io: stdio
# Суммы денег печатаются с двумя знаками, а сравниваются как числа: 1520.5 и 1520.50 — один ответ.
check: float
difficulty: 4
expected_minutes: 25
params:
  domain:
    type: choice
    choices:
      - выгрузка заказов интернет-магазина электроники
      - выгрузка заявок оптового склада
      - выгрузка счетов сервиса доставки цветов
      - выгрузка броней сети отелей
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
---

# Отчёт по выгрузке заказов

## Генерация

Придумай легенду: {domain}. Бухгалтерии нужен отчёт о суммах по клиентам для заказов в одном статусе. В условии обязательно скажи:

- кандидат пишет программу на Python; программа читает из stdin одну строку — статус заказа, лишние пробелы по краям не учитываются, — а выгрузку из файла `orders.csv` в текущем каталоге и печатает отчёт в stdout;
- `orders.csv` — CSV в кодировке UTF-8 с разделителем-запятой и строкой заголовка `id,customer,amount,status`; поле может стоять в двойных кавычках, и тогда внутри него бывают запятые и удвоенные кавычки, как в правилах CSV;
- строка данных корректна, если в ней ровно четыре поля, а `amount` — неотрицательное число с точкой в качестве разделителя, например `120` или `99.90`; некорректные строки пропускаются молча;
- в отчёт идут корректные строки, у которых `status` совпадает со статусом из stdin в точности, с учётом регистра;
- для каждого клиента напечатать строку `сумма клиент`: сумму его заказов с двумя знаками после точки, пробел и имя клиента как в файле;
- строки упорядочены по сумме по убыванию, при равенстве — по имени клиента по возрастанию; если печатать нечего, вывод пуст.

## Решения

### reference +

```python
import csv
import sys
from decimal import Decimal, InvalidOperation


def amount_of(text):
    if not text or not all(ch in "0123456789." for ch in text) or text.count(".") > 1:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def main():
    wanted = sys.stdin.readline().strip()
    totals = {}
    with open("orders.csv", encoding="utf-8", newline="") as source:
        rows = csv.reader(source)
        next(rows, None)
        for row in rows:
            if len(row) != 4:
                continue
            _, customer, raw_amount, status = row
            amount = amount_of(raw_amount)
            if amount is None or status != wanted:
                continue
            totals[customer] = totals.get(customer, Decimal(0)) + amount
    for customer, total in sorted(totals.items(), key=lambda item: (-item[1], item[0])):
        print(f"{total:.2f} {customer}")


main()
```

### naive-split -

Делит строку по запятым сам и ломается на имени клиента в кавычках с запятой внутри

```python
import sys
from decimal import Decimal, InvalidOperation


def amount_of(text):
    if not text or not all(ch in "0123456789." for ch in text) or text.count(".") > 1:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def main():
    wanted = sys.stdin.readline().strip()
    totals = {}
    with open("orders.csv", encoding="utf-8") as source:
        lines = source.read().splitlines()[1:]
    for line in lines:
        row = line.split(",")
        if len(row) != 4:
            continue
        _, customer, raw_amount, status = row
        amount = amount_of(raw_amount)
        if amount is None or status != wanted:
            continue
        totals[customer] = totals.get(customer, Decimal(0)) + amount
    for customer, total in sorted(totals.items(), key=lambda item: (-item[1], item[0])):
        print(f"{total:.2f} {customer}")


main()
```

### float-parse -

Принимает всё, что разбирает float: `1e3`, `-5`, `nan` и `inf` попадают в сумму

```python
import csv
import sys


def main():
    wanted = sys.stdin.readline().strip()
    totals = {}
    with open("orders.csv", encoding="utf-8", newline="") as source:
        rows = csv.reader(source)
        next(rows, None)
        for row in rows:
            if len(row) != 4:
                continue
            _, customer, raw_amount, status = row
            try:
                amount = float(raw_amount)
            except ValueError:
                continue
            if status != wanted:
                continue
            totals[customer] = totals.get(customer, 0.0) + amount
    for customer, total in sorted(totals.items(), key=lambda item: (-item[1], item[0])):
        print(f"{total:.2f} {customer}")


main()
```

### name-order -

Сортирует только по имени клиента

```python
import csv
import sys
from decimal import Decimal, InvalidOperation


def amount_of(text):
    if not text or not all(ch in "0123456789." for ch in text) or text.count(".") > 1:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def main():
    wanted = sys.stdin.readline().strip()
    totals = {}
    with open("orders.csv", encoding="utf-8", newline="") as source:
        rows = csv.reader(source)
        next(rows, None)
        for row in rows:
            if len(row) != 4:
                continue
            _, customer, raw_amount, status = row
            amount = amount_of(raw_amount)
            if amount is None or status != wanted:
                continue
            totals[customer] = totals.get(customer, Decimal(0)) + amount
    for customer, total in sorted(totals.items()):
        print(f"{total:.2f} {customer}")


main()
```

## Тесты

```python
import csv
import io
from decimal import Decimal

STATUSES = ["paid", "new", "cancelled", "Paid"]
CUSTOMERS = [
    "Иванов",
    "Петрова",
    'ООО "Ромашка, Лтд"',
    "Smith, John",
    "Сидоров",
    "ИП Ким",
]


def table(rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["id", "customer", "amount", "status"])
    writer.writerows(rows)
    return buffer.getvalue()


def money(text):
    parts = text.split(".")
    if len(parts) > 2 or not "".join(parts):
        return None
    if any(ch not in "0123456789" for part in parts for ch in part):
        return None
    return Decimal(text)


def expected(wanted, rows):
    totals = {}
    for row in rows:
        if len(row) != 4:
            continue
        _, customer, amount, status = row
        value = money(amount)
        if value is None or status != wanted:
            continue
        totals[customer] = totals.get(customer, Decimal(0)) + value
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return "".join(f"{total:.2f} {name}\n" for name, total in ordered)


def case(wanted, rows, group, padding=""):
    text = table(rows)
    parsed = list(csv.reader(io.StringIO(text)))[1:]
    return {
        "stdin": f"{padding}{wanted}{padding}\n",
        "files": {"orders.csv": text},
        "expected": expected(wanted, parsed),
        "group": group,
    }


def generate(params, rng):
    base = [
        ["1", "Иванов", "120", "paid"],
        ["2", "Петрова", "99.90", "paid"],
        ["3", "Иванов", "30.50", "paid"],
        ["4", "Сидоров", "500", "new"],
        ["5", "Петрова", "0.10", "paid"],
    ]
    quoted = [
        ["1", 'ООО "Ромашка, Лтд"', "1000", "paid"],
        ["2", "Smith, John", "250.25", "paid"],
        ["3", "Иванов", "250.25", "paid"],
        ["4", 'ООО "Ромашка, Лтд"', "0.5", "paid"],
    ]
    broken = [
        ["1", "Иванов", "abc", "paid"],
        ["2", "Петрова", "-5", "paid"],
        ["3", "Сидоров", "1e3", "paid"],
        ["4", "ИП Ким", "nan", "paid"],
        ["5", "ИП Ким", "inf", "paid"],
        ["6", "Иванов", "10", "paid", "лишнее"],
        ["7", "Петрова", "15"],
        ["8", "Сидоров", "", "paid"],
        ["9", "Сидоров", "40", "paid"],
    ]
    cases = [
        case("paid", base, "manual"),
        case("new", base, "manual"),
        case("paid", quoted, "manual"),
        case("paid", broken, "manual"),
        # Статус сравнивается с учётом регистра, пробелы вокруг строки stdin не считаются.
        case("Paid", base + [["6", "Петрова", "7", "Paid"]], "manual", padding="  "),
        # Ничья по сумме решается именем клиента.
        case("paid", [["1", "Сидоров", "10", "paid"], ["2", "Иванов", "10.00", "paid"]], "manual"),
        case("archived", base, "manual"),
        case("paid", [], "manual"),
    ]
    for _ in range(8):
        rows = []
        for number in range(rng.randint(0, 60)):
            amount = f"{rng.randint(0, 5000)}.{rng.randint(0, 99):02d}"
            if rng.random() < 0.3:
                amount = str(rng.randint(0, 5000))
            if rng.random() < 0.1:
                amount = rng.choice(["x", "-1", "1e2", ""])
            rows.append([str(number), rng.choice(CUSTOMERS), amount, rng.choice(STATUSES)])
        cases.append(case(rng.choice(STATUSES), rows, "random"))
    return cases
```

## Рубрика

- csv-module: Строки разбираются модулем `csv`, а не делением по запятой: поле в кавычках с запятой внутри остаётся одним полем
  + `csv.reader(source)`
  - `line.split(",")`
- strict-amount: Сумма принимается только как неотрицательное число с точкой, без экспоненты, знака, `nan` и `inf`
  - `float(raw_amount)` без проверки формата
- money-type: Деньги складываются без ошибок двоичной дроби: `Decimal` или целые копейки
  + `Decimal(text)` или сумма в копейках
- ordering: Порядок — по сумме по убыванию с ничьёй по имени, одним ключом сортировки
  + `sorted(..., key=lambda item: (-item[1], item[0]))`
- streaming: Файл читается построчно, без загрузки всей выгрузки в память списком строк
