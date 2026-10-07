import asyncio

import pytest
from sqlalchemy import func, select

from taskgen.bank.importer import (
    ImportStatus,
    TemplateDocument,
    import_documents,
    read_directory,
)
from taskgen.bank.parser import TemplateError, content_hash
from taskgen.db import Template, make_sessionmaker
from taskgen.sandbox.errors import SandboxServiceError
from tests.bank.factory import (
    REFERENCE,
    SHORT_RIGHT,
    local_check,
    make_essay,
    make_template,
    solution,
)
from tests.generation.fakes import LocalSandbox

pytestmark = pytest.mark.integration


def doc(text: str, source: str = "t.md") -> TemplateDocument:
    return TemplateDocument(source=source, text=text)


async def load(session, docs: list[TemplateDocument], check=None):
    return await import_documents(session, docs, check=check or local_check())


async def versions(session, name: str) -> list[tuple[int, bool]]:
    rows = await session.execute(
        select(Template.version, Template.is_current)
        .where(Template.name == name)
        .order_by(Template.version)
    )
    return [tuple(row) for row in rows]


async def test_new_template_is_created_as_current_version_one(db_session, unique):
    text = make_template(name=f"{unique}-a")
    [outcome] = await load(db_session, [doc(text)])
    assert outcome.status is ImportStatus.CREATED
    assert outcome.version == 1
    row = await db_session.get(Template, outcome.template_id)
    assert row.body == text
    assert row.content_hash == content_hash(text)
    assert row.area_code == "algo" and row.grade_code == "junior"
    assert row.skills == ["python", "two-pointers"]
    assert row.kind == "code" and row.runtime == "python" and row.difficulty == 3
    assert await versions(db_session, f"{unique}-a") == [(1, True)]


async def test_same_content_is_not_reimported(db_session, unique):
    text = make_template(name=f"{unique}-a")
    [first] = await load(db_session, [doc(text)])
    [again] = await load(db_session, [doc(text)])
    assert again.status is ImportStatus.UNCHANGED
    assert again.template_id == first.template_id
    assert await versions(db_session, f"{unique}-a") == [(1, True)]


async def test_changed_content_creates_next_version_and_keeps_old(db_session, unique):
    name = f"{unique}-a"
    [first] = await load(db_session, [doc(make_template(name=name))])
    [second] = await load(db_session, [doc(make_template(name=name, difficulty=4))])
    [third] = await load(db_session, [doc(make_template(name=name, difficulty=5))])
    assert (second.status, second.version) == (ImportStatus.UPDATED, 2)
    assert (third.status, third.version) == (ImportStatus.UPDATED, 3)
    assert await versions(db_session, name) == [(1, False), (2, False), (3, True)]
    old = await db_session.get(Template, first.template_id)
    assert old.difficulty == 3


async def test_unknown_references_are_reported(db_session, unique):
    text = make_template(name=f"{unique}-a", area="space", grade="guru", skills=["python", "x"])
    with pytest.raises(TemplateError) as caught:
        await load(db_session, [doc(text, "bad.md")])
    found = {(issue.source, issue.location) for issue in caught.value.issues}
    assert {("bad.md", "area"), ("bad.md", "grade"), ("bad.md", "skills")} <= found
    assert "x" in str(caught.value)


async def test_skill_outside_template_area_is_reported(db_session, unique):
    # django есть в справочнике, но не связан с областью algo в area_skills.
    text = make_template(name=f"{unique}-a", area="algo", skills=["python", "django"])
    with pytest.raises(TemplateError) as caught:
        await load(db_session, [doc(text, "area.md")])
    issues = [issue for issue in caught.value.issues if issue.location == "skills"]
    assert len(issues) == 1
    assert "django" in issues[0].message
    assert "python" not in issues[0].message


async def test_any_bad_file_aborts_whole_batch_and_all_errors_are_listed(db_session, unique):
    good = doc(make_template(name=f"{unique}-good"), "good.md")
    bad_parse = doc(make_template(name=f"{unique}-p", difficulty=99), "parse.md")
    bad_ref = doc(make_essay(name=f"{unique}-r", grade="guru"), "ref.md")
    with pytest.raises(TemplateError) as caught:
        await load(db_session, [good, bad_parse, bad_ref])
    sources = {issue.source for issue in caught.value.issues}
    assert sources == {"parse.md", "ref.md"}
    count = await db_session.scalar(
        select(func.count()).select_from(Template).where(Template.name.like(f"{unique}%"))
    )
    assert count == 0


async def test_duplicate_names_in_one_batch_are_rejected(db_session, unique):
    name = f"{unique}-a"
    batch = [doc(make_template(name=name), "a.md"), doc(make_template(name=name), "b.md")]
    with pytest.raises(TemplateError) as caught:
        await load(db_session, batch)
    assert any(issue.location == "name" for issue in caught.value.issues)
    assert await versions(db_session, name) == []


async def count_named(session, unique: str) -> int:
    return await session.scalar(
        select(func.count()).select_from(Template).where(Template.name.like(f"{unique}%"))
    )


def survivor(name: str) -> str:
    """Шаблон, тесты которого не ловят неверное решение: оно совпадает с верным."""
    same = solution("same", "-", REFERENCE, "Ничем не отличается от верного")
    return make_template(
        name=name, sections={"Решения": solution("reference", "+", REFERENCE) + "\n\n" + same}
    )


async def test_template_failing_sandbox_check_is_rejected_with_seed_and_solution(
    db_session, unique
):
    good = doc(make_template(name=f"{unique}-good"), "good.md")
    with pytest.raises(TemplateError) as caught:
        await load(db_session, [good, doc(survivor(f"{unique}-bad"), "bad.md")])

    [issue] = caught.value.issues
    assert (issue.source, issue.location) == ("bad.md", "seed 0")
    assert "неверное решение 'same'" in issue.message
    assert await count_named(db_session, unique) == 0


async def test_failing_correct_solution_is_rejected(db_session, unique):
    swapped = REFERENCE.replace("return [i, j]", "return [j, i]")
    text = make_template(
        name=f"{unique}-a",
        sections={
            "Решения": solution("reference", "+", swapped)
            + "\n\n"
            + solution("short-right", "-", SHORT_RIGHT)
        },
    )
    with pytest.raises(TemplateError) as caught:
        await load(db_session, [doc(text, "a.md")])
    messages = [issue.message for issue in caught.value.issues]
    assert any("верное решение 'reference': кейсы [1, 2, 3, 6]" in m for m in messages), messages
    assert await count_named(db_session, unique) == 0


async def test_sandbox_and_reference_problems_are_reported_in_one_pass(db_session, unique):
    bad_area = doc(make_essay(name=f"{unique}-r", area="space"), "area.md")
    bad_tests = doc(survivor(f"{unique}-s"), "tests.md")
    with pytest.raises(TemplateError) as caught:
        await load(db_session, [bad_area, bad_tests])
    assert {issue.source for issue in caught.value.issues} == {"area.md", "tests.md"}


async def test_sandbox_down_aborts_import_without_writing(db_session, unique):
    sandbox = LocalSandbox()
    sandbox.down = True
    with pytest.raises(SandboxServiceError):
        await load(db_session, [doc(make_template(name=f"{unique}-a"))], local_check(sandbox))
    assert await count_named(db_session, unique) == 0


async def test_essays_and_quizzes_import_without_sandbox(db_session, unique):
    sandbox = LocalSandbox()
    sandbox.down = True
    [outcome] = await load(db_session, [doc(make_essay(name=f"{unique}-e"))], local_check(sandbox))
    assert outcome.status is ImportStatus.CREATED


async def test_concurrent_update_of_one_name_waits_and_keeps_one_current(
    settings, unique, purge_committed
):
    name = f"{unique}-a"
    maker = make_sessionmaker(settings.database_url)
    async with maker() as session, session.begin():
        await load(session, [doc(make_template(name=name))])

    first, second = maker(), maker()
    await first.begin()
    await second.begin()
    [a] = await load(first, [doc(make_template(name=name, difficulty=4))])
    # Второй импорт ждёт блокировку текущей версии, пока первый не зафиксируется.
    pending = asyncio.create_task(load(second, [doc(make_template(name=name, difficulty=5))]))
    await asyncio.sleep(0.2)
    assert not pending.done()
    await first.commit()
    [b] = await pending
    await second.commit()
    await first.close()
    await second.close()

    assert (a.version, b.version) == (2, 3)
    async with maker() as session:
        assert await versions(session, name) == [(1, False), (2, False), (3, True)]
    await maker.kw["bind"].dispose()


async def test_concurrent_creation_of_one_name_is_serialized(settings, unique, purge_committed):
    name = f"{unique}-a"
    maker = make_sessionmaker(settings.database_url)
    first, second = maker(), maker()
    await first.begin()
    await second.begin()
    await load(first, [doc(make_template(name=name))])
    pending = asyncio.create_task(load(second, [doc(make_template(name=name))]))
    await asyncio.sleep(0.2)
    assert not pending.done()
    await first.commit()
    [b] = await pending
    await second.commit()
    await first.close()
    await second.close()
    assert b.status is ImportStatus.UNCHANGED
    async with maker() as session:
        assert await versions(session, name) == [(1, True)]
    await maker.kw["bind"].dispose()


def test_read_directory_takes_markdown_recursively(tmp_path):
    (tmp_path / "algo").mkdir()
    (tmp_path / "algo" / "b.md").write_text("B", encoding="utf-8")
    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("skip", encoding="utf-8")
    docs = read_directory(tmp_path)
    assert [(d.source, d.text) for d in docs] == [("a.md", "A"), ("algo/b.md", "B")]
