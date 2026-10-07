import logging

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from taskgen.bank.embeddings import refresh_embeddings, refresh_embeddings_safely
from taskgen.bank.importer import TemplateDocument, import_documents
from taskgen.bank.parser import content_hash
from taskgen.db import Template, TemplateEmbedding
from tests.bank.conftest import FakeEmbedder
from tests.bank.factory import local_check, make_template

pytestmark = pytest.mark.integration


async def put(session, name: str, description: str, **fields):
    text = make_template(name=name, description=description, **fields)
    doc = TemplateDocument("t.md", text)
    [outcome] = await import_documents(session, [doc], check=local_check())
    return outcome


def embedded_texts(embedder: FakeEmbedder) -> list[str]:
    return [text for call in embedder.calls for text in call]


async def stored(session, template_id, model="fake"):
    return await session.get(TemplateEmbedding, (template_id, model))


async def test_missing_embeddings_are_computed_in_batches(db_session, unique):
    a = await put(db_session, f"{unique}-a", f"{unique} описание A")
    b = await put(db_session, f"{unique}-b", f"{unique} описание B")
    embedder = FakeEmbedder(
        {f"{unique} описание A": [0.0, 1.0], f"{unique} описание B": [1.0, 0.0]}
    )
    await refresh_embeddings(db_session, embedder, batch_size=1)
    assert {f"{unique} описание A", f"{unique} описание B"} <= set(embedded_texts(embedder))
    assert all(len(call) <= 1 for call in embedder.calls)
    row = await stored(db_session, a.template_id)
    assert list(row.embedding) == [0.0, 1.0]
    assert row.content_hash == content_hash(f"{unique} описание A")
    assert list((await stored(db_session, b.template_id)).embedding) == [1.0, 0.0]


async def test_fresh_embeddings_are_not_recomputed(db_session, unique):
    await put(db_session, f"{unique}-a", f"{unique} описание A")
    await refresh_embeddings(db_session, FakeEmbedder())
    again = FakeEmbedder()
    await refresh_embeddings(db_session, again)
    assert f"{unique} описание A" not in embedded_texts(again)


async def test_stale_hash_and_other_model_trigger_recompute(db_session, unique):
    a = await put(db_session, f"{unique}-a", f"{unique} описание A")
    await refresh_embeddings(db_session, FakeEmbedder())
    await db_session.execute(
        update(TemplateEmbedding)
        .where(TemplateEmbedding.template_id == a.template_id)
        .values(content_hash="0" * 64)
    )
    stale = FakeEmbedder({f"{unique} описание A": [0.5, 0.5]})
    await refresh_embeddings(db_session, stale)
    assert f"{unique} описание A" in embedded_texts(stale)
    assert list((await stored(db_session, a.template_id)).embedding) == [0.5, 0.5]

    other = FakeEmbedder(model="other")
    await refresh_embeddings(db_session, other)
    assert f"{unique} описание A" in embedded_texts(other)
    assert await stored(db_session, a.template_id, "other") is not None


async def test_old_versions_are_not_embedded(db_session, unique):
    old = await put(db_session, f"{unique}-a", f"{unique} старое")
    await put(db_session, f"{unique}-a", f"{unique} новое")
    embedder = FakeEmbedder()
    await refresh_embeddings(db_session, embedder)
    assert f"{unique} старое" not in embedded_texts(embedder)
    assert f"{unique} новое" in embedded_texts(embedder)
    assert await stored(db_session, old.template_id) is None


async def test_wrong_vector_count_is_an_error(db_session, unique):
    await put(db_session, f"{unique}-a", f"{unique} описание A")

    class Short(FakeEmbedder):
        async def embed(self, texts):
            return []

    with pytest.raises(ValueError):
        await refresh_embeddings(db_session, Short())


async def test_embedder_failure_is_logged_and_keeps_templates(db_connection, unique, caplog):
    maker = async_sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    async with maker() as session:
        a = await put(session, f"{unique}-a", f"{unique} описание A")
        await session.commit()
    embedder = FakeEmbedder()
    embedder.fail = RuntimeError("эмбеддер недоступен")
    with caplog.at_level(logging.ERROR, logger="taskgen.bank.embeddings"):
        written = await refresh_embeddings_safely(maker, embedder)
    assert written == 0
    assert "эмбеддер недоступен" in caplog.text
    async with maker() as session:
        assert await session.get(Template, a.template_id) is not None
        rows = await session.scalars(
            select(TemplateEmbedding).where(TemplateEmbedding.template_id == a.template_id)
        )
        assert rows.all() == []


async def test_safe_refresh_keeps_batches_written_before_failure(db_connection, unique):
    maker = async_sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    async with maker() as session:
        await put(session, f"{unique}-a", f"{unique} описание A")
        await put(session, f"{unique}-b", f"{unique} описание B")
        await session.commit()

    class FailsSecondCall(FakeEmbedder):
        async def embed(self, texts):
            if self.calls:
                self.calls.append(list(texts))
                raise RuntimeError("сбой на втором батче")
            return await super().embed(texts)

    embedder = FailsSecondCall()
    written = await refresh_embeddings_safely(maker, embedder, batch_size=1)
    assert written == 1
    [first_text] = embedder.calls[0]
    [failed_text] = embedder.calls[1]
    async with maker() as session:
        descriptions = set(
            await session.scalars(
                select(Template.description)
                .join(TemplateEmbedding, TemplateEmbedding.template_id == Template.id)
                .where(TemplateEmbedding.model == "fake")
            )
        )
    assert first_text in descriptions
    assert failed_text not in descriptions
