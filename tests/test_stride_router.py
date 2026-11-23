"""Testes dos endpoints da API STRIDE (/stride e /stride/analyze)."""
from datetime import datetime
from fastapi.testclient import TestClient
import pytest

import main


@pytest.fixture
def client():
    return TestClient(main.create_app())


@pytest.fixture
def sample_dfd():
    return {
        "repository": "test-org/test-repo",
        "elements": [
            {
                "id": "proc_auth",
                "name": "AuthService",
                "type": "process",
                "properties": {
                    "has_authentication": False,
                    "has_authorization": False,
                },
            },
            {
                "id": "ds_users",
                "name": "UsersDatabase",
                "type": "data_store",
                "technology": "postgresql",
                "properties": {
                    "isEncrypted": False,
                    "stores_pii": True,
                },
            },
        ],
        "data_flows": [
            {
                "id": "flow_1",
                "name": "UserCredentials",
                "source": "proc_auth",
                "target": "ds_users",
                "protocol": "tcp",
                "properties": {
                    "isEncrypted": False,
                },
            }
        ],
    }


def test_post_stride_analyze_endpoint(client, sample_dfd):
    response = client.post("/stride/analyze", json=sample_dfd)
    assert response.status_code == 200
    data = response.json()
    assert data["repository"] == "test-org/test-repo"
    assert data["total_findings"] > 0
    assert "summary_by_category" in data
    assert "summary_by_severity" in data
    assert "actionable_vulnerabilities" in data
    assert "architectural_recommendations" in data
    assert "stride_matrix" in data


def test_post_stride_analyze_with_nodes_edges(client):
    dfd_graph = {
        "repository": {"owner": "test-owner", "repo": "test-repo"},
        "nodes": [
            {
                "id": "p1",
                "name": "API Gateway",
                "kind": "process",
                "extra": {"properties": {"has_authentication": True}},
            }
        ],
        "edges": [],
    }
    response = client.post("/stride/analyze", json=dfd_graph)
    assert response.status_code == 200
    data = response.json()
    assert data["repository"] == "test-owner/test-repo"
    assert "summary_by_category" in data


def test_get_stride_invalid_url(client):
    response = client.get("/stride?url=invalid-url")
    assert response.status_code == 400


def test_get_stride_with_cached_dfd(client):
    sample_cached = {
        "schema_version": "1",
        "repository": {"owner": "test-org", "repo": "test-repo"},
        "level": "detail",
        "nodes": [
            {
                "id": "p1",
                "kind": "process",
                "name": "main_app",
                "path": "app/main.py",
                "provenance": {"source": "code", "confidence": "high", "evidence": [{}]},
            }
        ],
        "edges": [],
        "meta": {"producer": "backend-detail", "generated_at": datetime.now().isoformat()},
    }

    key = ("test-org", "test-repo", "directory")
    client.app.state.guard.cache.set(key, sample_cached)

    response = client.get("/stride?url=https://github.com/test-org/test-repo")
    assert response.status_code == 200
    data = response.json()
    assert data["repository"] == "test-org/test-repo"
    assert "total_findings" in data
    assert "findings" in data
