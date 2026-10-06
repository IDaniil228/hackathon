import io

import pytest
from sqlalchemy import select

from taskgen import cli
from taskgen.bank.cli import run_import
from taskgen.db import Template, TemplateEmbedding, make_sessionmaker
from tests.bank.conftest import FakeEmbedder
from tests.bank.factory import make_essay, make_template

pytestmark = pytest.mark.integration


async def stored_names(settings, unique) -> set[str]:
    maker = make_sessionmaker(settings.database_url)
    async with maker() as session:
        names = set(
            await session.scalars(select(Template.name).where(Template.name.like(f"{unique}%")))
        )
    await maker.kw["bind"].dispose()
    return names


def write(folder, relative: str, text: str) -> None:
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def test_import_directory_writes_templates_and_embeddings(
    tmp_path, settings, unique, purge_committed
):
    write(tmp_path, "algo/a.md", make_template(name=f"{unique}-a", description=f"{unique} A"))
    write(tmp_path, "b.md", make_essay(name=f"{unique}-b"))
    out, err = io.StringIO(), io.StringIO()
    embedder = FakeEmbedder()

    code = await run_import(tmp_path, settings, embedder=embedder, out=out, err=err)

    assert code == 0, err.getvalue()
    assert await stored_names(settings, unique) == {f"{unique}-a", f"{unique}-b"}
    assert any(f"{unique} A" in call for call in embedder.calls)
    assert f"{unique}-a" in out.getvalue()
    maker = make_sessionmaker(settings.database_url)
    async with maker() as session:
        vectors = await session.scalars(
            select(TemplateEmbedding)
            .join(Template, Template.id == TemplateEmbedding.template_id)
            .where(Template.name == f"{unique}-a")
        )
        assert len(vectors.all()) == 1
    await maker.kw["bind"].dispose()


async def test_any_error_aborts_import_and_lists_every_problem(
    tmp_path, settings, unique, purge_committed
):
    write(tmp_path, "good.md", make_template(name=f"{unique}-good"))
    write(tmp_path, "bad1.md", make_template(name=f"{unique}-b1", difficulty=99))
    write(tmp_path, "bad2.md", make_template(name=f"{unique}-b2", area="space"))
    out, err = io.StringIO(), io.StringIO()

    code = await run_import(tmp_path, settings, embedder=None, out=out, err=err)

    assert code == 1
    assert "bad1.md" in err.getvalue() and "difficulty" in err.getvalue()
    assert "bad2.md" in err.getvalue() and "area" in err.getvalue()
    assert await stored_names(settings, unique) == set()


async def test_embedder_failure_does_not_fail_import(tmp_path, settings, unique, purge_committed):
    write(tmp_path, "a.md", make_template(name=f"{unique}-a"))
    embedder = FakeEmbedder()
    embedder.fail = RuntimeError("эмбеддер недоступен")
    code = await run_import(
        tmp_path, settings, embedder=embedder, out=io.StringIO(), err=io.StringIO()
    )
    assert code == 0
    assert await stored_names(settings, unique) == {f"{unique}-a"}


async def test_missing_or_empty_directory_is_an_error(tmp_path, settings):
    err = io.StringIO()
    assert (
        await run_import(tmp_path / "nope", settings, embedder=None, out=io.StringIO(), err=err)
        == 2
    )
    assert await run_import(tmp_path, settings, embedder=None, out=io.StringIO(), err=err) == 1


def test_import_subcommand_is_wired(tmp_path, settings, unique, purge_committed, monkeypatch):
    write(tmp_path, "a.md", make_template(name=f"{unique}-a"))
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli.main(["import", str(tmp_path), "--no-embeddings"]) == 0
