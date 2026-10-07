import re

import pytest

from taskgen.bank.parser import TemplateError, content_hash, parse_template
from taskgen.contracts import IOKind, TaskKind
from tests.bank.factory import (
    REFERENCE,
    SHORT_RIGHT,
    make_essay,
    make_quiz,
    make_stdio,
    make_template,
    solution,
)

CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


def issues_of(text: str) -> list:
    with pytest.raises(TemplateError) as caught:
        parse_template(text, source="t.md")
    assert caught.value.issues, "ошибка без единой причины"
    for issue in caught.value.issues:
        assert CYRILLIC.search(issue.message), issue.message
    return caught.value.issues


def locations(text: str) -> set[str]:
    return {issue.location for issue in issues_of(text)}


def test_valid_code_template_is_parsed_into_spec():
    spec = parse_template(make_template(), source="t.md")
    assert spec.name == "algo-two-sum-sorted"
    assert spec.kind is TaskKind.CODE
    assert spec.skills == ["python", "two-pointers"]
    assert spec.params["n"].min == 1000
    assert spec.params["unique"].choices == [True, False]
    assert spec.entrypoint == "two_sum"
    assert "def two_sum" in spec.reference_solution
    # Заголовок внутри блока кода не начинает новый раздел.
    assert "## Два указателя" in spec.reference_solution
    assert "def generate" in spec.test_generator
    assert "{n}" in spec.generator_prompt
    assert [item.id for item in spec.rubric] == ["linear", "edge"]
    assert spec.rubric[0].passing_example == "два указателя с концов массива"
    assert spec.rubric[0].failing_example == "вложенный цикл по всем парам"
    assert spec.rubric[1].passing_example is None


def test_valid_essay_template_has_no_code_parts():
    spec = parse_template(make_essay(), source="e.md")
    assert spec.kind is TaskKind.ESSAY
    assert spec.entrypoint is None and spec.reference_solution is None
    assert spec.test_generator is None and spec.runtime is None


def test_essay_sections_are_parsed():
    text = make_essay(
        params={"lang": {"type": "choice", "choices": ["Python", "Go"]}},
        sections={
            "Вопросы": "1. Что выбрать на {lang}?\n2. Почему?",
            "Факты": "- cpu: сервис на {lang} считает на процессоре\n- cores: на машине 8 ядер",
            "Нельзя раскрывать": "- общая память\n- GIL",
        },
    )
    spec = parse_template(text, source="e.md")
    assert spec.questions == "1. Что выбрать на {lang}?\n2. Почему?"
    assert [(f.id, f.text) for f in spec.scenario_facts] == [
        ("cpu", "сервис на {lang} считает на процессоре"),
        ("cores", "на машине 8 ядер"),
    ]
    assert spec.forbidden == ["общая память", "GIL"]


def test_forbidden_section_is_optional_for_essay():
    spec = parse_template(make_essay(sections={"Нельзя раскрывать": None}), source="e.md")
    assert spec.forbidden == []


def test_code_template_has_no_essay_parts():
    spec = parse_template(make_template(), source="t.md")
    assert spec.questions is None and spec.scenario_facts == [] and spec.forbidden == []


def test_scoring_override_with_known_weight_is_accepted():
    spec = parse_template(make_template(scoring={"weight_performance": 0.15}), source="t.md")
    assert spec.scoring == {"weight_performance": 0.15}


def test_case_groups_are_parsed_with_optional_required():
    groups = {"manual": {"weight": 2, "required": True}, "random": {"weight": 0.5}}
    spec = parse_template(make_template(case_groups=groups), source="t.md")
    assert spec.case_groups["manual"].weight == 2 and spec.case_groups["manual"].required
    # Не заданный required берётся из настройки required_case_groups при оценке.
    assert spec.case_groups["random"].weight == 0.5 and spec.case_groups["random"].required is None


def test_template_without_case_groups_has_none():
    assert parse_template(make_template(case_groups=None), source="t.md").case_groups == {}


@pytest.mark.parametrize(
    "groups",
    [
        {"manual": {"weight": 0}},
        {"manual": {"weight": -1}},
        {"manual": {}},
        {"manual": {"weight": 1, "share": 2}},
        {"Bad Name": {"weight": 1}},
        {"manual": 1},
    ],
)
def test_invalid_case_groups_are_named(groups):
    found = locations(make_template(case_groups=groups))
    assert any(loc.startswith("case_groups") for loc in found), found


def test_case_groups_belong_to_code_templates_only():
    text = make_essay(case_groups={"manual": {"weight": 1}})
    assert any(loc.startswith("case_groups") for loc in locations(text))


def test_content_hash_is_sha256_of_whole_text():
    text = make_template()
    assert content_hash(text) == content_hash(text)
    assert len(content_hash(text)) == 64
    assert content_hash(text) != content_hash(text + " ")


def test_missing_frontmatter_is_reported():
    assert "frontmatter" in locations("## Генерация\n\nтекст\n")


def test_broken_yaml_is_reported():
    assert "frontmatter" in locations("---\nname: [unclosed\n---\n\n## Генерация\n\nx\n")


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"name": "Bad_Name"}, "name"),
        ({"difficulty": 11}, "difficulty"),
        ({"difficulty": 0}, "difficulty"),
        ({"expected_minutes": 0}, "expected_minutes"),
        ({"kind": "poll"}, "kind"),
        ({"skills": "python"}, "skills"),
        ({"description": "  "}, "description"),
        ({"skills": ["python", "python"]}, "skills"),
        ({"scoring": {"weight_magic": 1.0}}, "scoring"),
        # Вес блока задачек — настройка сессии, шаблон задания его не меняет.
        ({"scoring": {"weight_quiz": 1.0}}, "scoring"),
        ({"params": {"n": {"type": "int", "min": 5}}}, "params.n"),
        ({"params": {"n": {"type": "int", "min": 5, "max": 1}}}, "params.n"),
        ({"params": {"n": {"type": "choice", "choices": []}}}, "params.n"),
        ({"params": {"n": {"type": "vector"}}}, "params.n"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_invalid_frontmatter_field_is_named(overrides, field):
    assert any(loc.startswith(field) for loc in locations(make_template(**overrides)))


@pytest.mark.parametrize("field", ["name", "description", "area", "grade", "skills", "difficulty"])
def test_missing_required_field_is_named(field):
    assert field in locations(make_template(drop=(field,)))


def test_code_template_requires_runtime():
    assert "runtime" in locations(make_template(runtime=None))


@pytest.mark.parametrize("runtime", ["pyhton", "analyzers"])
def test_runtime_must_be_a_language_environment_of_the_registry(runtime):
    # Опечатка проходила разбор и всплывала только ошибкой песочницы при импорте, а в
    # служебном окружении анализаторов задания не решают.
    assert "runtime" in locations(make_template(runtime=runtime))


def test_function_task_needs_a_runtime_with_a_driver():
    # Драйвера задач-функций для Bash нет: шаблон не дал бы ни одного экземпляра.
    found = issues_of(make_template(runtime="bash"))
    assert any(i.location == "io" and "драйвер" in i.message for i in found), found


def _solutions_in(language: str, code: str = REFERENCE) -> dict[str, str]:
    wrong = SHORT_RIGHT if code == REFERENCE else code
    return {
        "Решения": solution("a", "+", code, language=language)
        + "\n\n"
        + solution("b", "-", wrong, language=language)
    }


@pytest.mark.parametrize("language", ["", "cpp", "python-ml"])
def test_solution_block_names_the_language_of_the_runtime(language):
    # Метка блока — язык кода, окружение задаёт только runtime: python-ml пишется на python.
    found = issues_of(make_template(runtime="python-ml", sections=_solutions_in(language)))
    assert any("язык" in issue.message for issue in found), found


@pytest.mark.parametrize("language", ["python", "py"])
def test_python_ml_solutions_are_labelled_python(language):
    spec = parse_template(
        make_template(runtime="python-ml", sections=_solutions_in(language)), source="t.md"
    )
    assert spec.runtime == "python-ml"


def test_python_ml_solution_is_checked_as_python():
    broken = REFERENCE.replace("def two_sum(nums, target):", "def two_sum(nums, target)")
    found = issues_of(make_template(runtime="python-ml", sections=_solutions_in("python", broken)))
    assert any("синтаксическая ошибка" in issue.message for issue in found), found


def test_test_generator_block_is_labelled_python():
    tests = make_template().split("## Тесты\n\n", 1)[1].split("\n## ", 1)[0]
    found = issues_of(make_template(sections={"Тесты": tests.replace("```python", "```cpp", 1)}))
    assert any(issue.location == "раздел «Тесты»" for issue in found), found


# --- Вид задания с кодом и проверка ответа ------------------------------------------------

CHECKER_SECTION = (
    "```python\nimport sys\n_, out, ans = (open(n).read().split() for n in sys.argv[1:4])\n"
    "sys.exit(0 if sorted(out) == sorted(ans) else 1)\n```"
)


def test_code_template_requires_io():
    assert "io" in locations(make_template(io=None))


def test_unknown_io_is_named():
    assert "io" in locations(make_template(io="socket"))


@pytest.mark.parametrize("maker", [make_essay, make_quiz])
def test_io_belongs_to_code_templates_only(maker):
    assert "io" in locations(maker(io="stdio"))


def test_function_template_checks_answers_as_json_by_default():
    spec = parse_template(make_template(), source="t.md")
    assert spec.io is IOKind.FUNCTION
    assert spec.check.mode == "json" and spec.check.checker is None


def test_stdio_template_is_parsed_without_signature():
    spec = parse_template(make_stdio(), source="s.md")
    assert spec.io is IOKind.STDIO
    assert spec.entrypoint is None
    assert spec.check.mode == "tokens"
    assert [s.id for s in spec.solutions] == ["reference", "skips-empty"]


def test_stdio_template_must_not_carry_signature():
    text = make_stdio(sections={"Сигнатура": "```\nmain\n```"})
    assert "раздел «Сигнатура»" in locations(text)


def test_function_template_requires_signature():
    assert "раздел «Сигнатура»" in locations(make_template(sections={"Сигнатура": None}))


def test_stdio_python_solution_still_needs_valid_syntax():
    broken = make_stdio(
        sections={
            "Решения": solution("reference", "+", "print(")
            + "\n\n"
            + solution("bad", "-", "print(1)\n")
        }
    )
    assert "раздел «Решения», решение 'reference'" in locations(broken)


@pytest.mark.parametrize("mode", ["tokens", "exact", "float", "json"])
def test_stdio_takes_any_builtin_mode(mode):
    assert parse_template(make_stdio(check=mode), source="s.md").check.mode == mode


@pytest.mark.parametrize("mode", ["tokens", "exact", "float"])
def test_function_answers_are_json_so_text_modes_are_rejected(mode):
    assert "check" in locations(make_template(check=mode))


def test_unknown_check_mode_is_named():
    assert "check" in locations(make_stdio(check="fuzzy"))


@pytest.mark.parametrize("maker", [make_template, make_stdio])
def test_checker_mode_takes_program_from_its_section(maker):
    spec = parse_template(
        maker(check="checker", sections={"Проверяльщик": CHECKER_SECTION}), source="t.md"
    )
    assert spec.check.mode == "checker"
    assert spec.check.checker.runtime == "python"
    assert "sorted(out)" in spec.check.checker.code


def test_checker_mode_requires_its_section():
    assert "раздел «Проверяльщик»" in locations(make_stdio(check="checker"))


def test_checker_section_needs_checker_mode():
    text = make_stdio(sections={"Проверяльщик": CHECKER_SECTION})
    assert "раздел «Проверяльщик»" in locations(text)


@pytest.mark.parametrize("label", ["", "pyhton", "analyzers"])
def test_checker_block_names_a_language_runtime(label):
    text = make_stdio(check="checker", sections={"Проверяльщик": f"```{label}\nexit 0\n```"})
    assert "раздел «Проверяльщик»" in locations(text)


def test_checker_section_belongs_to_code_templates_only():
    text = make_essay() + f"\n## Проверяльщик\n\n{CHECKER_SECTION}\n"
    assert "раздел «Проверяльщик»" in locations(text)


def test_essay_must_not_carry_runtime():
    assert "runtime" in locations(make_essay(runtime="python"))


@pytest.mark.parametrize("section", ["Решения", "Сигнатура", "Тесты"])
def test_code_template_requires_code_sections(section):
    assert f"раздел «{section}»" in locations(make_template(sections={section: None}))


@pytest.mark.parametrize("section", ["Решения", "Сигнатура", "Тесты"])
def test_essay_must_not_carry_code_sections(section):
    from tests.bank.factory import CODE_SECTIONS

    text = make_essay() + f"\n## {section}\n\n{CODE_SECTIONS[section]}\n"
    assert f"раздел «{section}»" in locations(text)


@pytest.mark.parametrize("section", ["Вопросы", "Факты"])
def test_essay_requires_questions_and_facts(section):
    assert f"раздел «{section}»" in locations(make_essay(sections={section: None}))


@pytest.mark.parametrize(
    ("section", "text"),
    [
        ("Вопросы", "1. Что выбрать?"),
        ("Факты", "- cpu: сервис считает"),
        ("Нельзя раскрывать", "- GIL"),
    ],
)
def test_code_template_must_not_carry_essay_sections(section, text):
    assert f"раздел «{section}»" in locations(make_template(sections={section: text}))


@pytest.mark.parametrize(
    ("section", "text"),
    [
        ("Вопросы", ""),
        ("Факты", ""),
        ("Факты", "просто текст без пунктов"),
        ("Факты", "- без идентификатора"),
        ("Факты", "- a: x\n- a: y"),
        ("Факты", "- a: x\n  + пример"),
        ("Нельзя раскрывать", ""),
        ("Нельзя раскрывать", "просто текст"),
        ("Нельзя раскрывать", "- ---"),
    ],
)
def test_malformed_essay_section_is_reported(section, text):
    assert f"раздел «{section}»" in locations(make_essay(sections={section: text}))


@pytest.mark.parametrize("section", ["Вопросы", "Факты"])
def test_unknown_placeholder_in_essay_section_is_reported(section):
    text = {"Вопросы": "1. Что на {lang}?", "Факты": "- cpu: сервис на {lang}"}[section]
    assert f"раздел «{section}»" in locations(make_essay(sections={section: text}))


@pytest.mark.parametrize(
    ("section", "text"),
    [
        # Регистр, пробелы и пунктуация не прячут совпадение.
        ("Вопросы", "1. Где здесь ОБЩАЯ  память?"),
        ("Вопросы", "1. Нужен ли тут МЬЮТЕКС?"),
        ("Факты", "- shared: у потоков процесса Общая—память"),
    ],
)
def test_forbidden_phrase_in_questions_or_facts_is_reported(section, text):
    # Вопрос, который сам называет ответ, обесценивает рубрику; факт с запрещённой фразой
    # противоречит проверке ситуации, и шаблон никогда не даст экземпляра.
    assert f"раздел «{section}»" in locations(make_essay(sections={section: text}))


def test_forbidden_phrase_in_param_choice_is_reported():
    params = {"lang": {"type": "choice", "choices": ["Python с мьютексом", "Go"]}}
    assert "params.lang" in locations(make_essay(params=params))


def test_forbidden_phrase_matches_yo_as_ye():
    text = make_essay(
        sections={"Вопросы": "1. Что ещё мешает?", "Нельзя раскрывать": "- ЕЩЕ мешает"}
    )
    assert "раздел «Вопросы»" in locations(text)


@pytest.mark.parametrize("section", ["Генерация", "Рубрика"])
def test_every_template_requires_common_sections(section):
    assert f"раздел «{section}»" in locations(make_template(sections={section: None}))


def test_unknown_and_duplicate_sections_are_reported():
    text = make_template() + "\n## Примечания\n\nx\n\n## Рубрика\n\n- a: b\n"
    found = locations(text)
    assert "раздел «Примечания»" in found
    assert "раздел «Рубрика»" in found


def test_text_before_first_section_is_reported_but_title_is_allowed():
    head, body = make_template().split("---\n\n", 1)
    assert parse_template(f"{head}---\n\n# Две суммы\n\n{body}", source="t.md")
    assert "тело" in locations(f"{head}---\n\nпотерянный текст\n\n{body}")


def test_section_without_code_block_is_reported():
    assert "раздел «Тесты»" in locations(make_template(sections={"Тесты": "просто текст"}))


def test_section_with_two_code_blocks_is_reported():
    two = "```python\ndef generate(a, b):\n    pass\n```\n\n```python\nx = 1\n```"
    assert "раздел «Тесты»" in locations(make_template(sections={"Тесты": two}))


def test_signature_must_be_one_identifier():
    bad = make_template(sections={"Сигнатура": "```\ntwo sum\n```"})
    assert "раздел «Сигнатура»" in locations(bad)


def test_test_generator_must_define_generate():
    tests = "```python\ndef cases(params, rng):\n    return []\n```"
    assert "раздел «Тесты»" in locations(make_template(sections={"Тесты": tests}))


def solutions_issues(section: str | None) -> list[str]:
    """Сообщения разбора о разделе «Решения», включая сообщения об одном решении."""
    found = issues_of(make_template(sections={"Решения": section}))
    return [i.message for i in found if i.location.startswith("раздел «Решения»")]


def test_solutions_are_parsed_in_order_with_marks():
    fast = solution("fast", "+", REFERENCE)
    slow_code = (
        "def two_sum(nums, target):\n"
        "    for i in range(len(nums)):\n"
        "        for j in range(i + 1, len(nums)):\n"
        "            if nums[i] + nums[j] == target:\n"
        "                return [i, j]\n"
        "    return []\n"
    )
    slow = solution("slow", "+", slow_code, "Честный перебор пар")
    wrong = solution("short-right", "-", SHORT_RIGHT, "Правый указатель стартует не с конца")
    text = make_template(sections={"Решения": f"{fast}\n\n{slow}\n\n{wrong}"})

    spec = parse_template(text, source="t.md")

    assert [(s.id, s.correct) for s in spec.solutions] == [
        ("fast", True),
        ("slow", True),
        ("short-right", False),
    ]
    assert spec.solutions[1].description == "Честный перебор пар"
    assert spec.solutions[0].description == ""
    assert spec.solutions[2].code == SHORT_RIGHT.rstrip("\n")
    # Мерило и прогон при генерации — первое верное решение, а не первое в разделе.
    assert spec.reference_solution == REFERENCE.rstrip("\n")


def test_reference_is_the_first_correct_solution_even_after_a_wrong_one():
    wrong = solution("short-right", "-", SHORT_RIGHT)
    text = make_template(sections={"Решения": f"{wrong}\n\n" + solution("ok", "+", REFERENCE)})
    assert parse_template(text, source="t.md").reference_solution == REFERENCE.rstrip("\n")


def test_longer_fence_keeps_inner_backticks_in_solution_code():
    # Строка из трёх обратных кавычек закрыла бы блок из трёх, но не блок из четырёх.
    inner = REFERENCE.replace("    return []\n", '    note = """\n```\n"""\n    return []\n')
    block = f"### reference +\n\n````python\n{inner}````"
    text = make_template(
        sections={"Решения": block + "\n\n" + solution("short-right", "-", SHORT_RIGHT)}
    )
    spec = parse_template(text, source="t.md")
    assert spec.reference_solution == inner.rstrip("\n")


@pytest.mark.parametrize(
    ("section", "fragment"),
    [
        (None, "обязателен"),
        (solution("only", "+", REFERENCE), "неверное решение"),
        (solution("only", "-", SHORT_RIGHT), "верное решение"),
        (
            solution("a", "+", REFERENCE) + "\n\n" + solution("a", "-", SHORT_RIGHT),
            "повторяется",
        ),
        (f"### reference\n\n```python\n{REFERENCE}```", "'### id +'"),
        (f"### reference ±\n\n```python\n{REFERENCE}```", "'### id +'"),
        ("текст\n\n" + solution("a", "+", REFERENCE), "до первого решения"),
        ("### a +\n\nбез кода\n\n" + solution("b", "-", SHORT_RIGHT), "ровно один"),
        (
            solution("a", "+", REFERENCE)
            + f"\n\n```python\n{REFERENCE}```\n\n"
            + solution("b", "-", SHORT_RIGHT),
            "ровно один",
        ),
        (
            solution("a", "+", REFERENCE)
            + "\n\n"
            + solution("b", "-", "def other(nums, target):\n    return []\n"),
            "two_sum",
        ),
        (
            solution("a", "+", REFERENCE)
            + "\n\n"
            + solution("b", "-", "def two_sum(nums, target)\n    return []\n"),
            "синтаксическая ошибка",
        ),
    ],
    ids=[
        "missing",
        "no-wrong",
        "no-correct",
        "duplicate-id",
        "no-mark",
        "bad-mark",
        "stray-text",
        "no-block",
        "two-blocks",
        "no-entrypoint",
        "syntax-error",
    ],
)
def test_broken_solutions_are_reported(section, fragment):
    messages = solutions_issues(section)
    assert any(fragment in message for message in messages), messages


def test_solution_issue_names_the_solution():
    bad = solution("b", "-", "def other(nums, target):\n    return []\n")
    found = issues_of(
        make_template(sections={"Решения": solution("a", "+", REFERENCE) + "\n\n" + bad})
    )
    assert [i.location for i in found] == ["раздел «Решения», решение 'b'"]


def test_unknown_placeholder_in_generation_is_reported():
    text = make_template(sections={"Генерация": "Массив длины {size}"})
    assert "раздел «Генерация»" in locations(text)


@pytest.mark.parametrize(
    "rubric",
    [
        "",
        "просто текст без пунктов",
        "- без идентификатора",
        "- a: x\n- a: y",
        "- a: x\n  * лишнее",
    ],
)
def test_malformed_rubric_is_reported(rubric):
    assert "раздел «Рубрика»" in locations(make_template(sections={"Рубрика": rubric}))


def test_all_problems_are_reported_at_once():
    text = make_template(difficulty=42, sections={"Сигнатура": None, "Рубрика": ""})
    found = locations(text)
    assert {"difficulty", "раздел «Сигнатура»", "раздел «Рубрика»"} <= found


def test_error_text_names_source_and_location():
    with pytest.raises(TemplateError) as caught:
        parse_template(make_template(difficulty=42), source="algo/two-sum.md")
    assert "algo/two-sum.md" in str(caught.value)
    assert "difficulty" in str(caught.value)


# --- Тестовые задачки ----------------------------------------------------------------------

VARIANT_POSITIVE = """### positive

Факты:
- weights: время проезда по каждой дороге — положительное число минут

Ответ:
+ Алгоритм Дейкстры
- Обход в ширину
- Алгоритм Беллмана — Форда
- Алгоритм Флойда — Уоршелла"""

VARIANT_NEGATIVE = """### negative

Факты:
- weights: у части дорог время отрицательное: там платят за проезд

Ответ:
+ Алгоритм Беллмана — Форда
- Алгоритм Дейкстры
- Обход в ширину
- Алгоритм Флойда — Уоршелла"""


def quiz(variants: str | None = None, **sections) -> str:
    merged = {"Варианты": variants} if variants is not None else {}
    return make_quiz(sections=merged | sections)


def test_valid_quiz_template_is_parsed_into_variants():
    spec = parse_template(make_quiz(), source="t.md")
    assert spec.kind is TaskKind.QUIZ
    assert spec.question.startswith("Какой алгоритм")
    assert [variant.id for variant in spec.variants] == ["positive", "unit"]
    positive = spec.variants[0]
    assert [fact.id for fact in positive.facts] == ["weights", "size"]
    assert positive.facts[1].text == "в городе {n} перекрёстков"
    assert positive.correct == "Алгоритм Дейкстры"
    assert positive.wrong == [
        "Обход в ширину",
        "Алгоритм Беллмана — Форда",
        "Алгоритм Флойда — Уоршелла",
    ]
    assert spec.forbidden == ["дейкстр", "в ширину"]
    # Рубрики, эталона и вопросов эссе у задачки нет: ответ сверяется с ключом варианта.
    assert spec.rubric == [] and spec.entrypoint is None and spec.questions is None


def test_quiz_requires_question_and_variants():
    found = locations(make_quiz(sections={"Вопрос": None, "Варианты": None}))
    assert {"раздел «Вопрос»", "раздел «Варианты»"} <= found


@pytest.mark.parametrize("section", ["Рубрика", "Решения", "Вопросы", "Факты"])
def test_quiz_must_not_carry_other_kinds_sections(section):
    assert f"раздел «{section}»" in locations(make_quiz(sections={section: "- a: b"}))


def test_quiz_sections_belong_only_to_quiz():
    assert "раздел «Вопрос»" in locations(make_essay(sections={"Вопрос": "Что выбрать?"}))
    assert "раздел «Варианты»" in locations(make_template(sections={"Варианты": VARIANT_POSITIVE}))


def test_quiz_needs_two_variants():
    found = issues_of(quiz(VARIANT_POSITIVE))
    assert any("не меньше 2 вариантов" in issue.message for issue in found)


@pytest.mark.parametrize(
    ("variant", "fragment"),
    [
        # Два верных ответа: ключ неоднозначен.
        (VARIANT_NEGATIVE.replace("- Алгоритм Дейкстры", "+ Алгоритм Дейкстры"), "ровно один"),
        # Без верного ответа.
        (VARIANT_NEGATIVE.replace("+ Алгоритм Беллмана", "- Алгоритм Беллмана"), "ровно один"),
        # Двух неверных мало: угадать проще, чем при четырёх вариантах.
        (VARIANT_NEGATIVE.replace("- Алгоритм Флойда — Уоршелла", ""), "не меньше 3"),
        # Повтор ответа внутри варианта.
        (VARIANT_NEGATIVE.replace("- Обход в ширину", "- Алгоритм Дейкстры"), "повторя"),
        # Вариант без фактов: из пустого кейса ответ не следует.
        (
            VARIANT_NEGATIVE.replace(
                "- weights: у части дорог время отрицательное: там платят за проезд", ""
            ),
            "факт",
        ),
        # Строка, которая не факт и не ответ.
        (VARIANT_NEGATIVE.replace("Ответ:", "Ответ:\nпросто строка"), "строка"),
    ],
)
def test_malformed_variant_is_reported(variant, fragment):
    found = issues_of(quiz(f"{VARIANT_POSITIVE}\n\n{variant}"))
    messages = [issue.message for issue in found if issue.location == "раздел «Варианты»"]
    assert any(fragment in message for message in messages), messages


def test_duplicate_variant_ids_are_reported():
    twin = VARIANT_NEGATIVE.replace("### negative", "### positive")
    found = issues_of(quiz(f"{VARIANT_POSITIVE}\n\n{twin}"))
    assert any("positive" in issue.message and "повторя" in issue.message for issue in found)


def test_variants_with_one_and_the_same_answer_are_reported():
    # Утёкший ответ одного экземпляра подходит к любому другому: смысл вариантов потерян.
    same = VARIANT_POSITIVE.replace("### positive", "### positive-2")
    found = issues_of(quiz(f"{VARIANT_POSITIVE}\n\n{same}"))
    assert any("верный ответ" in issue.message for issue in found)


def test_text_before_first_variant_is_reported():
    found = issues_of(quiz(f"Пояснение\n\n{VARIANT_POSITIVE}\n\n{VARIANT_NEGATIVE}"))
    assert any("до первого варианта" in issue.message for issue in found)


def test_forbidden_phrase_in_quiz_question_or_facts_is_reported():
    in_question = make_quiz(sections={"Вопрос": "Подойдёт ли алгоритм Дейкстры?"})
    assert "раздел «Вопрос»" in locations(in_question)
    in_facts = quiz(
        f"{VARIANT_POSITIVE}\n\n{VARIANT_NEGATIVE}", **{"Нельзя раскрывать": "- платят за проезд"}
    )
    assert "раздел «Варианты»" in locations(in_facts)


def test_forbidden_phrase_may_appear_in_answer_options():
    # Названия алгоритмов — сами варианты ответа, их видит кандидат. Запрет действует на
    # кейс: он не должен назвать верный ответ.
    spec = parse_template(make_quiz(), source="t.md")
    assert "дейкстр" in spec.forbidden
    assert any("Дейкстр" in variant.correct for variant in spec.variants)


def test_unknown_placeholder_in_question_or_variants_is_reported():
    assert "раздел «Вопрос»" in locations(make_quiz(sections={"Вопрос": "Что при {m}?"}))
    bad = VARIANT_NEGATIVE.replace("отрицательное", "отрицательное у {m} дорог")
    assert "раздел «Варианты»" in locations(quiz(f"{VARIANT_POSITIVE}\n\n{bad}"))
