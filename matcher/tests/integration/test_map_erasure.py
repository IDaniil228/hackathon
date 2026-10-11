import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from matcher.geometry import forget_before


async def test_erasure_keeps_new_authorized_vectors(owner_session):
    candidate = UUID("11111111-0000-4000-8000-000000000001")
    run = UUID("bbbbbbbb-0000-4000-8000-000000000001")
    cutoff = datetime.now(UTC)
    payload = {"query": [1, 0], "candidates": {str(candidate): {"vector": [1, 0]}}}
    await owner_session.execute(
        text(
            "INSERT INTO match.run_map_snapshots(run_id,payload,created_at) "
            "VALUES (:run,CAST(:payload AS jsonb),:date)"
        ),
        {"run": run, "payload": json.dumps(payload), "date": cutoff - timedelta(days=1)},
    )
    for model, date in (
        ("erasure-old", cutoff - timedelta(days=1)),
        ("erasure-new", cutoff + timedelta(days=1)),
    ):
        await owner_session.execute(
            text(
                "INSERT INTO match.candidate_embeddings"
                "(candidate_id,model,embedding,source_hash,built_at) "
                "VALUES (:id,:model,CAST(:vector AS vector),'synthetic',:date)"
            ),
            {"id": candidate, "model": model, "vector": json.dumps([1] + [0] * 1023), "date": date},
        )
    await forget_before(owner_session, candidate, cutoff)
    result = await owner_session.scalar(
        text("SELECT payload FROM match.run_map_snapshots WHERE run_id=:run"), {"run": run}
    )
    assert str(candidate) not in result["candidates"]
    models = (
        (
            await owner_session.execute(
                text(
                    "SELECT model FROM match.candidate_embeddings "
                    "WHERE candidate_id=:id AND model LIKE 'erasure-%'"
                ),
                {"id": candidate},
            )
        )
        .scalars()
        .all()
    )
    assert models == ["erasure-new"]
