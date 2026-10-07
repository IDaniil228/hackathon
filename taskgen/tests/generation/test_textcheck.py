"""Проверки текста от LLM-писателя: дефект отклоняется, честный текст — нет.

Дефектные фрагменты взяты из настоящих выводов моделей на шаблонах банка. Честный корпус —
шаблоны банка и ситуации эссе из бенчмарка проверки: в них есть блоки кода, формулы, списки
и примеры вызовов, и ни одна проверка не должна на них сработать.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskgen.generation.textcheck import foreign_numbers, text_problems
from taskgen.markers import markers, new_nonce

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / "seed" / "templates").rglob("*.md"))
BENCH = json.loads((ROOT / "tests" / "generation" / "bench" / "cases.json").read_text("utf-8"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda path: path.stem)
def test_template_text_has_no_problems(path):
    assert text_problems(path.read_text(encoding="utf-8")) == []


def test_bench_situations_have_no_problems():
    flagged = {
        case["id"]: problems
        for case in BENCH
        if (problems := text_problems(f"{case['title']}\n\n{case['situation']}"))
    }
    assert flagged == {}


def test_unclosed_code_block_is_a_cut_off():
    # Saiga закрыла строку JSON посреди примера в блоке кода.
    [problem] = text_problems("Пример использования:\n```python\ninput = [[")
    assert "блока кода" in problem


CUT_OFF = {
    # Настоящий вывод GigaChat на algo-grid-weighted-path: модель сама закрыла строку JSON.
    "gigachat-grid": "Если start == finish, вернуть 0.\n\nПример: shortest_path([[",
    "open-code-span": "Пример вызова: `shortest_path([[1, 2], [3",
    "open-paren-after-list": "Вызов:\nf([1, 2],\n  (3, ",
}

HONEST_ENDINGS = {
    "half-open-interval": "Значения лежат в интервале [0, 1).",
    "numbered-item": "Порядок:\n1) прочитать журнал",
    "bracket-in-code-span": "Символ `[` открывает список.",
    "closed-code-block": "Пример:\n```python\nf([[1, 2],\n```",
    "multiline-call": "Пример:\nshortest_path([[1, 2],\n[3, 4]], 2)",
    "plain-sentence": "Ответ — развёрнутый текст на 10–20 предложений.",
}


@pytest.mark.parametrize("text", CUT_OFF.values(), ids=CUT_OFF.keys())
def test_text_cut_off_outside_code_block_is_rejected(text):
    [problem] = text_problems(text)
    assert "оборвался" in problem


@pytest.mark.parametrize("text", HONEST_ENDINGS.values(), ids=HONEST_ENDINGS.keys())
def test_honest_last_line_is_not_a_cut_off(text):
    assert text_problems(text) == []


# --- Служебные маркеры промпта в тексте ------------------------------------------------------

ECHOED_MARKERS = {
    # Настоящий вывод Saiga: начало ситуации эссе algo-shortest-path-choice-essay с профилем.
    "saiga-profile": "<<ПРОФИЛЬ-5519fed5cdaabc35>>><<КОНЕЦ-ПРОФИЛЯ-5519fed5cdaabc35>>>\n\n"
    "Представлена ситуация с сетью",
    "this-call": f"Условие.\n{markers('ПРОФИЛЬ', new_nonce(), end_label='КОНЕЦ-ПРОФИЛЯ')[1]}",
    "without-nonce": "Конец данных <<<КОНЕЦ ОТВЕТА>>> и текст",
    "spaced": "<<< СИТУАЦИЯ-ab12cd34 >>>",
}

HONEST_ANGLES = {
    "shift-operators": "Выражение `a << 2 >> 1` и a << 2 >> 1 сдвигают биты",
    "nested-template": "Тип vector<vector<int>> хранит матрицу",
    "stream": "Вывод: std::cout << x << std::endl; ввод: std::cin >> x",
    "guillemets": "Поле «Резюме» и <<кавычки>> в тексте",
}


@pytest.mark.parametrize("text", ECHOED_MARKERS.values(), ids=ECHOED_MARKERS.keys())
def test_echoed_service_marker_is_rejected(text):
    [problem] = text_problems(text)
    assert "маркер" in problem


@pytest.mark.parametrize("text", HONEST_ANGLES.values(), ids=HONEST_ANGLES.keys())
def test_angle_brackets_that_are_not_markers_are_not_rejected(text):
    assert text_problems(text) == []


def decoded(json_text: str) -> str:
    """Строка так, как её получает сервис из JSON-ответа модели."""
    return json.loads(f'"{json_text}"')


# --- Формулы, испорченные JSON-экранированием -------------------------------------------------

# Модель пишет в JSON-строке \frac одним обратным слэшем, и json.loads читает \f как символ
# перевода страницы. \b, \t, \n и \r так же становятся управляющими символами.
SPOILED_FORMULAS = {
    # Настоящие выводы qwen3:8b и qwen3.5 на ml-logreg-gradient-step.
    "qwen38-beta": "Функция активации $\x08eta(x) = \\frac{1}{1+e^{-x}}$.",
    "qwen38-frac": "Градиент по весам $w$: $\x0crac{\\partial L}{\\partial w} = X^T (p - y)$",
    "qwen35-frac": "по всем объектам: $\x0crac{1}{n} \\sum_{i=0}^{n-1} y_i$",
    # Настоящий вывод GigaChat: \b вне формулы съел букву b в правиле обновления.
    "gigachat-b": "w ← w − lr · ∇_w L\n\x08 <- b − lr · ∇_b L",
    # Команды, которые json.loads превращает в табуляцию, перевод строки и возврат каретки.
    "theta": decoded(r"угол $\theta$ между векторами"),
    "tau": decoded(r"температура $\tau = 0.5$"),
    "nabla": decoded(r"шаг $w - lr \nabla L$ по градиенту"),
    "rho": decoded(r"корреляция $\rho(x, y)$ признаков"),
    "neq-display": decoded(r"$$\na \neq b$$"),
}

# Честный текст: обратные слэши удвоены, переводы строк и табуляция стоят там, где им место.
HONEST_FORMULAS = {
    "doubled": decoded(r"$\\frac{1}{n} \\sum_i \\beta_i$, угол $\\theta$, $\\nabla L$"),
    "display-lines": "$$\nL = -\\frac{1}{n} \\sum_i y_i \\log p_i\n$$\n\nNext step",
    "display-letter-lines": "$$\nx = 1\n$$\n\n$$\ny_i = w^T x_i\n$$",
    "shell-in-code": "```bash\nexport PATH=$HOME/bin:$PATH\n\techo $PATH\n```\nnext $x$",
    "go-tabs": "```go\nfunc f() {\n\treturn\n}\n```",
    "inline-code-dollars": "Переменная `$HOME`\nи `$PATH` берутся из окружения",
    "prices": "Запрос стоит $5.\nPremium-тариф стоит $10\nв месяц.",
    "newline-after-math": "Вероятность $p$\nnext строка и $q$",
}


@pytest.mark.parametrize("text", SPOILED_FORMULAS.values(), ids=SPOILED_FORMULAS.keys())
def test_formula_spoiled_by_json_escape_is_rejected(text):
    [problem] = text_problems(text)
    assert "обратные слэши" in problem


@pytest.mark.parametrize("text", HONEST_FORMULAS.values(), ids=HONEST_FORMULAS.keys())
def test_honest_formulas_and_code_are_not_rejected(text):
    assert text_problems(text) == []


# --- Числа кейса задачки, которых нет в фактах ----------------------------------------------

PER_PERSON = [
    "у каждого человека в данных от пяти до тридцати строк, и строки одного человека сильно "
    "похожи друг на друга",
    "в работе модель будет применяться к людям, которых не было в обучении",
]


def test_numbers_invented_by_writer_are_foreign():
    # Настоящий вывод GigaChat на ml-validation-split-quiz: объёмы и сроки сверх фактов.
    case = (
        "У каждого клиента от пяти до тридцати транзакций. Для обучения используется часть "
        "клиентов — их не более трёх тысяч из миллиона. Всего есть около ста тысяч разных "
        "клиентов и более двадцати миллионов операций за пять лет."
    )
    foreign = foreign_numbers(case, PER_PERSON)
    assert {"трёх тысяч", "миллиона", "ста тысяч", "двадцати миллионов"} <= set(foreign)
    assert "пяти" not in foreign and "тридцати" not in foreign


HONEST_NUMBERS = {
    # Настоящий вывод Saiga: числа фактов записаны цифрами.
    "digits-for-words": ("Данные содержат от 5 до 30 визиток каждого пациента", PER_PERSON),
    "digits-with-scale": ("веса от 1 до 10 миллионов", ["вес от 1 до 10 миллионов"]),
    "grouped-digits": ("в наборе 10 000 000 записей", ["в наборе десять миллионов записей"]),
    "words-for-digits": ("сто тысяч строк", ["100 тысяч строк"]),
    "compound-words": ("двадцать пять сервисов", ["25 сервисов"]),
    # Настоящий вывод qwen3.8: неопределённое количество — не число.
    "vague-amounts": ("несколько сотен тысяч вершин, миллионы рёбер и тысячи путей", []),
    "ordinal": ("после 10-й эпохи потери растут", ["после десятой эпохи потери растут"]),
    "one-and-zero": ("одна транзакция прочитала ноль строк, а другая — одну", []),
    # Порядковое слово в кейсе — приём рассказа, а не новое условие.
    "ordinal-words": ("вторая транзакция, третий сервис и пятый шаг", []),
    "percent": ("класс встречается в 2 % строк", ["класс встречается в 2 % строк"]),
    "glued-to-letters": ("модель в fp32, ошибки 5xx, тип int64", []),
    # Настоящие выводы qwen3.5:9b на задачках банка: нумерация пунктов, счёт в рассказе,
    # кратность и класс кодов ответа, развёрнутый в диапазон.
    "list-numbers": (
        "Требования:\n1. Проверять у сервера.\n2) Не передавать тело.\n 3. Хранить минуту.",
        [],
    ),
    "narrative-two": ("выдвинула два требования: сумма между двумя позициями", []),
    "multiple-word": (
        "к концу ошибка примерно в три раза выше, чем на обучении",
        ["к концу втрое выше, чем на обучении"],
    ),
    "status-class": (
        "запросов, завершившихся с кодом ответа от 500 до 599",
        ["сколько запросов завершилось ошибкой 5xx в секунду"],
    ),
    "from-other-source": ("версия 13 и версия 12", ["версия 12", "сохранил версию 13"]),
}


@pytest.mark.parametrize("case", HONEST_NUMBERS.values(), ids=HONEST_NUMBERS.keys())
def test_numbers_present_in_sources_are_not_foreign(case):
    text, sources = case
    assert foreign_numbers(text, sources) == []
