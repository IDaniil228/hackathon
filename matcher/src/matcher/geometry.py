from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

import numpy as np
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sqlalchemy import text

from matcher.profile_text import render_profile, source_hash


def project_vectors(vectors: Mapping[str, Sequence[float]], query: Sequence[float]) -> dict:
    ids = sorted(vectors)
    if not ids:
        return {"algorithm": "pca", "query": {"x": 0, "y": 0}, "points": []}
    matrix = np.asarray([vectors[key] for key in ids] + [query], dtype=float)
    if matrix.ndim != 2 or not np.isfinite(matrix).all():
        raise ValueError("Некорректные векторы")
    norms = np.linalg.norm(matrix, axis=1)
    if (norms <= 0).any():
        raise ValueError("Нулевой вектор не имеет косинусной близости")
    matrix /= norms[:, None]
    scores = np.clip(matrix[:-1] @ matrix[-1], -1, 1)
    components = min(2, *matrix.shape)
    coordinates = PCA(n_components=components, svd_solver="full").fit_transform(matrix)
    if components == 1:
        coordinates = np.column_stack((coordinates[:, 0], np.zeros(len(matrix))))
    labels = np.full(len(ids), -1, dtype=int)
    if len(ids) >= 10:
        distances = np.clip(1 - matrix[:-1] @ matrix[:-1].T, 0, 2)
        np.fill_diagonal(distances, 0)
        labels = HDBSCAN(
            metric="precomputed", copy=True, min_cluster_size=max(5, int(len(ids) ** 0.5))
        ).fit_predict(distances)
    return {
        "algorithm": "pca",
        "query": {"x": float(coordinates[-1, 0]), "y": float(coordinates[-1, 1])},
        "points": [
            {
                "candidate_id": key,
                "x": float(coordinates[n, 0]),
                "y": float(coordinates[n, 1]),
                "cosine": float(scores[n]),
                "cluster": int(labels[n]),
            }
            for n, key in enumerate(ids)
        ],
    }


async def revisions(session, ids):
    rows = (
        (
            await session.execute(
                text("""
        SELECT c.user_id, c.updated_at, c.current_category_id, c.profile_strength,
          c.profile_refreshed_at, p.updated_at AS privacy_updated,
          p.is_searchable, p.show_about, p.show_experience, p.show_fsp, p.show_github,
          cp.updated_at AS preferences_updated,
          (SELECT max(s.updated_at) FROM profile.candidate_skills s
           WHERE s.candidate_id=c.user_id) AS skills_updated,
          (SELECT count(*) FROM profile.fsp_achievements a
           WHERE a.candidate_id=c.user_id) AS fsp_count,
          (SELECT max(g.indexed_at) FROM profile.github_repos g
           WHERE g.candidate_id=c.user_id) AS github_updated
        FROM profile.candidates c
        JOIN profile.candidate_privacy p ON p.candidate_id=c.user_id
        JOIN profile.candidate_preferences cp ON cp.candidate_id=c.user_id
        WHERE c.user_id=ANY(:ids)
    """),
                {"ids": list(ids)},
            )
        )
        .mappings()
        .all()
    )
    return {
        str(row["user_id"]): hashlib.sha256(
            json.dumps(dict(row), sort_keys=True, default=str).encode()
        ).hexdigest()
        for row in rows
    }


async def capture(sessionmaker, run_id, model, query, cards, before, exploration):
    async with sessionmaker() as session, session.begin():
        for candidate_id in sorted(cards):
            await session.execute(text("SELECT profile.lock_candidate(:id)"), {"id": candidate_id})
        after = await revisions(session, list(cards))
        rows = (
            (
                await session.execute(
                    text("""
            SELECT candidate_id, source_hash, embedding::text AS embedding
            FROM match.candidate_embeddings WHERE model=:model AND candidate_id=ANY(:ids)
        """),
                    {"model": model, "ids": list(cards)},
                )
            )
            .mappings()
            .all()
        )
        candidates = {}
        for row in rows:
            key = str(row["candidate_id"])
            card = cards[row["candidate_id"]]
            if before.get(key) != after.get(key) or row["source_hash"] != source_hash(
                render_profile(card)
            ):
                continue
            candidates[key] = {
                "vector": json.loads(row["embedding"]),
                "revision": after[key],
                "requires": [
                    field
                    for field, used in {
                        "about": bool(card.about),
                        "experience_months": card.experience_months is not None,
                        "github": bool(card.github),
                        "fsp_achievements": bool(card.fsp),
                        "private_preferences": bool(card.positive_prompt),
                    }.items()
                    if used
                ],
            }
        payload = {"query": query, "candidates": candidates, "exploration": exploration}
        await session.execute(
            text("""
            INSERT INTO match.run_map_snapshots(run_id,payload) VALUES (:id,CAST(:payload AS jsonb))
            ON CONFLICT (run_id) DO NOTHING
        """),
            {"id": run_id, "payload": json.dumps(payload)},
        )


async def snapshot(session, run_id):
    return await session.scalar(
        text("SELECT payload FROM match.run_map_snapshots WHERE run_id=:id"), {"id": run_id}
    )


async def forget_before(session, candidate_id, before):
    await session.execute(
        text("DELETE FROM match.candidate_embeddings WHERE candidate_id=:id AND built_at<=:before"),
        {"id": candidate_id, "before": before},
    )
    await session.execute(
        text(
            "UPDATE match.run_map_snapshots "
            "SET payload=jsonb_set(payload,'{candidates}',"
            "(payload->'candidates')-CAST(:key AS text)) "
            "WHERE created_at<=:before"
        ),
        {"key": str(candidate_id), "before": before},
    )
