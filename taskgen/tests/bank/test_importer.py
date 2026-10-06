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
from tests.bank.factory import make_essay, make_template

pytestmark = pytest.mark.integration


def doc(text: str, source: str = "t.md") -> TemplateDocument:
    return TemplateDocument(source=source, text=text)


async def versions(session, name: str) -> list[tuple[int, bool]]:
    rows = await session.execute(
        select(Template.version, Template.is_current)
        .where(Template.name == name)
        .order_by(Template.version)
    )
    return [tuple(row) for row in rows]


async def test_new_template_is_created_as_current_version_one(db_session, unique):
    text = make_template(name=f"{unique}-a")
    [outcome] = await import_documents(db_session, [doc(text)])
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
    [first] = await import_documents(db_session, [doc(text)])
    [again] = await import_documents(db_session, [doc(text)])
    assert again.status is ImportStatus.UNCHANGED
    assert again.template_id == first.template_id
    assert await versions(db_session, f"{unique}-a") == [(1, True)]


async def test_changed_content_creates_next_version_and_keeps_old(db_session, unique):
    name = f"{unique}-a"
    [first] = await import_documents(db_session, [doc(make_template(name=name))])
    [second] = await import_documents(db_session, [doc(make_template(name=name, difficulty=4))])
    [third] = await import_documents(db_session, [doc(make_template(name=name, difficulty=5))])
    assert (second.status, second.version) == (ImportStatus.UPDATED, 2)
    assert (third.status, third.version) == (ImportStatus.UPDATED, 3)
    assert await versions(db_session, name) == [(1, False), (2, False), (3, True)]
    old = await db_session.get(Template, first.template_id)
    assert old.difficulty == 3


async def test_unknown_references_are_reported(db_session, unique):
    text = make_template(name=f"{unique}-a", area="space", grade="guru", skills=["python", "x"])
    with pytest.raises(TemplateError) as caught:
        await import_documents(db_session, [doc(text, "bad.md")])
    found = {(issue.source, issue.location) for issue in caught.value.issues}
    assert {("bad.md", "area"), ("bad.md", "grade"), ("bad.md", "skills")} <= found
    assert "x" in str(caught.value)


async def test_any_bad_file_aborts_whole_batch_and_all_errors_are_listed(db_session, unique):
    good = doc(make_template(name=f"{unique}-good"), "good.md")
    bad_parse = doc(make_template(name=f"{unique}-p", difficulty=99), "parse.md")
    bad_ref = doc(make_essay(name=f"{unique}-r", grade="guru"), "ref.md")
    with pytest.raises(TemplateError) as caught:
        await import_documents(db_session, [good, bad_parse, bad_ref])
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
        await import_documents(db_session, batch)
    assert any(issue.location == "name" for issue in caught.value.issues)
    assert await versions(db_session, name) == []


async def test_concurrent_update_of_one_name_waits_and_keeps_one_current(
    settings, unique, purge_committed
):
    name = f"{unique}-a"
    maker = make_sessionmaker(settings.database_url)
    async with maker() as session, session.begin():
        await import_documents(session, [doc(make_template(name=name))])

    first, second = maker(), maker()
    await first.begin()
    await second.begin()
    [a] = await import_documents(first, [doc(make_template(name=name, difficulty=4))])
    # Второй импорт ждёт блокировку текущей версии, пока первый не зафиксируется.
    pending = asyncio.create_task(
        import_documents(second, [doc(make_template(name=name, difficulty=5))])
    )
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
    await import_documents(first, [doc(make_template(name=name))])
    pending = asyncio.create_task(import_documents(second, [doc(make_template(name=name))]))
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
