"""Unit tests for the STRIDE Threat Modeling Engine."""

from engine.stride.defensive_stride_engine import StrideEngine


def test_engine_initialization():
    engine = StrideEngine()
    assert len(engine.rules) >= 15
    assert set(engine.categories.keys()) == {
        "Spoofing",
        "Tampering",
        "Repudiation",
        "Information Disclosure",
        "Denial of Service",
        "Elevation of Privilege",
    }
    assert "process" in engine.stride_per_element
    assert "data_flow" in engine.stride_per_element
    assert "data_store" in engine.stride_per_element


def test_defensive_assumptions_on_empty_process():
    engine = StrideEngine()
    # A process with zero security properties defined
    dfd = {
        "repository": "test/empty-process",
        "elements": [
            {
                "id": "proc_1",
                "type": "process",
                "name": "Undocumented API",
            }
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    assert result["total_findings"] > 0

    # Defensively, threats covering all process aspects should fire
    categories = result["summary_by_category"]
    assert categories["Spoofing"] >= 1
    assert categories["Tampering"] >= 1
    assert categories["Repudiation"] >= 1
    assert categories["Denial of Service"] >= 1
    assert categories["Elevation of Privilege"] >= 1

    # Check component in STRIDE matrix
    matrix = result["stride_matrix"]
    assert "proc_1" in matrix
    assert "Spoofing" in matrix["proc_1"]
    assert "Tampering" in matrix["proc_1"]


def test_mitigated_process():
    engine = StrideEngine()
    # A hardened process where security controls are explicitly proven
    dfd = {
        "repository": "test/secure-process",
        "elements": [
            {
                "id": "proc_secure",
                "type": "process",
                "name": "Hardened Gateway",
                "properties": {
                    "provides_authentication": True,
                    "validates_session_tokens": True,
                    "sanitizes_input": True,
                    "checks_input_bounds": True,
                    "generates_audit_logs": True,
                    "has_rate_limiting": True,
                    "runs_as_least_privilege": True,
                    "enforces_authorization": True,
                    "restricts_command_execution": True,
                },
            }
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    # Since all process controls are explicitly True, no process threats should fire
    assert result["total_findings"] == 0
    assert result["stride_matrix"].get("proc_secure") is None


def test_defensive_assumptions_on_dataflow():
    engine = StrideEngine()
    # Unencrypted HTTP flow
    dfd = {
        "repository": "test/unencrypted-flow",
        "elements": [],
        "data_flows": [
            {
                "id": "flow_insecure",
                "name": "Plain HTTP Flow",
                "source": "ext_client",
                "target": "web_proc",
                "protocol": "HTTP",
                "is_encrypted": False,
            }
        ],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]
    threat_ids = [f["threat_id"] for f in findings]

    # Must fire Information Disclosure (STRIDE-I-01 & STRIDE-I-03), Tampering (STRIDE-T-01),
    # Spoofing (STRIDE-S-02) and DoS timeout (STRIDE-D-02)
    assert "STRIDE-I-01" in threat_ids
    assert "STRIDE-I-03" in threat_ids
    assert "STRIDE-T-01" in threat_ids
    assert "STRIDE-S-02" in threat_ids
    assert "STRIDE-D-02" in threat_ids


def test_mitigated_dataflow():
    engine = StrideEngine()
    # Fully secured mTLS flow
    dfd = {
        "repository": "test/secure-flow",
        "elements": [],
        "data_flows": [
            {
                "id": "flow_secure",
                "name": "Secured HTTPS Flow",
                "source": "api_gateway",
                "target": "auth_service",
                "protocol": "HTTPS",
                "is_encrypted": True,
                "authenticates_source": True,
                "defines_timeout": True,
            }
        ],
    }

    result = engine.analyze(dfd)
    threat_ids = [f["threat_id"] for f in result["findings"]]
    assert "STRIDE-I-01" not in threat_ids
    assert "STRIDE-I-03" not in threat_ids
    assert "STRIDE-T-01" not in threat_ids
    assert "STRIDE-S-02" not in threat_ids
    assert "STRIDE-D-02" not in threat_ids


def test_datastore_encryption_at_rest():
    engine = StrideEngine()
    dfd = {
        "repository": "test/datastores",
        "elements": [
            {
                "id": "ds_plain",
                "type": "data_store",
                "name": "Unencrypted DB",
                "encrypted_at_rest": False,
            },
            {
                "id": "ds_vault",
                "type": "data_store",
                "name": "Encrypted DB",
                "encrypted_at_rest": True,
                "enforces_access_control": True,
                "is_tamper_evident": True,
                "masks_sensitive_data": True,
                "enforces_quota_limits": True,
            },
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    matrix = result["stride_matrix"]

    assert "ds_plain" in matrix
    assert "Information Disclosure" in matrix["ds_plain"]
    assert "ds_vault" not in matrix


def test_canonical_full_dfd():
    engine = StrideEngine()
    dfd = {
        "repository": "deti-org/payment-service",
        "elements": [
            {
                "id": "ext_client",
                "type": "external_entity",
                "name": "Customer Client",
                "trust_zone": "internet",
            },
            {
                "id": "proc_auth",
                "type": "process",
                "name": "Auth Gateway",
                "file_path": "auth/session.ts",
                "trust_zone": "internal_backend",
            },
            {
                "id": "ds_users",
                "type": "data_store",
                "name": "Users DB",
                "file_path": "storage/users.db",
                "encrypted_at_rest": False,
                "trust_zone": "internal_backend",
            },
        ],
        "data_flows": [
            {
                "id": "flow_login",
                "name": "User Login Request",
                "source": "ext_client",
                "target": "proc_auth",
                "protocol": "HTTPS",
                "is_encrypted": True,
            },
            {
                "id": "flow_db",
                "name": "Store User Profile",
                "source": "proc_auth",
                "target": "ds_users",
                "protocol": "SQL",
                "is_encrypted": False,
            },
        ],
    }

    result = engine.analyze(dfd)
    assert result["total_findings"] > 0
    assert result["summary_by_severity"]["Critical"] >= 1
    assert result["summary_by_severity"]["High"] >= 1

    # Verify flow_login does NOT trigger STRIDE-I-01 or STRIDE-I-03 because HTTPS & is_encrypted=True
    login_threats = [f["threat_id"] for f in result["findings"] if f["component_id"] == "flow_login"]
    assert "STRIDE-I-01" not in login_threats
    assert "STRIDE-I-03" not in login_threats

    # Verify flow_db triggers STRIDE-I-01 because is_encrypted=False
    db_threats = [f["threat_id"] for f in result["findings"] if f["component_id"] == "flow_db"]
    assert "STRIDE-I-01" in db_threats


def test_dfd_graph_schema_compatibility():
    engine = StrideEngine()
    # Format generated by app/schemas/dfd.py (nodes and edges)
    dfd = {
        "repository": {"owner": "test", "repo": "app"},
        "level": "detail",
        "nodes": [
            {
                "id": "node_proc",
                "kind": "process",
                "name": "API Handler",
                "path": "src/api.py",
                "extra": {
                    "provides_authentication": False,
                },
            }
        ],
        "edges": [
            {
                "id": "edge_1",
                "source": "node_proc",
                "target": "db_node",
                "label": "DB Query",
                "protocol": "HTTP",
                "extra": {
                    "is_encrypted": False,
                },
            }
        ],
    }

    result = engine.analyze(dfd)
    assert result["total_findings"] > 0
    assert "node_proc" in result["stride_matrix"]
    assert "edge_1" in result["stride_matrix"]
