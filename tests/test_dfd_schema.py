import io
import tarfile

import pytest
from fastapi.testclient import TestClient

from app.routers import dfd as dfd_router
from app.schemas.dfd import DfdGraph
from main import app

FILES= {
    "app/api/users.py": 'from fastapi import APIRouter\nfrom app.services import users as svc\nrouter = APIRouter()\n@router.get("/users")\ndef list_users():\n    return svc.load_all()\n',
    "app/services/users.py": 'from app.db import repo\nimport requests\nrequests.get("https://api.stripe.com/v1")\ndef load_all():\n    return repo.fetch_users()\n',
    "app/db/repo.py": 'import psycopg2\nimport celery\nconn = psycopg2.connect()\ndef fetch_users():\n    cur = conn.cursor()\n    cur.execute("select 1")\n    return cur.fetchall()\n',
    "main.py": "print('hi')\n",
}


def archive_of(files: dict[str, str]) -> io.BytesIO:
    buffer= io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, text in files.items():
            data= text.encode()
            info= tarfile.TarInfo(f"owner-repo-abc123/{path}")
            info.size= len(data)
            tar.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    return buffer


@pytest.fixture
def get_dfd(monkeypatch):
    async def fake_download(owner, repo):
        return archive_of(FILES)

    monkeypatch.setattr(dfd_router, "download_archive", fake_download)
    client= TestClient(app)
    return lambda group_by="directory": client.get("/dfd", params={"url": "https://github.com/owner/repo", "group_by": group_by})


def test_the_endpoint_returns_the_shared_schema(get_dfd):
    response= get_dfd()
    assert response.status_code == 200
    body= response.json()
    DfdGraph.model_validate(body)
    assert (body["schema_version"], body["level"], body["meta"]["producer"]) == ("1", "detail", "backend-detail")
    assert body["repository"] == {"owner": "owner", "repo": "repo", "ref": None}
    assert body["meta"]["stats"]["files_parsed"] == 4


def test_only_the_three_dfd_kinds_are_used_and_the_old_ones_are_gone(get_dfd):
    body= get_dfd().json()
    assert {n["kind"] for n in body["nodes"]} <= {"external_entity", "process", "data_store"}
    ids= {n["id"] for n in body["nodes"]}
    assert not any(i.startswith(("entity:", "datastore:", "external:", "queue:")) for i in ids)
    assert {"process:app/api", "process:app/services", "process:app/db", "external_entity:client",
            "external_entity:api.stripe.com", "data_store:postgresql"} <= ids


def test_a_queue_is_a_data_store_with_the_queue_tag(get_dfd):
    queue= next(n for n in get_dfd().json()["nodes"] if n["technology"] == "message-queue")
    assert (queue["kind"], queue["tags"], queue["id"]) == ("data_store", ["queue"], "data_store:message-queue")


def test_catalog_services_have_a_technology_and_hosts_found_in_urls_do_not(get_dfd):
    nodes= {n["id"]: n for n in get_dfd().json()["nodes"]}
    assert nodes["data_store:postgresql"]["technology"] == "postgresql"
    assert nodes["external_entity:http-api"]["technology"] == "http-api"
    assert nodes["external_entity:api.stripe.com"]["technology"] is None
    assert nodes["external_entity:api.stripe.com"]["tags"] == ["third-party"]
    assert nodes["external_entity:client"]["tags"] == ["client"]


def test_every_edge_endpoint_and_flow_step_is_a_node(get_dfd):
    body= get_dfd().json()
    ids= {n["id"] for n in body["nodes"]}
    assert len(ids) == len(body["nodes"])
    assert all(e["source"] in ids and e["target"] in ids for e in body["edges"])
    assert len({e["id"] for e in body["edges"]}) == len(body["edges"])
    flows= body["extra"]["flows"]
    assert flows
    for flow in flows:
        assert flow["process"] in ids
        for resource in flow["resources"]:
            assert resource["id"] in ids and all(step in ids for step in resource["path"])


def test_no_presentation_fields_leak_into_the_graph(get_dfd):
    body= get_dfd().json()
    assert all({"position", "type", "data", "x", "y"}.isdisjoint(n) for n in body["nodes"])
    assert all({"data"}.isdisjoint(e) for e in body["edges"])


def test_every_item_has_provenance_and_processes_point_at_real_files(get_dfd):
    body= get_dfd().json()
    for item in [*body["nodes"], *body["edges"]]:
        assert item["provenance"]["source"] == "code"
        assert item["provenance"]["evidence"]
    process= next(n for n in body["nodes"] if n["id"] == "process:app/db")
    assert process["provenance"]["confidence"] == "high"
    assert process["provenance"]["evidence"] == [{"file": "app/db/repo.py", "line": None, "span": None, "note": None}]
    store= next(n for n in body["nodes"] if n["id"] == "data_store:postgresql")
    assert store["provenance"]["confidence"] == "medium"
    assert store["provenance"]["evidence"][0]["note"] == "seen via psycopg2"


def test_the_repository_root_is_the_dot_path(get_dfd):
    root= next(n for n in get_dfd().json()["nodes"] if n["kind"] == "process" and n["path"] == ".")
    assert (root["id"], root["name"]) == ("process:.", "(root)")


def test_grouping_by_file_adds_the_importing_file_as_edge_evidence(get_dfd):
    body= get_dfd("file").json()
    edge= next(e for e in body["edges"] if e["source"] == "process:app/api/users.py" and e["target"] == "process:app/services/users.py")
    assert edge["provenance"]["evidence"][0]["file"] == "app/api/users.py"


def test_a_missing_repository_still_maps_to_a_clean_error(monkeypatch):
    from app.services.github import GitHubError

    async def missing(owner, repo):
        raise GitHubError(404, "Repositório não encontrado ou privado")

    monkeypatch.setattr(dfd_router, "download_archive", missing)
    response= TestClient(app).get("/dfd", params={"url": "https://github.com/owner/repo"})
    assert (response.status_code, response.json()["detail"]) == (404, "Repositório não encontrado ou privado")
