import numpy as np
import pytest

from matcher.geometry import project_vectors


def test_real_cosine_and_deterministic_projection():
    vectors = {"same": [1.0, 0, 0], "orthogonal": [0, 1.0, 0], "opposite": [-1.0, 0, 0]}
    result = project_vectors(vectors, [1.0, 0, 0])
    scores = {row["candidate_id"]: row["cosine"] for row in result["points"]}
    assert scores == {"same": 1, "orthogonal": 0, "opposite": -1}
    assert result == project_vectors(dict(reversed(list(vectors.items()))), [1.0, 0, 0])


def test_exclusion_recomputes_geometry_without_hidden_vector():
    rng = np.random.default_rng(8)
    vectors = {str(i): rng.normal(size=16).tolist() for i in range(30)}
    visible = {key: value for key, value in vectors.items() if key != "0"}
    expected = project_vectors(visible, vectors["1"])
    assert {row["candidate_id"] for row in expected["points"]} == set(visible)
    assert expected != project_vectors(vectors, vectors["1"])


def test_zero_vector_cannot_create_a_semantic_point():
    with pytest.raises(ValueError):
        project_vectors({"empty": [0, 0]}, [1, 0])
