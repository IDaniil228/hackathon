---
name: backend-validate-signup
description: >-
  Валидация тела запроса регистрации: обязательные поля, типы JSON с ловушкой bool как int,
  правила для email, пароля, возраста и логина, сбор всех ошибок в стабильном порядке
area: backend
grade: junior
skills: [python, http-api]
kind: code
runtime: python
io: function
difficulty: 3
expected_minutes: 25
params:
  min_password: {type: choice, choices: [8, 10, 12]}
  domain:
    type: choice
    choices:
      - регистрация в сервисе онлайн-курсов
      - регистрация в приложении для бронирования спортзалов
      - создание аккаунта в игровом сервисе
      - регистрация покупателя в интернет-магазине
case_groups:
  manual: {weight: 2, required: true}
  random: {weight: 1}
---

# Валидация запроса регистрации

## Генерация

Придумай легенду: {domain}. Бэкенд получает тело POST-запроса, уже разобранное из JSON в словарь, и должен вернуть все ошибки сразу, чтобы фронтенд подсветил поля. В условии обязательно скажи:

- функция `validate_signup(payload, min_password)` получает словарь `payload` и целое `min_password` (в этой задаче — {min_password}) и возвращает список кодов ошибок, отсортированный по возрастанию; пустой список значит «запрос корректен»;
- допустимые поля — `email`, `password`, `age`, `username`, все обязательны; если есть хотя бы одно другое поле, в ответ один раз добавляется `payload.unknown_fields`;
- для каждого поля код ошибки — `<поле>.missing`, если поля нет; `<поле>.type`, если значение не того типа; `<поле>.invalid`, если тип верный, но значение не проходит правило; у поля не больше одной ошибки;
- `email` — строка без пробелов с ровно одним `@`, непустой частью до `@` и доменом, который содержит точку, но не начинается и не заканчивается ею;
- `password` — строка длиной не меньше `min_password`, в которой есть хотя бы одна латинская буква и хотя бы одна цифра 0–9;
- `age` — целое число от 14 до 120 включительно; `true`, `25.0` и `"25"` — ошибка типа;
- `username` — строка из 3–20 символов: строчные латинские буквы, цифры и `_`, первая — буква.

## Решения

### reference +

```python
import re

USERNAME = re.compile(r"[a-z][a-z0-9_]{2,19}")
LETTER = re.compile(r"[A-Za-z]")
DIGIT = re.compile(r"[0-9]")


def _is_int(value):
    # bool — подкласс int, но в JSON true не число.
    return isinstance(value, int) and not isinstance(value, bool)


def _email_ok(value):
    if " " in value or value.count("@") != 1:
        return False
    local, domain = value.split("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def validate_signup(payload, min_password):
    rules = {
        "email": (lambda v: isinstance(v, str), _email_ok),
        "password": (
            lambda v: isinstance(v, str),
            lambda v: len(v) >= min_password and LETTER.search(v) and DIGIT.search(v),
        ),
        "age": (_is_int, lambda v: 14 <= v <= 120),
        "username": (lambda v: isinstance(v, str), USERNAME.fullmatch),
    }
    errors = []
    if payload.keys() - rules.keys():
        errors.append("payload.unknown_fields")
    for field, (type_ok, value_ok) in rules.items():
        if field not in payload:
            errors.append(f"{field}.missing")
        elif not type_ok(payload[field]):
            errors.append(f"{field}.type")
        elif not value_ok(payload[field]):
            errors.append(f"{field}.invalid")
    return sorted(errors)
```

### bool-as-int -

`bool` принят за `int`

```python
import re

USERNAME = re.compile(r"[a-z][a-z0-9_]{2,19}")
LETTER = re.compile(r"[A-Za-z]")
DIGIT = re.compile(r"[0-9]")


def _is_int(value):
    # bool — подкласс int, но в JSON true не число.
    return isinstance(value, int)


def _email_ok(value):
    if " " in value or value.count("@") != 1:
        return False
    local, domain = value.split("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def validate_signup(payload, min_password):
    rules = {
        "email": (lambda v: isinstance(v, str), _email_ok),
        "password": (
            lambda v: isinstance(v, str),
            lambda v: len(v) >= min_password and LETTER.search(v) and DIGIT.search(v),
        ),
        "age": (_is_int, lambda v: 14 <= v <= 120),
        "username": (lambda v: isinstance(v, str), USERNAME.fullmatch),
    }
    errors = []
    if payload.keys() - rules.keys():
        errors.append("payload.unknown_fields")
    for field, (type_ok, value_ok) in rules.items():
        if field not in payload:
            errors.append(f"{field}.missing")
        elif not type_ok(payload[field]):
            errors.append(f"{field}.type")
        elif not value_ok(payload[field]):
            errors.append(f"{field}.invalid")
    return sorted(errors)
```

### any-letter -

Буква не только латинская

```python
import re

USERNAME = re.compile(r"[a-z][a-z0-9_]{2,19}")
LETTER = re.compile(r"[^\W\d_]")
DIGIT = re.compile(r"[0-9]")


def _is_int(value):
    # bool — подкласс int, но в JSON true не число.
    return isinstance(value, int) and not isinstance(value, bool)


def _email_ok(value):
    if " " in value or value.count("@") != 1:
        return False
    local, domain = value.split("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def validate_signup(payload, min_password):
    rules = {
        "email": (lambda v: isinstance(v, str), _email_ok),
        "password": (
            lambda v: isinstance(v, str),
            lambda v: len(v) >= min_password and LETTER.search(v) and DIGIT.search(v),
        ),
        "age": (_is_int, lambda v: 14 <= v <= 120),
        "username": (lambda v: isinstance(v, str), USERNAME.fullmatch),
    }
    errors = []
    if payload.keys() - rules.keys():
        errors.append("payload.unknown_fields")
    for field, (type_ok, value_ok) in rules.items():
        if field not in payload:
            errors.append(f"{field}.missing")
        elif not type_ok(payload[field]):
            errors.append(f"{field}.type")
        elif not value_ok(payload[field]):
            errors.append(f"{field}.invalid")
    return sorted(errors)
```

## Сигнатура

```
validate_signup
```

## Тесты

```python
LOWER = "abcdefghijklmnopqrstuvwxyz"


def valid_payload(rng, min_password):
    name = rng.choice(LOWER) + "".join(
        rng.choice(LOWER + "0123456789_") for _ in range(rng.randint(2, 19))
    )
    password = list(rng.choice("ABCxyz") + rng.choice("0123456789"))
    password += [
        rng.choice(LOWER + "0123456789!") for _ in range(min_password - 2 + rng.randint(0, 4))
    ]
    rng.shuffle(password)
    return {
        "email": f"{rng.choice(['ivan', 'a.b', 'x_1'])}@{rng.choice(['mail.ru', 'ex.co.uk', 'b.io'])}",
        "password": "".join(password),
        "age": rng.choice([14, 120, rng.randint(15, 119)]),
        "username": name,
    }


def mutations(min_password):
    short = "a1" + "x" * (min_password - 3)
    return {
        "email": {
            "invalid": [
                "user.example.com",
                "a@b@c.com",
                "@example.com",
                "user@example",
                "user@.example.com",
                "user@example.com.",
                "us er@example.com",
            ],
            "type": [42, None, ["a@b.co"]],
        },
        "password": {
            "invalid": [
                short,
                "abcdefgh" * 2,
                "1" * (min_password + 2),
                "пароль" + "1" * min_password,
            ],
            "type": [12345678, None, True],
        },
        "age": {"invalid": [13, 121, 0, -5], "type": ["25", 25.0, True, None]},
        "username": {
            "invalid": ["ab", "1user", "User", "a" * 21, "user-name", ""],
            "type": [123, None],
        },
    }


def mutate(rng, payload, fields, unknown, min_password):
    table = mutations(min_password)
    payload = dict(payload)
    codes = set()
    for field in fields:
        kind = rng.choice(["missing", "type", "invalid"])
        if kind == "missing":
            del payload[field]
        else:
            payload[field] = rng.choice(table[field][kind])
        codes.add(f"{field}.{kind}")
    if unknown:
        payload[rng.choice(["role", "is_admin", "Email"])] = rng.choice(["admin", True])
        codes.add("payload.unknown_fields")
    return {"input": [payload, min_password], "expected": sorted(codes)}


def grouped(group, cases):
    return [dict(item, group=group) for item in cases]


def generate(params, rng):
    m = params["min_password"]
    base = {
        "email": "anna@school.ru",
        "password": "Secret" + "7" * (m - 6),
        "age": 30,
        "username": "anna_k",
    }
    cases = [
        {"input": [base, m], "expected": []},
        {
            "input": [{"email": "anna@@school", "password": "short1", "age": True}, m],
            "expected": ["age.type", "email.invalid", "password.invalid", "username.missing"],
        },
        {
            "input": [{}, m],
            "expected": ["age.missing", "email.missing", "password.missing", "username.missing"],
        },
        {"input": [dict(base, age=14, username="abc"), m], "expected": []},
        {"input": [dict(base, age=120, username="a" * 20), m], "expected": []},
        {"input": [dict(base, password="A" + "1" * (m - 1)), m], "expected": []},
        {"input": [dict(base, password="A" + "1" * (m - 2)), m], "expected": ["password.invalid"]},
        {"input": [dict(base, extra=1, other=2), m], "expected": ["payload.unknown_fields"]},
    ]
    names = ["email", "password", "age", "username"]
    for field in names:
        for kind, values in mutations(m)[field].items():
            for value in values:
                cases.append(
                    {"input": [dict(base, **{field: value}), m], "expected": [f"{field}.{kind}"]}
                )
    cases = grouped("manual", cases)
    for _ in range(12):
        fields = rng.sample(names, rng.randint(0, 4))
        case = mutate(rng, valid_payload(rng, m), fields, rng.random() < 0.3, m)
        cases.append(dict(case, group="random"))
    return cases
```

## Рубрика

- bool-trap: Проверка типа `age` отсекает `bool`, который в Python — подкласс `int`
  + `isinstance(v, int) and not isinstance(v, bool)`
  - `isinstance(v, int)` без исключения `bool`
- one-error-per-field: Для каждого поля проверки идут по порядку «нет поля → тип → правило», и поле даёт не больше одной ошибки
  - `password.type` и `password.invalid` одновременно для числа
- collect-all: Решение собирает все ошибки, а не возвращается после первой
- table-driven: Правила полей описаны данными или отдельными функциями, без четырёх скопированных блоков if с разными именами
  + словарь `field -> (проверка типа, проверка значения)`
- ascii-rules: Правила пароля и логина опираются на латиницу и цифры 0–9 явно, а не на `str.isalpha` и `str.isdigit`, которые пропускают кириллицу и другие алфавиты
  - `any(c.isalpha() for c in v)` для требования латинской буквы
- unknown-fields: Лишние поля обнаруживаются разностью множеств ключей и дают ровно одну ошибку
  + `payload.keys() - rules.keys()`
