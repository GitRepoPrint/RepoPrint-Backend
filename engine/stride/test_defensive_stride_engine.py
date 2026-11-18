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
                    "validates_redirect_targets": True,
                    "masks_error_details": True,
                    "manages_secrets_securely": True,
                    "validates_outgoing_urls": True,
                    "validates_resource_ownership": True,
                    "enforces_strict_cors": True,
                    "uses_safe_deserializers": True,
                    "verifies_payload_signature": True,
                    "filters_assignable_properties": True,
                    "sanitizes_file_paths": True,
                    "validates_file_types": True,
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
                "has_backup_and_replication": True,
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


def test_trust_boundary_crossing_discrimination():
    """Verify that STRIDE-T-05 triggers ONLY when crossing trust boundaries without encryption."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/trust-boundaries",
        "elements": [],
        "data_flows": [
            {
                "id": "flow_tb_insecure",
                "name": "Public API to Internal Worker",
                "crosses_trust_boundary": True,
                "is_encrypted": False,
            },
            {
                "id": "flow_tb_secure",
                "name": "Secured Public to Internal TLS",
                "crosses_trust_boundary": True,
                "is_encrypted": True,
                "authenticates_source": True,
                "defines_timeout": True,
            },
            {
                "id": "flow_internal_insecure",
                "name": "Internal Service-to-Service Flow",
                "crosses_trust_boundary": False,
                "is_encrypted": False,
            },
        ],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]

    # 1. flow_tb_insecure: must fire STRIDE-T-05 (Critical) AND STRIDE-T-01 (High)
    tb_insecure_threats = [f["threat_id"] for f in findings if f["component_id"] == "flow_tb_insecure"]
    assert "STRIDE-T-05" in tb_insecure_threats
    assert "STRIDE-T-01" in tb_insecure_threats
    t05_finding = next(f for f in findings if f["component_id"] == "flow_tb_insecure" and f["threat_id"] == "STRIDE-T-05")
    assert t05_finding["severity"] == "Critical"

    # 2. flow_tb_secure: must NOT fire STRIDE-T-05 or STRIDE-T-01
    tb_secure_threats = [f["threat_id"] for f in findings if f["component_id"] == "flow_tb_secure"]
    assert "STRIDE-T-05" not in tb_secure_threats
    assert "STRIDE-T-01" not in tb_secure_threats

    # 3. flow_internal_insecure: unencrypted, so fires STRIDE-T-01, but MUST NOT fire STRIDE-T-05 (no trust boundary)
    internal_threats = [f["threat_id"] for f in findings if f["component_id"] == "flow_internal_insecure"]
    assert "STRIDE-T-01" in internal_threats
    assert "STRIDE-T-05" not in internal_threats


def test_external_entity_rules_and_mitigation():
    """Verify that external entities are evaluated for Spoofing (S-04) and Repudiation (R-03)."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/external-entities",
        "elements": [
            {
                "id": "ext_untrusted_client",
                "type": "external_entity",
                "name": "Anonymous Web Visitor",
                "is_trusted_party": False,
                "provides_authentication": False,
            },
            {
                "id": "ext_trusted_partner",
                "type": "external_entity",
                "name": "Verified Banking Partner API",
                "is_trusted_party": True,
                "provides_authentication": True,
            },
            {
                "id": "ext_semi_trusted",
                "type": "external_entity",
                "name": "Partner without Auth Credentials",
                "is_trusted_party": True,
                "provides_authentication": False,
            },
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]

    # 1. Untrusted client must trigger BOTH S-04 (Spoofing) and R-03 (Repudiation)
    untrusted_threats = [f["threat_id"] for f in findings if f["component_id"] == "ext_untrusted_client"]
    assert "STRIDE-S-04" in untrusted_threats
    assert "STRIDE-R-03" in untrusted_threats

    # 2. Hardened trusted partner must trigger NEITHER
    partner_threats = [f["threat_id"] for f in findings if f["component_id"] == "ext_trusted_partner"]
    assert "STRIDE-S-04" not in partner_threats
    assert "STRIDE-R-03" not in partner_threats

    # 3. Semi-trusted partner: Trusted party, so NO S-04, but lacks auth, so MUST fire R-03
    semi_threats = [f["threat_id"] for f in findings if f["component_id"] == "ext_semi_trusted"]
    assert "STRIDE-S-04" not in semi_threats
    assert "STRIDE-R-03" in semi_threats


def test_bola_idor_elevation_of_privilege_discrimination():
    """Verify that BOLA (STRIDE-E-04) is caught even when generic RBAC (STRIDE-E-02) is enabled."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/bola-idor",
        "elements": [
            {
                "id": "proc_rbac_only",
                "type": "process",
                "name": "Orders API with Role Check but No Ownership Validation",
                "properties": {
                    "enforces_authorization": True,  # Has RBAC
                    "validates_resource_ownership": False,  # Missing BOLA protection
                },
            },
            {
                "id": "proc_secure_bola",
                "type": "process",
                "name": "Invoices API with Strict Object Ownership",
                "properties": {
                    "enforces_authorization": True,
                    "validates_resource_ownership": True,
                },
            },
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]

    # proc_rbac_only: STRIDE-E-02 (generic RBAC) is mitigated, but STRIDE-E-04 (BOLA) MUST trigger
    rbac_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_rbac_only"]
    assert "STRIDE-E-02" not in rbac_threats
    assert "STRIDE-E-04" in rbac_threats
    bola_finding = next(f for f in findings if f["component_id"] == "proc_rbac_only" and f["threat_id"] == "STRIDE-E-04")
    assert bola_finding["severity"] == "Critical"

    # proc_secure_bola: Both RBAC and BOLA are cleanly mitigated
    secure_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_secure_bola"]
    assert "STRIDE-E-02" not in secure_threats
    assert "STRIDE-E-04" not in secure_threats


def test_ssrf_and_error_handling_information_disclosure():
    """Verify detection and mitigation of SSRF, secrets exposure, and stack trace leaks."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/app-threats",
        "elements": [
            {
                "id": "proc_vulnerable_proxy",
                "type": "process",
                "name": "Webhook Dispatcher",
                "properties": {
                    "validates_outgoing_urls": False,
                    "masks_error_details": False,
                    "manages_secrets_securely": False,
                    "validates_redirect_targets": False,
                },
            },
            {
                "id": "proc_hardened_proxy",
                "type": "process",
                "name": "Hardened Dispatcher",
                "properties": {
                    "validates_outgoing_urls": True,
                    "masks_error_details": True,
                    "manages_secrets_securely": True,
                    "validates_redirect_targets": True,
                },
            },
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]

    vuln_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_vulnerable_proxy"]
    assert "STRIDE-I-07" in vuln_threats  # SSRF
    assert "STRIDE-I-05" in vuln_threats  # Stack trace
    assert "STRIDE-I-06" in vuln_threats  # Plaintext secrets
    assert "STRIDE-S-05" in vuln_threats  # Open redirect

    hardened_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_hardened_proxy"]
    assert "STRIDE-I-07" not in hardened_threats
    assert "STRIDE-I-05" not in hardened_threats
    assert "STRIDE-I-06" not in hardened_threats
    assert "STRIDE-S-05" not in hardened_threats


def test_datastore_backup_and_resilience_dos():
    """Verify that missing backups trigger STRIDE-D-04 independently of disk quotas."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/backup-resilience",
        "elements": [
            {
                "id": "ds_no_backup",
                "type": "data_store",
                "name": "Production DB without Backup",
                "properties": {
                    "enforces_quota_limits": True,  # Quotas OK
                    "has_backup_and_replication": False,  # Missing backup
                },
            },
            {
                "id": "ds_with_backup",
                "type": "data_store",
                "name": "Disaster Recoverable DB",
                "properties": {
                    "enforces_quota_limits": True,
                    "has_backup_and_replication": True,
                },
            },
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]

    no_backup_threats = [f["threat_id"] for f in findings if f["component_id"] == "ds_no_backup"]
    assert "STRIDE-D-03" not in no_backup_threats  # Quota is OK, so D-03 doesn't fire
    assert "STRIDE-D-04" in no_backup_threats      # Missing backup fires D-04
    d04_finding = next(f for f in findings if f["component_id"] == "ds_no_backup" and f["threat_id"] == "STRIDE-D-04")
    assert d04_finding["severity"] == "High"

    with_backup_threats = [f["threat_id"] for f in findings if f["component_id"] == "ds_with_backup"]
    assert "STRIDE-D-04" not in with_backup_threats


def test_catalog_schema_and_rules_integrity():
    """Verify that all rules in the catalog adhere strictly to schema and semantic consistency."""
    engine = StrideEngine()
    valid_categories = {"Spoofing", "Tampering", "Repudiation", "Information Disclosure", "Denial of Service", "Elevation of Privilege"}
    valid_severities = {"Critical", "High", "Medium", "Low"}
    valid_element_types = {"process", "data_store", "data_flow", "external_entity"}

    assert len(engine.rules) >= 34, f"Expected at least 34 rules, found {len(engine.rules)}"

    seen_ids = set()
    for rule in engine.rules:
        rid = rule.get("id")
        assert rid, "Rule must have a non-empty ID"
        assert rid not in seen_ids, f"Duplicate rule ID found: {rid}"
        seen_ids.add(rid)

        cat = rule.get("category")
        assert cat in valid_categories, f"Invalid category '{cat}' in rule {rid}"
        assert rule.get("stride_letter") == cat[0], f"Stride letter mismatch for rule {rid}"

        sev = rule.get("severity")
        assert sev in valid_severities, f"Invalid severity '{sev}' in rule {rid}"

        target = rule.get("applies_to")
        assert target in valid_element_types, f"Invalid applies_to '{target}' in rule {rid}"

        conditions = rule.get("conditions")
        assert isinstance(conditions, dict) and conditions, f"Rule {rid} must have non-empty conditions"

        # Check that condition keys exist in defined dfd_properties or are standard DFD attributes
        for cond_key in conditions.keys():
            assert cond_key in engine.dfd_properties or cond_key in ("protocol", "trust_zone"), (
                f"Rule {rid} uses undeclared property '{cond_key}' not present in dfd_properties!"
            )

        assert rule.get("title"), f"Rule {rid} missing title"
        assert rule.get("description"), f"Rule {rid} missing description"
        assert rule.get("mitigation"), f"Rule {rid} missing mitigation"
        assert len(rule.get("references", [])) > 0, f"Rule {rid} must have at least one reference"


def test_cors_misconfiguration_rule():
    """Verify that overly permissive CORS configuration triggers STRIDE-S-06."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/cors-policy",
        "elements": [
            {
                "id": "proc_wildcard_cors",
                "type": "process",
                "name": "API with Wildcard CORS and Credentials",
                "properties": {
                    "enforces_strict_cors": False,
                },
            },
            {
                "id": "proc_strict_cors",
                "type": "process",
                "name": "API with Whitelisted Domain CORS",
                "properties": {
                    "enforces_strict_cors": True,
                },
            },
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]

    vuln_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_wildcard_cors"]
    assert "STRIDE-S-06" in vuln_threats
    s06_finding = next(f for f in findings if f["component_id"] == "proc_wildcard_cors" and f["threat_id"] == "STRIDE-S-06")
    assert s06_finding["severity"] == "High"

    mitigated_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_strict_cors"]
    assert "STRIDE-S-06" not in mitigated_threats


def test_insecure_deserialization_and_webhook_tampering():
    """Verify detection of unsafe deserializers (STRIDE-T-06) and unverified webhooks (STRIDE-T-07)."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/tampering-risks",
        "elements": [
            {
                "id": "proc_unsafe_worker",
                "type": "process",
                "name": "Background Job Consumer",
                "properties": {
                    "uses_safe_deserializers": False,
                    "verifies_payload_signature": False,
                },
            },
            {
                "id": "proc_secure_worker",
                "type": "process",
                "name": "Hardened Webhook Handler",
                "properties": {
                    "uses_safe_deserializers": True,
                    "verifies_payload_signature": True,
                },
            },
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]

    unsafe_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_unsafe_worker"]
    assert "STRIDE-T-06" in unsafe_threats
    assert "STRIDE-T-07" in unsafe_threats
    t06_finding = next(f for f in findings if f["component_id"] == "proc_unsafe_worker" and f["threat_id"] == "STRIDE-T-06")
    assert t06_finding["severity"] == "Critical"

    secure_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_secure_worker"]
    assert "STRIDE-T-06" not in secure_threats
    assert "STRIDE-T-07" not in secure_threats


def test_path_traversal_and_file_upload_execution():
    """Verify detection of Path Traversal (STRIDE-I-08) and Arbitrary File Upload (STRIDE-E-06)."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/file-operations",
        "elements": [
            {
                "id": "proc_file_server",
                "type": "process",
                "name": "Dynamic File Downloader & Uploader",
                "properties": {
                    "sanitizes_file_paths": False,
                    "validates_file_types": False,
                },
            },
            {
                "id": "proc_safe_storage",
                "type": "process",
                "name": "Hardened S3 Asset Gateway",
                "properties": {
                    "sanitizes_file_paths": True,
                    "validates_file_types": True,
                },
            },
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]

    vuln_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_file_server"]
    assert "STRIDE-I-08" in vuln_threats
    assert "STRIDE-E-06" in vuln_threats
    e06_finding = next(f for f in findings if f["component_id"] == "proc_file_server" and f["threat_id"] == "STRIDE-E-06")
    assert e06_finding["severity"] == "Critical"

    safe_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_safe_storage"]
    assert "STRIDE-I-08" not in safe_threats
    assert "STRIDE-E-06" not in safe_threats


def test_mass_assignment_bopla_discrimination():
    """Verify that Mass Assignment (STRIDE-E-05) triggers independently of BOLA and RBAC."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/mass-assignment",
        "elements": [
            {
                "id": "proc_unfiltered_dto",
                "type": "process",
                "name": "User Profile Update Endpoint",
                "properties": {
                    "enforces_authorization": True,       # RBAC OK
                    "validates_resource_ownership": True, # BOLA OK
                    "filters_assignable_properties": False, # Vulnerable to Mass Assignment!
                },
            },
            {
                "id": "proc_filtered_dto",
                "type": "process",
                "name": "Strict Pydantic DTO Endpoint",
                "properties": {
                    "enforces_authorization": True,
                    "validates_resource_ownership": True,
                    "filters_assignable_properties": True,
                },
            },
        ],
        "data_flows": [],
    }

    result = engine.analyze(dfd)
    findings = result["findings"]

    unfiltered_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_unfiltered_dto"]
    # Neither RBAC nor BOLA should fire, ONLY Mass Assignment
    assert "STRIDE-E-02" not in unfiltered_threats
    assert "STRIDE-E-04" not in unfiltered_threats
    assert "STRIDE-E-05" in unfiltered_threats
    e05_finding = next(f for f in findings if f["component_id"] == "proc_unfiltered_dto" and f["threat_id"] == "STRIDE-E-05")
    assert e05_finding["severity"] == "High"

    filtered_threats = [f["threat_id"] for f in findings if f["component_id"] == "proc_filtered_dto"]
    assert "STRIDE-E-05" not in filtered_threats


def test_in_process_import_flows_do_not_trigger_network_tampering():
    """Verify that in-memory code imports between modules do not trigger network TLS or mTLS threats."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/in-process-imports",
        "nodes": [
            {"id": "process:mod_a", "kind": "process", "name": "Module A", "path": "src/mod_a.py"},
            {"id": "process:mod_b", "kind": "process", "name": "Module B", "path": "src/mod_b.py"},
        ],
        "edges": [
            {
                "id": "edge_import",
                "source": "process:mod_a",
                "target": "process:mod_b",
                "label": "imports",
            }
        ],
    }
    result = engine.analyze(dfd)
    findings = result["findings"]
    edge_threats = [f["threat_id"] for f in findings if f.get("component_id") == "edge_import"]
    # Internal code import should NOT trigger in-transit encryption or mTLS threats
    assert "STRIDE-T-01" not in edge_threats
    assert "STRIDE-S-02" not in edge_threats
    assert "STRIDE-I-01" not in edge_threats
    assert len(edge_threats) == 0


def test_auxiliary_test_components_filtered_by_default():
    """Verify that test harnesses and dev containers are excluded from production threat findings."""
    engine = StrideEngine(filter_auxiliary_components=True)
    dfd = {
        "repository": "test/test-harness",
        "nodes": [
            {"id": "process:tests/test_api", "kind": "process", "name": "Test Suite", "path": "tests/test_api.py"},
            {"id": "process:src/api", "kind": "process", "name": "Prod API", "path": "src/api.py", "extra": {"route_count": 2}},
        ],
        "edges": [],
    }
    result = engine.analyze(dfd)
    matrix = result["stride_matrix"]
    assert "process:tests/test_api" not in matrix
    assert "process:src/api" in matrix


def test_internal_library_without_routes_does_not_trigger_api_endpoint_threats():
    """Verify that internal modules without routes do not trigger web API threats like CORS or BOLA."""
    engine = StrideEngine()
    dfd = {
        "repository": "test/library-module",
        "nodes": [
            {
                "id": "process:src/utils",
                "kind": "process",
                "name": "Utils",
                "path": "src/utils.py",
                "extra": {"files": ["src/utils.py"], "route_count": 0, "routes": []},
            }
        ],
        "edges": [],
    }
    result = engine.analyze(dfd)
    findings = result["findings"]
    threat_ids = [f["threat_id"] for f in findings if f["component_id"] == "process:src/utils"]
    # Web API threats should NOT fire on internal library
    assert "STRIDE-S-01" not in threat_ids  # Unauthenticated API endpoint
    assert "STRIDE-S-06" not in threat_ids  # Permissive CORS
    assert "STRIDE-E-04" not in threat_ids  # BOLA / IDOR
    # General code-level threats (e.g. input bounds, input sanitization) still fire defensively
    assert "STRIDE-T-02" in threat_ids



