"""Проверки текста от LLM-писателя, которым не нужен смысл текста.

Текст — заголовок и условие задания с кодом, ситуация эссе или кейс задачки. Каждая проверка
возвращает причину отказа для писателя, и отказ уходит в тот же цикл переписывания, что и
отказ по утечке тестов. Ложный отказ честного текста хуже пропуска: он тратит попытку и может
оставить шаблон без экземпляра. Поэтому проверка срабатывает только на признак, которого
в честном тексте нет.
"""

from __future__ import annotations

import re

from taskgen.markdown import code_rows, fences, lines, unclosed_fence
from taskgen.markers import has_marker

# Управляющие символы, кроме табуляции и переводов строк. В Markdown им не место, а \b и \f
# получаются из LaTeX-команд вроде \beta и \frac, записанных в JSON одним обратным слэшем.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MATH = re.compile(r"\$\$(.+?)\$\$|\$([^$]+?)\$", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# Внутри формулы JSON превращает \t, \r и \n в табуляцию, возврат каретки и перевод строки.
# Табуляции и возврату каретки в формуле не место. Перевод строки в формуле $$…$$ честный,
# поэтому дефектом считается только перевод строки, за которым идёт остаток команды на n.
# Короткие команды \nu, \ne и \ni не учитываются: остаток u, e или i похож на переменную.
_SPOILED_IN_MATH = re.compile(
    r"[\t\r]|\n(?:abla|eq|eg|ewline|otin|ot|exists|leq|geq|mid|subseteq)(?![A-Za-z])"
)

# Заголовок, которым запрос к писателю отделяет инструкцию автора шаблона. Кандидату он не
# нужен: заголовок в тексте значит, что модель пересказала инструкцию вместо условия.
AUTHOR_INSTRUCTIONS = "Инструкция автора задания"

SPOILED_ESCAPES = (
    "в тексте испорчены LaTeX-команды: в JSON обратный слэш перед буквой стал управляющим "
    "символом, например \\frac стал переводом страницы и «rac». Удвой обратные слэши в "
    "формулах: в JSON-строке пиши \\\\frac, \\\\beta, \\\\theta"
)


def _prose(text: str) -> str:
    """Текст без блоков и фрагментов кода: в коде $, табуляция и переводы строк честные."""
    inside = code_rows(text)
    return "".join(
        _INLINE_CODE.sub(" ", row)
        for i, row in enumerate(lines(text, keepends=True))
        if i not in inside
    )


def spoiled_escapes(text: str) -> bool:
    """Испорчены ли LaTeX-команды чтением JSON с одним обратным слэшем."""
    if _CONTROL.search(text):
        return True
    return any(
        _SPOILED_IN_MATH.search(match.group(1) or match.group(2))
        for match in _MATH.finditer(_prose(text))
    )


def cut_off_line(text: str) -> bool:
    """Оборвана ли последняя строка вне блока кода: в ней не закрыты скобки или код.

    Модель может сама закрыть строку JSON посреди примера, и тогда ответ разбирается без
    ошибки. Скобки считаются без учёта вида, поэтому полуоткрытый интервал [0, 1) честный.
    Лишние закрывающие скобки, как в пункте "1)", не обрыв. Скобки внутри фрагмента кода
    в обратных кавычках не считаются: "символ `[`" честный.
    """
    rows = lines(text)
    filled = [i for i, row in enumerate(rows) if row.strip()]
    if not filled:
        return False
    markers = {row for fence in fences(text) for row in (fence.start, fence.end - 1)}
    if filled[-1] in markers:
        return False
    last = rows[filled[-1]]
    if last.count("`") % 2:
        return True
    plain = _INLINE_CODE.sub(" ", last)
    return sum(map(plain.count, "([{")) > sum(map(plain.count, ")]}"))


def text_problems(text: str) -> list[str]:
    """Причины отказа для писателя; пустой список — дефектов нет."""
    problems = []
    if unclosed_fence(text):
        problems.append("текст оборвался посреди блока кода; допиши его целиком и закрой блок")
    elif cut_off_line(text):
        problems.append(
            "текст оборвался на последней строке: в ней не закрыты скобки или код; "
            "допиши текст до конца"
        )
    if spoiled_escapes(text):
        problems.append(SPOILED_ESCAPES)
    if has_marker(text):
        problems.append(
            "в тексте есть служебный маркер вида <<<МЕТКА-…>>> из запроса; маркеры только "
            "отделяют данные в запросе, не переноси их в текст"
        )
    if AUTHOR_INSTRUCTIONS.lower() in text.lower():
        problems.append(
            f"в тексте есть заголовок «{AUTHOR_INSTRUCTIONS}» из запроса; напиши текст для "
            "кандидата, а не пересказ инструкции"
        )
    return problems


# --- Числа, которых нет в источниках ---------------------------------------------------------


def _forms(value: int, *words: str) -> dict[str, int]:
    return dict.fromkeys(words, value)


def _stem_forms(endings: tuple[str, ...], stems: dict[str, int]) -> dict[str, int]:
    return {stem + ending: value for stem, value in stems.items() for ending in endings}


# fmt: off
# Основы числительных, которые склоняются одинаково: пять, пяти, пятью.
_CARDINAL_STEMS = {
    "пят": 5, "шест": 6, "сем": 7, "восем": 8, "девят": 9, "десят": 10, "одиннадцат": 11,
    "двенадцат": 12, "тринадцат": 13, "четырнадцат": 14, "пятнадцат": 15, "шестнадцат": 16,
    "семнадцат": 17, "восемнадцат": 18, "девятнадцат": 19, "двадцат": 20, "тридцат": 30,
}
_ORDINAL_STEMS = {
    "перв": 1, "втор": 2, "четверт": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8,
    "девят": 9, "десят": 10, "одиннадцат": 11, "двенадцат": 12, "тринадцат": 13,
    "четырнадцат": 14, "пятнадцат": 15, "шестнадцат": 16, "семнадцат": 17,
    "восемнадцат": 18, "девятнадцат": 19, "двадцат": 20, "тридцат": 30, "сороков": 40,
    "пятидесят": 50, "шестидесят": 60, "семидесят": 70, "восьмидесят": 80, "девяност": 90,
    "сот": 100, "тысячн": 1000,
}
# fmt: on

# Количественные числительные во всех падежах; ё заменена на е, как в нормализованном тексте.
_CARDINALS: dict[str, int] = {
    **_forms(0, "ноль", "нуль", "ноля", "нуля"),
    **_forms(1, "один", "одна", "одно", "одного", "одной", "одному", "одним", "одну", "одном"),
    **_forms(2, "два", "две", "двух", "двум", "двумя"),
    **_forms(3, "три", "трех", "трем", "тремя"),
    **_forms(4, "четыре", "четырех", "четырем", "четырьмя"),
    **_stem_forms(("ь", "и", "ью"), _CARDINAL_STEMS),
    **_forms(8, "восьми", "восьмью"),
    **_forms(40, "сорок", "сорока"),
    **_forms(50, "пятьдесят", "пятидесяти", "пятьюдесятью"),
    **_forms(60, "шестьдесят", "шестидесяти", "шестьюдесятью"),
    **_forms(70, "семьдесят", "семидесяти", "семьюдесятью"),
    **_forms(80, "восемьдесят", "восьмидесяти", "восемьюдесятью"),
    **_forms(90, "девяносто", "девяноста"),
    **_forms(100, "сто", "ста"),
    **_forms(200, "двести", "двухсот", "двумстам", "двумястами", "двухстах"),
    **_forms(300, "триста", "трехсот", "тремстам", "тремястами", "трехстах"),
    **_forms(400, "четыреста", "четырехсот", "четыремстам", "четырехстах"),
    **_stem_forms(
        ("сот", "ьсот", "стам", "юстами", "стах"),
        {"пят": 500, "шест": 600, "сем": 700, "девят": 900},
    ),
    **_forms(800, "восемьсот", "восьмисот", "восьмистам", "восьмистах"),
    # Кратность: «втрое выше» в факте и «в три раза выше» в кейсе — одно число.
    **_forms(2, "вдвое"),
    **_forms(3, "втрое"),
    **_forms(4, "вчетверо"),
    **_forms(5, "впятеро"),
    **_forms(10, "вдесятеро"),
}

_ADJECTIVE = ("ый", "ий", "ой", "ая", "ое", "ого", "ому", "ым", "ом", "ую", "ые", "ых", "ыми")

# Порядковые числительные. Они считаются только в источниках: «после десятой эпохи» в факте
# разрешает «после 10-й эпохи» в кейсе. В кейсе порядковое слово — приём рассказа вроде
# «вторая транзакция», а не новое условие.
_ORDINALS: dict[str, int] = {
    **_stem_forms(_ADJECTIVE, _ORDINAL_STEMS),
    **_stem_forms(
        ("ий", "ья", "ье", "ьего", "ьей", "ьему", "ьим", "ьем", "ью", "ьи", "ьих"), {"трет": 3}
    ),
}

_SCALES: dict[str, int] = {
    **_stem_forms(("а", "и", "у", "ей", "ам", "ами", "ах", ""), {"тысяч": 10**3}),
    **_stem_forms(("", "а", "у", "ом", "е", "ы", "ов", "ам", "ами", "ах"), {"миллион": 10**6}),
    **_stem_forms(("", "а", "у", "ом", "е", "ы", "ов", "ам", "ами", "ах"), {"миллиард": 10**9}),
    **_forms(10**3, "тыс"),
    **_forms(10**6, "млн"),
    **_forms(10**9, "млрд"),
}
# Слово разряда без числа перед ним — ровно один разряд: «из миллиона». Множественное число
# вроде «миллионы» или «сотни тысяч» — неопределённое количество, а не число.
_SINGLE_SCALES = {"тысяча", "тысячу", "тысячей"} | {
    stem + ending for stem in ("миллион", "миллиард") for ending in ("", "а", "у", "ом", "е")
}

# Число цифрами: группы по три цифры через пробел, дробная часть через точку или запятую.
# Цифры, приклеенные к букве, — часть имени вроде fp32, int64 или 5xx, а не количество.
_DIGITS = re.compile(
    r"(?<![\w.,])(?:\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,]\d+)?(?![\w])"
    r"(?:\s*(?P<scale>[а-я]+))?"
)
_WORD = re.compile(r"[а-я]+")
# Номер пункта списка в начале строки: «1. Проверять», «2) Не передавать».
_LIST_ITEM = re.compile(r"[ \t]*\d+[.)](?:\s|$)")
# Класс кодов ответа HTTP вроде 5xx разрешает каждый код класса: «от 500 до 599».
_STATUS_CLASS = re.compile(r"(?<!\w)([1-5])xx(?!\w)", re.IGNORECASE)
# Числа, которые встречаются в любом рассказе: «одна транзакция», «два требования»,
# «между двумя позициями».
_IGNORED = {0.0, 1.0, 2.0}


def _numbers(text: str, *, ordinals: bool) -> list[tuple[str, float]]:
    """Числа текста: написанный фрагмент и значение."""
    lowered = text.lower().replace("ё", "е")
    found: list[tuple[str, float]] = []
    blanked = list(lowered)
    for match in _DIGITS.finditer(lowered):
        line_start = lowered.rfind("\n", 0, match.start()) + 1
        if (
            _LIST_ITEM.match(lowered, line_start)
            and not lowered[line_start : match.start()].strip()
        ):
            continue
        raw = match.group(0)
        scale = match.group("scale")
        if scale is not None and scale not in _SCALES:
            raw = raw[: match.start("scale") - match.start()].rstrip()
        digits = re.sub(r"[   ]", "", re.match(r"[\d   .,]+", raw)[0])
        value = float(digits.strip(".,").replace(",", "."))
        if scale in _SCALES:
            value *= _SCALES[scale]
        found.append((text[match.start() : match.start() + len(raw)], value))
        blanked[match.start() : match.start() + len(raw)] = " " * len(raw)
    words = "".join(blanked)
    run: list[re.Match[str]] = []
    total = 0
    pending: int | None = None

    def flush() -> None:
        nonlocal total, pending
        if run and (total or pending is not None):
            found.append((text[run[0].start() : run[-1].end()], float(total + (pending or 0))))
        run.clear()
        total, pending = 0, None

    for match in _WORD.finditer(words):
        word = match.group(0)
        if run and words[run[-1].end() : match.start()].strip():
            flush()
        number = _CARDINALS.get(word)
        if number is None and ordinals:
            number = _ORDINALS.get(word)
        if number is not None:
            run.append(match)
            pending = (pending or 0) + number
        elif word in _SCALES and pending is not None:
            run.append(match)
            total += pending * _SCALES[word]
            pending = None
        elif word in _SINGLE_SCALES and not run:
            run.append(match)
            total = _SCALES[word]
            flush()
        else:
            flush()
    flush()
    return found


def foreign_numbers(text: str, sources: list[str]) -> list[str]:
    """Числа текста, которых нет ни в одном источнике, как они написаны в тексте.

    Числа сравниваются по значению: «10 миллионов» в тексте совпадает с «десять миллионов»
    в факте. Ноль и единица не проверяются.
    """
    allowed = {value for source in sources for _, value in _numbers(source, ordinals=True)}
    for source in sources:
        for match in _STATUS_CLASS.finditer(source):
            allowed.update(
                float(code) for code in range(int(match[1]) * 100, int(match[1]) * 100 + 100)
            )
    foreign: list[str] = []
    for written, value in _numbers(text, ordinals=False):
        if value not in allowed and value not in _IGNORED and written not in foreign:
            foreign.append(written)
    return foreign
