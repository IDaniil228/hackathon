import pytest

from taskgen.markdown import fences, headings, split_frontmatter, unclosed_fence


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
        # Последняя строка незакрытого блока похожа на забор, но короче открывающего.
        ("````\nx\n```", True),
        ("1. шаг\n   ```python\n   x = 1\n   ```\n2. дальше", False),
        ("1. шаг\n   ```python\n   x = 1", True),
    ],
)
def test_unclosed_fence(text, unclosed):
    assert unclosed_fence(text) is unclosed


def test_fence_has_info_content_and_lines():
    [block] = fences("до\n\n```python  extra\nx = 1\n\ny = 2\n```\nпосле")
    assert (block.info, block.content) == ("python  extra", "x = 1\n\ny = 2")
    assert (block.start, block.end, block.closed) == (2, 7, True)


def test_longer_fence_keeps_inner_fences_as_content():
    [block] = fences("````md\n```\ninner\n```\n````")
    assert block.content == "```\ninner\n```"


def test_fence_in_list_item_is_found_without_list_indent():
    [block] = fences("1. шаг\n   ```python\n   x = 1\n   ```")
    assert (block.info, block.content, block.closed) == ("python", "x = 1", True)


def test_headings_skip_code_and_nested_blocks():
    text = "# Шаблон\n## Раздел ##\n```\n## не заголовок\n```\n- ## в списке\n### id +\n"
    assert [(h.level, h.title, h.line) for h in headings(text)] == [
        (1, "Шаблон", 0),
        (2, "Раздел", 1),
        (3, "id +", 6),
    ]


def test_setext_heading_is_a_heading_as_it_renders():
    # Строка --- под абзацем отрисовывается заголовком; разбор видит то же, что читатель.
    assert [(h.level, h.title) for h in headings("текст\n---\n")] == [(2, "текст")]


def test_frontmatter_is_split_from_body():
    assert split_frontmatter("---\na: 1\n---\n## Раздел\n") == ("a: 1", "## Раздел\n")


@pytest.mark.parametrize("text", ["## Раздел\n", "---\na: 1\n## Раздел\n"])
def test_text_without_closed_frontmatter_has_none(text):
    assert split_frontmatter(text) is None
