import re

import pytest

from tests.bank.conftest import FakeEmbedder
from tests.bank.factory import make_essay, make_template
from tests.conftest import ADMIN_KEY

pytestmark = pytest.mark.integration

ADMIN = {"Authorization": f"Bearer {ADMIN_KEY}"}
MARKDOWN = {**ADMIN, "Content-Type": "text/markdown; charset=utf-8"}


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def api(client, embedder, purge_committed):
    """Клиент с подделкой эмбеддера; шаблоны теста удаляются после него."""
    client.app.state.embedder = embedder
    return client


@pytest.fixture
def client_headers(api) -> dict[str, str]:
    issued = api.post("/keys", json={"name": "backend", "role": "client"}, headers=ADMIN)
    assert issued.status_code == 201
    return {"Authorization": f"Bearer {issued.json()['key']}"}


def post_md(api, text: str, headers=MARKDOWN):
    return api.post("/templates", content=text.encode(), headers=headers)


def test_admin_uploads_markdown_and_versions_follow_content(api, unique):
    text = make_template(name=f"{unique}-a")
    created = post_md(api, text)
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "created" and created.json()["version"] == 1

    same = post_md(api, text)
    assert same.json()["status"] == "unchanged" and same.json()["version"] == 1

    changed = post_md(api, make_template(name=f"{unique}-a", difficulty=6))
    assert changed.json()["status"] == "updated" and changed.json()["version"] == 2


def test_admin_uploads_json_body(api, unique):
    response = api.post("/templates", json={"body": make_essay(name=f"{unique}-e")}, headers=ADMIN)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == f"{unique}-e"


def test_upload_requires_admin(api, client_headers, unique):
    text = make_template(name=f"{unique}-a")
    assert api.post("/templates", content=text.encode()).status_code == 401
    as_client = post_md(api, text, {**client_headers, "Content-Type": "text/markdown"})
    assert as_client.status_code == 403


def test_invalid_template_is_rejected_with_all_problems(api, unique):
    text = make_template(name=f"{unique}-a", difficulty=99, area="space")
    response = post_md(api, text)
    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert {"difficulty"} <= {e["location"] for e in errors}
    assert api.get(f"/templates/{unique}-a", headers=ADMIN).status_code == 404


def test_reference_errors_are_reported(api, unique):
    response = post_md(api, make_template(name=f"{unique}-a", area="space"))
    assert response.status_code == 422
    assert "area" in {e["location"] for e in response.json()["detail"]["errors"]}


def test_unsupported_content_type_is_rejected(api, unique):
    response = api.post(
        "/templates",
        content=make_template(name=f"{unique}-a").encode(),
        headers={**ADMIN, "Content-Type": "application/xml"},
    )
    assert response.status_code == 415


def test_bulk_upload_of_files(api, unique):
    files = [
        ("files", ("a.md", make_template(name=f"{unique}-a").encode(), "text/markdown")),
        ("files", ("b.md", make_essay(name=f"{unique}-b").encode(), "text/markdown")),
    ]
    response = api.post("/templates/bulk", files=files, headers=ADMIN)
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert {(r["source"], r["status"]) for r in results} == {
        ("a.md", "created"),
        ("b.md", "created"),
    }


def test_bulk_upload_as_json(api, unique):
    payload = {"templates": [{"source": "x.md", "body": make_template(name=f"{unique}-x")}]}
    response = api.post("/templates/bulk", json=payload, headers=ADMIN)
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["source"] == "x.md"


def test_bulk_with_one_bad_file_writes_nothing(api, unique):
    files = [
        ("files", ("a.md", make_template(name=f"{unique}-a").encode(), "text/markdown")),
        (
            "files",
            ("b.md", make_template(name=f"{unique}-b", grade="guru").encode(), "text/markdown"),
        ),
    ]
    response = api.post("/templates/bulk", files=files, headers=ADMIN)
    assert response.status_code == 422
    assert {e["source"] for e in response.json()["detail"]["errors"]} == {"b.md"}
    assert api.get(f"/templates/{unique}-a", headers=ADMIN).status_code == 404


def test_bulk_requires_admin(api, client_headers):
    response = api.post("/templates/bulk", json={"templates": []}, headers=client_headers)
    assert response.status_code == 403


def test_client_lists_current_metadata_without_body(api, client_headers, unique):
    post_md(api, make_template(name=f"{unique}-a", grade="junior", skills=["python", "sorting"]))
    post_md(api, make_template(name=f"{unique}-a", grade="middle", skills=["python", "sorting"]))
    post_md(api, make_template(name=f"{unique}-b", grade="middle", skills=["python"]))
    post_md(api, make_essay(name=f"{unique}-c", grade="middle", skills=["python"]))

    def listed(**query) -> dict[str, dict]:
        response = api.get("/templates", params=query, headers=client_headers)
        assert response.status_code == 200, response.text
        return {row["name"]: row for row in response.json() if row["name"].startswith(unique)}

    everything = listed(area="algo")
    assert set(everything) == {f"{unique}-a", f"{unique}-b", f"{unique}-c"}
    assert everything[f"{unique}-a"]["version"] == 2
    for row in everything.values():
        assert "body" not in row
        assert "def two_sum" not in str(row)

    assert set(listed(area="algo", grade="middle", skill="sorting")) == {f"{unique}-a"}
    assert set(listed(area="algo", kind="essay")) == {f"{unique}-c"}
    assert listed(area="ml") == {}


def test_get_by_name_hides_body_from_client(api, client_headers, unique):
    text = make_template(name=f"{unique}-a")
    post_md(api, text)
    as_client = api.get(f"/templates/{unique}-a", headers=client_headers)
    assert as_client.status_code == 200
    assert "body" not in as_client.json()
    assert "def two_sum" not in as_client.text
    as_admin = api.get(f"/templates/{unique}-a", headers=ADMIN)
    assert as_admin.json()["body"] == text


def test_get_unknown_template_is_404(api, client_headers, unique):
    assert api.get(f"/templates/{unique}-zzz", headers=client_headers).status_code == 404


def test_import_triggers_embedding_refresh(api, embedder, unique):
    post_md(api, make_template(name=f"{unique}-a", description=f"{unique} описание"))
    assert any(f"{unique} описание" in call for call in embedder.calls)


def test_embedder_failure_does_not_fail_import(api, embedder, unique):
    embedder.fail = RuntimeError("эмбеддер недоступен")
    response = post_md(api, make_template(name=f"{unique}-a"))
    assert response.status_code == 200
    assert api.get(f"/templates/{unique}-a", headers=ADMIN).status_code == 200


def test_template_routes_are_documented_in_russian(api):
    paths = api.get("/openapi.json").json()["paths"]
    cyrillic = re.compile(r"[а-яё]", re.IGNORECASE)
    for path in ("/templates", "/templates/bulk", "/templates/{name}"):
        for operation in paths[path].values():
            assert cyrillic.search(operation["summary"]), (path, operation["summary"])
            assert cyrillic.search(operation.get("description", "")), path
    upload = paths["/templates"]["post"]["requestBody"]["content"]
    assert {"text/markdown", "application/json"} <= set(upload)
