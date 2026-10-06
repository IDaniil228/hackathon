import re

import pytest

from taskgen.bank.parser import TemplateError, content_hash, parse_template, unclosed_fence
from taskgen.contracts import TaskKind
from tests.bank.factory import make_essay, make_template

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


def test_essay_must_not_carry_runtime():
    assert "runtime" in locations(make_essay(runtime="python"))


@pytest.mark.parametrize("section", ["Эталон", "Сигнатура", "Тесты"])
def test_code_template_requires_code_sections(section):
    assert f"раздел «{section}»" in locations(make_template(sections={section: None}))


@pytest.mark.parametrize("section", ["Эталон", "Сигнатура", "Тесты"])
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
    assert "раздел «Эталон»" in locations(make_template(sections={"Эталон": "просто текст"}))


def test_section_with_two_code_blocks_is_reported():
    two = "```python\ndef two_sum(a, b):\n    pass\n```\n\n```python\nx = 1\n```"
    assert "раздел «Эталон»" in locations(make_template(sections={"Эталон": two}))


def test_signature_must_be_one_identifier():
    bad = make_template(sections={"Сигнатура": "```\ntwo sum\n```"})
    assert "раздел «Сигнатура»" in locations(bad)


def test_reference_must_define_signature_function():
    other = "```python\ndef other(nums, target):\n    return []\n```"
    assert "раздел «Эталон»" in locations(make_template(sections={"Эталон": other}))


def test_python_syntax_error_in_code_is_reported():
    broken = "```python\ndef two_sum(nums, target)\n    return []\n```"
    assert "раздел «Эталон»" in locations(make_template(sections={"Эталон": broken}))


def test_test_generator_must_define_generate():
    tests = "```python\ndef cases(params, rng):\n    return []\n```"
    assert "раздел «Тесты»" in locations(make_template(sections={"Тесты": tests}))


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


@pytest.mark.parametrize(
    ("text", "unclosed"),
    [
        ("Условие\n```python\ninput = [[", True),
        ("```\nx = 1\n```\nдальше текст", False),
        # Внутренний более короткий забор не закрывает внешний.
        ("````\n```\nx\n```\n", True),
        ("~~~\n```\n~~~\n", False),
        # Внутри блока забор с текстом после маркера — строка кода, а не закрывающий забор.
        ("```\nx\n```python\n", True),
        ("Без кода", False),
    ],
)
def test_unclosed_fence(text, unclosed):
    assert unclosed_fence(text) is unclosed
