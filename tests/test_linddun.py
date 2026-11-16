"""Testes do motor de privacidade LINDDUN e dos respetivos endpoints da API."""
from datetime import datetime
from fastapi.testclient import TestClient
import pytest

from app.schemas.dfd import (
    DfdEdge,
    DfdGraph,
    DfdNode,
    Evidence,
    GraphMeta,
    Provenance,
    RepositoryRef,
)
from app.services.dfd_to_linddun import map_dfd_to_linddun
from app.services.linddun_catalog import get_categories, get_rules, load_catalog
from app.services.linddun_engine import LinddunEngine
import main


@pytest.fixture
def client():
    return TestClient(main.create_app())


@pytest.fixture
def cw685_dfd():
    """DFD canónico baseado no Relatório CW685 da KU Leuven (Social Network 2.0)."""
    return {
        "repository": "kuleuven-cw685-social-network",
        "elements": [
            {
                "id": "1",
                "name": "User",
                "type": "actor",
                "properties": {"shares_personal_data": True},
            },
            {
                "id": "2",
                "name": "Portal",
                "type": "process",
                "properties": {"isALog": False},
            },
            {
                "id": "3",
                "name": "Service",
                "type": "process",
                "properties": {"isALog": False},
            },
            {
                "id": "4",
                "name": "Social network data",
                "type": "data_store",
                "properties": {
                    "stores_raw_data": True,
                    "contains_pseudo_identifiers": True,
                    "contains_quasi_identifiers": True,
                    "re_identification_risk": True,
                    "isEncrypted": False,
                    "has_retention_policy": False,
                    "provides_user_access": False,
                },
            },
        ],
        "data_flows": [
            {
                "id": "DF1",
                "name": "User data stream (user-portal)",
                "source": "1",
                "target": "2",
                "properties": {
                    "direction": "inbound",
                    "data_type": "direct_identifier",
                    "isEncrypted": False,
                    "is_pseudonymized": False,
                },
            },
            {
                "id": "DF3",
                "name": "Service data stream (portal-service)",
                "source": "2",
                "target": "3",
                "properties": {
                    "direction": "inbound",
                    "data_type": "general_data",
                    "isEncrypted": True,
                },
            },
            {
                "id": "DF4",
                "name": "DB data stream (service-DB)",
                "source": "3",
                "target": "4",
                "properties": {
                    "direction": "inbound",
                    "data_type": "general_data",
                    "isEncrypted": True,
                },
            },
        ],
    }


def test_catalog_loading_and_categories():
    categories = get_categories()
    rules = get_rules()
    assert len(categories) == 7
    assert "Linkability" in categories.values()
    assert "Identifiability" in categories.values()
    assert len(rules) >= 30


def test_cw685_canonical_evaluation(cw685_dfd):
    engine = LinddunEngine()
    report = engine.analyze(cw685_dfd)

    assert report.repository == "kuleuven-cw685-social-network"
    assert report.summary.total_findings > 0
    assert "Linkability" in report.summary.by_category
    assert "Identifiability" in report.summary.by_category

    threats_by_component = {}
    for finding in report.findings:
        threats_by_component.setdefault(finding.component_name, []).append(finding.threat_id)

    # 1. Validação no Data Store (CW685 Página 26: L4, I5, DD4)
    ds_threats = threats_by_component.get("Social network data", [])
    assert "L4" in ds_threats
    assert "I5" in ds_threats
    assert "DD4" in ds_threats

    # 2. Validação no Fluxo de Entrada (L1 / I1)
    flow_threats = threats_by_component.get("User data stream (user-portal)", [])
    assert "L1" in flow_threats
    assert "I1" in flow_threats


def test_map_dfd_graph_to_linddun():
    dfd = DfdGraph(
        schema_version="1",
        repository=RepositoryRef(owner="owner", repo="repo"),
        level="detail",
        nodes=[
            DfdNode(
                id="actor_1",
                kind="external_entity",
                name="Web Browser",
                provenance=Provenance(source="code", confidence="high", evidence=[Evidence()]),
            ),
            DfdNode(
                id="proc_1",
                kind="process",
                name="auth_service",
                path="app/auth.py",
                provenance=Provenance(source="code", confidence="high", evidence=[Evidence()]),
            ),
            DfdNode(
                id="ds_1",
                kind="data_store",
                name="user_db",
                technology="PostgreSQL",
                tags=["database"],
                provenance=Provenance(source="code", confidence="high", evidence=[Evidence()]),
            ),
        ],
        edges=[
            DfdEdge(
                id="edge_1",
                source="actor_1",
                target="proc_1",
                label="POST /login (password, user)",
                protocol="http",
                operations=["write"],
                provenance=Provenance(source="code", confidence="high", evidence=[Evidence()]),
            ),
            DfdEdge(
                id="edge_2",
                source="proc_1",
                target="ds_1",
                label="SELECT users",
                protocol="sql",
                operations=["read"],
                provenance=Provenance(source="code", confidence="high", evidence=[Evidence()]),
            ),
        ],
        meta=GraphMeta(producer="backend-detail", generated_at=datetime.now()),
    )

    mapped = map_dfd_to_linddun(dfd)
    assert mapped["repository"] == "owner/repo"
    assert len(mapped["elements"]) == 3
    assert len(mapped["data_flows"]) == 2

    proc_props = next(e["properties"] for e in mapped["elements"] if e["id"] == "proc_1")
    assert proc_props["storesCredentials"] is True

    flow_props = next(f["properties"] for f in mapped["data_flows"] if f["id"] == "edge_1")
    assert flow_props["publicNetwork"] is True
    assert flow_props["direction"] == "inbound"
    assert flow_props["isEncrypted"] is False
    assert flow_props["data_type"] == "credential"

    engine = LinddunEngine()
    report = engine.analyze(dfd)
    assert report.summary.total_findings > 0


def test_post_linddun_analyze_endpoint(client, cw685_dfd):
    response = client.post("/linddun/analyze", json=cw685_dfd)
    assert response.status_code == 200
    data = response.json()
    assert data["repository"] == "kuleuven-cw685-social-network"
    assert data["summary"]["total_findings"] > 0
    assert len(data["findings"]) == data["summary"]["total_findings"]


def test_get_linddun_invalid_url(client):
    response = client.get("/linddun?url=not_a_valid_url")
    assert response.status_code == 400


def test_get_linddun_with_cached_dfd(client):
    # Simula um DFD em cache no guard da aplicação
    sample_dfd = {
        "schema_version": "1",
        "repository": {"owner": "test-org", "repo": "test-repo"},
        "level": "detail",
        "nodes": [
            {
                "id": "p1",
                "kind": "process",
                "name": "login_controller",
                "path": "controllers/login.py",
                "provenance": {"source": "code", "confidence": "high", "evidence": [{}]},
            }
        ],
        "edges": [],
        "meta": {"producer": "backend-detail", "generated_at": datetime.now().isoformat()},
        "extra": {"flows": []},
    }

    key = ("test-org", "test-repo", "directory")
    client.app.state.guard.cache.set(key, sample_dfd)

    response = client.get("/linddun?url=https://github.com/test-org/test-repo")
    assert response.status_code == 200
    data = response.json()
    assert data["repository"] == "test-org/test-repo"
    assert "summary" in data
    assert "findings" in data
