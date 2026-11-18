"""STRIDE Threat Modeling Engine.

Analyzes Data Flow Diagrams (DFDs) in JSON format and identifies threats
based on declarative rules adapted from OWASP pytm and the STRIDE methodology.
Operates on a refined, context-aware defensive security model that avoids false positives
on in-process code imports, test suites, and template presentations.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


class StrideEngine:
    """Evaluation engine for STRIDE threats against DFD representations."""

    def __init__(
        self,
        catalog_path: str | Path | None = None,
        filter_auxiliary_components: bool = True,
    ) -> None:
        if catalog_path is None:
            catalog_path = Path(__file__).parent / "stride_catalog.json"
        else:
            catalog_path = Path(catalog_path)

        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)

        self.categories: dict[str, Any] = catalog.get("categories", {})
        self.stride_per_element: dict[str, list[str]] = catalog.get("stride_per_element", {})
        self.dfd_properties: dict[str, str] = catalog.get("dfd_properties", {})
        self.rules: list[dict[str, Any]] = catalog.get("rules", [])
        self.filter_auxiliary_components = filter_auxiliary_components

    def analyze(self, dfd: dict[str, Any]) -> dict[str, Any]:
        """Analyze a DFD JSON structure and return detected STRIDE threats."""
        findings: list[dict[str, Any]] = []

        elements, data_flows = self._normalize_dfd(dfd)
        context = self._build_graph_context(elements, data_flows)

        # 1. Evaluate element rules (process, data_store, external_entity)
        for elem in elements:
            if self.filter_auxiliary_components and self._is_auxiliary_or_test(elem):
                continue

            elem_type = elem.get("type")
            elem_props = self._extract_properties(elem)

            for rule in self.rules:
                if rule.get("applies_to") != elem_type:
                    continue

                if not self._is_rule_applicable_to_element(rule, elem, elem_props, context):
                    continue

                if self._matches_conditions(rule.get("conditions", {}), elem_props):
                    findings.append(self._create_finding(rule, elem, is_flow=False))

        # 2. Evaluate flow rules (data_flow)
        for flow in data_flows:
            if self.filter_auxiliary_components and self._is_flow_connected_to_auxiliary(flow, context):
                continue

            flow_props = self._extract_properties(flow)

            for rule in self.rules:
                if rule.get("applies_to") != "data_flow":
                    continue

                if not self._is_rule_applicable_to_flow(rule, flow, flow_props, context):
                    continue

                if self._matches_conditions(rule.get("conditions", {}), flow_props):
                    findings.append(self._create_finding(rule, flow, is_flow=True))

        # 3. Build metrics and STRIDE matrix
        summary_by_category = {cat: 0 for cat in self.categories.keys()}
        summary_by_severity = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
        stride_matrix: dict[str, list[str]] = defaultdict(list)

        for f in findings:
            cat = f.get("category")
            if cat in summary_by_category:
                summary_by_category[cat] += 1

            sev = f.get("severity")
            if sev in summary_by_severity:
                summary_by_severity[sev] += 1

            comp_id = f.get("component_id")
            if comp_id and cat and cat not in stride_matrix[comp_id]:
                stride_matrix[comp_id].append(cat)

        repo = dfd.get("repository", "unknown")
        if isinstance(repo, dict):
            repo_name = f"{repo.get('owner', '')}/{repo.get('repo', '')}" if repo.get("owner") else repo.get("repo", "unknown")
        else:
            repo_name = str(repo)

        return {
            "repository": repo_name,
            "total_findings": len(findings),
            "summary_by_category": summary_by_category,
            "summary_by_severity": summary_by_severity,
            "stride_matrix": dict(stride_matrix),
            "findings": findings,
        }

    def _build_graph_context(
        self, elements: list[dict[str, Any]], data_flows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Index the graph for contextual topological checks."""
        elem_by_id = {e.get("id"): e for e in elements if e.get("id")}
        inbound: dict[str, list[dict[str, Any]]] = defaultdict(list)
        outbound: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for f in data_flows:
            src = f.get("source")
            tgt = f.get("target")
            if src:
                outbound[src].append(f)
            if tgt:
                inbound[tgt].append(f)

        return {
            "elem_by_id": elem_by_id,
            "inbound": inbound,
            "outbound": outbound,
        }

    def _is_auxiliary_or_test(self, comp: dict[str, Any]) -> bool:
        """Identify test suites, dev environments, or auxiliary tooling."""
        path = str(comp.get("path") or comp.get("file_path") or "").lower()
        cid = str(comp.get("id") or "").lower()
        name = str(comp.get("name") or "").lower()

        # If it's a pure architectural ID without path (e.g. proc_1, proc_auth), keep it
        if not path:
            return cid.startswith("test_") or name.startswith("test ")

        parts = [p.strip() for p in path.split("/") if p.strip()]
        test_indicators = {"tests", "__tests__", "test_apps", "type_check", ".devcontainer", "fixtures", "mock", "mocks"}
        return any(part in test_indicators for part in parts)

    def _is_flow_connected_to_auxiliary(self, flow: dict[str, Any], context: dict[str, Any]) -> bool:
        """Check if a data flow is exclusively tied to an auxiliary/test component."""
        src_id = flow.get("source")
        tgt_id = flow.get("target")
        src = context["elem_by_id"].get(src_id)
        tgt = context["elem_by_id"].get(tgt_id)
        if src and self._is_auxiliary_or_test(src):
            return True
        if tgt and self._is_auxiliary_or_test(tgt):
            return True
        return False

    def _is_rule_applicable_to_element(
        self,
        rule: dict[str, Any],
        elem: dict[str, Any],
        elem_props: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        """Filter rules based on the element's actual role and attack surface."""
        rid = rule.get("id", "")
        conditions = rule.get("conditions", {})

        # 1. If any condition property is explicitly defined as a boolean/value, always evaluate
        has_explicit_prop = any(
            k in elem_props and elem_props[k] is not None
            for k in conditions.keys()
            if k not in ("protocol", "trust_zone")
        )
        if has_explicit_prop:
            return True

        # 2. Template directories (HTML views without backend API routes)
        path = str(elem.get("path") or elem.get("file_path") or "").lower()
        parts = [p.strip() for p in path.split("/") if p.strip()]
        is_template = "templates" in parts or "html" in elem_props
        extra = elem.get("extra", {}) if isinstance(elem.get("extra"), dict) else {}
        routes = extra.get("routes", [])
        route_count = extra.get("route_count", len(routes))

        if is_template and route_count == 0:
            return False

        # 3. Determine if this process exposes a Web / API surface
        inbound = context["inbound"].get(elem.get("id"), [])
        receives_external_traffic = any(
            str(f.get("source", "")).startswith("external_entity")
            or "http" in str(f.get("label", "")).lower()
            or "form" in str(f.get("label", "")).lower()
            for f in inbound
        )

        has_api_surface = (
            route_count > 0
            or receives_external_traffic
            or "api" in str(elem.get("id", "")).lower()
            or "api" in str(elem.get("name", "")).lower()
            or "gateway" in str(elem.get("name", "")).lower()
            or ("files" not in extra and not path)  # Architectural DFD without AST files
        )

        # Web API-specific rules
        web_api_rules = {
            "STRIDE-S-01",  # Unauthenticated API Endpoint
            "STRIDE-S-03",  # Weak or Unvalidated Session Identifiers
            "STRIDE-S-05",  # Open Redirect
            "STRIDE-S-06",  # Permissive CORS
            "STRIDE-D-01",  # Missing Rate Limiting
            "STRIDE-E-04",  # BOLA / IDOR
            "STRIDE-E-05",  # Mass Assignment (BOPLA)
        }
        if rid in web_api_rules and not has_api_surface:
            return False

        # Webhook specific rule (STRIDE-T-07)
        if rid == "STRIDE-T-07":
            is_webhook = (
                "webhook" in str(elem.get("id", "")).lower()
                or "webhook" in str(elem.get("name", "")).lower()
                or "webhook" in path
                or any("webhook" in str(r.get("path", "")).lower() for r in routes)
                or ("files" not in extra and not path)
            )
            if not is_webhook:
                return False

        # Arbitrary file upload rule (STRIDE-E-06)
        if rid == "STRIDE-E-06":
            is_upload = (
                "upload" in str(elem.get("id", "")).lower()
                or "upload" in str(elem.get("name", "")).lower()
                or "upload" in path
                or any("upload" in str(r.get("path", "")).lower() for r in routes)
                or ("files" not in extra and not path)
            )
            if not is_upload:
                return False

        # Insecure deserialization rule (STRIDE-T-06)
        if rid == "STRIDE-T-06":
            tech = str(elem.get("technology") or "").lower()
            tags = [str(t).lower() for t in elem.get("tags", [])]
            via: list[str] = []
            for f in inbound:
                via_list = f.get("extra", {}).get("via", []) if isinstance(f.get("extra"), dict) else []
                via.extend([str(v).lower() for v in via_list])
            uses_serialization = (
                any(k in tech for k in ["celery", "redis", "pickle", "yaml", "serialize", "queue", "kafka", "rabbit"])
                or any(k in tags for k in ["queue", "celery", "worker", "serialization"])
                or any(k in str(via) for k in ["celery", "pickle", "yaml", "serialize"])
                or ("files" not in extra and not path)
            )
            if not uses_serialization:
                return False

        # Action 1: Check if this component is passive library / framework code (not a standalone running OS daemon)
        is_library = (
            ("files" in extra or path.startswith(("src/", "lib/")))
            and route_count == 0
            and not receives_external_traffic
            and not any("main" in str(d).lower() for d in extra.get("definitions", []))
        )
        daemon_process_rules = {
            "STRIDE-E-01",  # Principle of Least Privilege / daemon running as root
            "STRIDE-R-01",  # Security Audit Logging for application events
            "STRIDE-D-01",  # Rate Limiting
        }
        if is_library and rid in daemon_process_rules:
            return False

        # Action 1: Embedded / Local File Datastore vs Remote Cloud Datastore
        tech = str(elem.get("technology") or "").lower()
        is_embedded_datastore = (
            any(t in tech for t in ["sqlite", "file-storage", "local_file", "disk"])
            or path.endswith((".db", ".sqlite", ".sqlite3"))
            or "sqlite" in str(elem.get("id", "")).lower()
            or "file-storage" in str(elem.get("id", "")).lower()
        )
        remote_cluster_datastore_rules = {
            "STRIDE-D-04",  # Missing backup and replication in cluster/cloud
            "STRIDE-D-03",  # Quota limits / volume capacity exhaustion
        }
        if is_embedded_datastore and rid in remote_cluster_datastore_rules:
            return False

        return True

    def _is_rule_applicable_to_flow(
        self,
        rule: dict[str, Any],
        flow: dict[str, Any],
        flow_props: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        """Filter network flow rules so that in-memory code imports are not falsely flagged."""
        rid = rule.get("id", "")
        conditions = rule.get("conditions", {})

        # If a property is explicitly declared with a boolean, always evaluate
        has_explicit_prop = any(
            k in flow_props and flow_props[k] is not None
            for k in conditions.keys()
            if k not in ("protocol", "trust_zone")
        )
        if has_explicit_prop:
            return True

        label = str(flow.get("label") or flow.get("name") or "").lower()
        src_id = str(flow.get("source", ""))
        tgt_id = str(flow.get("target", ""))
        protocol = flow.get("protocol")

        is_network = (
            protocol is not None
            or flow.get("is_network") is True
            or flow.get("crosses_trust_boundary") is True
            or src_id.startswith("external_entity")
            or tgt_id.startswith("external_entity")
            or src_id.startswith("data_store")
            or tgt_id.startswith("data_store")
            or any(kw in label for kw in ["http", "form", "publishes", "consumes", "socket", "api", "rpc", "remote"])
            or (not label and not src_id.startswith("process:") and not tgt_id.startswith("process:"))
        )

        network_rules = {
            "STRIDE-S-02",  # Missing Mutual Authentication on Data Flow
            "STRIDE-T-01",  # Unencrypted Dataflow in Transit
            "STRIDE-T-05",  # Insecure Trust Boundary Crossing without Transport Encryption
            "STRIDE-I-01",  # Cleartext Sensitive Data in Transit
            "STRIDE-I-03",  # Insecure Protocol Usage
            "STRIDE-D-02",  # Missing Connection Timeout Configuration
        }

        if rid in network_rules and not is_network:
            return False

        # Action 1: Public web browser clients accessing web endpoints do not present mTLS client certificates
        if rid == "STRIDE-S-02" and src_id == "external_entity:client" and label in ["submits form", "http requests"]:
            return False

        return True

    def _normalize_dfd(self, dfd: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Normalize DFD representation whether in input.json format or DfdGraph schema format."""
        elements: list[dict[str, Any]] = []
        data_flows: list[dict[str, Any]] = []

        # Format 1: elements & data_flows (e.g. doc/input.json)
        if "elements" in dfd:
            elements = list(dfd.get("elements", []))
        # Format 2: nodes & edges (e.g. app/schemas/dfd.py DfdGraph)
        elif "nodes" in dfd:
            for node in dfd.get("nodes", []):
                elem = dict(node)
                # Map kind to type
                if "kind" in elem and "type" not in elem:
                    elem["type"] = elem["kind"]
                elements.append(elem)

        if "data_flows" in dfd:
            data_flows = list(dfd.get("data_flows", []))
        elif "edges" in dfd:
            for edge in dfd.get("edges", []):
                flow = dict(edge)
                if "name" not in flow and "label" in flow:
                    flow["name"] = flow["label"]
                data_flows.append(flow)

        return elements, data_flows

    def _extract_properties(self, component: dict[str, Any]) -> dict[str, Any]:
        """Extract properties merging top-level fields, nested 'properties', and 'extra'."""
        props: dict[str, Any] = {}

        # Merge in order of priority: extra -> properties -> top-level
        if "extra" in component and isinstance(component["extra"], dict):
            props.update(component["extra"])
        if "properties" in component and isinstance(component["properties"], dict):
            props.update(component["properties"])

        for k, v in component.items():
            if k not in ("properties", "extra", "provenance"):
                props[k] = v

        # Action 1: Infer authentication from rich code metadata if not explicitly declared
        if props.get("provides_authentication") is None:
            defs = [str(d).lower() for d in props.get("definitions", [])]
            auth_keywords = [
                "login", "authenticate", "require_login", "load_logged_in_user",
                "verify_password", "check_password", "jwt", "session_token",
            ]
            files = [str(f).lower() for f in props.get("files", [])]
            routes = props.get("routes", [])
            route_paths = [str(r.get("path", "")).lower() for r in routes if isinstance(r, dict)]

            has_auth_logic = (
                any(any(kw in d for kw in auth_keywords) for d in defs)
                or any(any(kw in f for kw in ["/auth.", "/security.", "auth.py", "security.py"]) for f in files)
                or any(any(kw in p for kw in ["/login", "/auth", "/register"]) for p in route_paths)
            )
            if has_auth_logic:
                props["provides_authentication"] = True

        return props

    def _matches_conditions(self, conditions: dict[str, Any], properties: dict[str, Any]) -> bool:
        """Evaluate conditions using a defensive security model.

        If a security property is missing or unknown (None), and the rule expects False
        (i.e. absence of control), the condition is satisfied to signal the threat.
        """
        for key, expected_value in conditions.items():
            actual_value = properties.get(key)

            # Defensive principle: missing security controls default to False
            if actual_value is None:
                if expected_value is False:
                    actual_value = False
                elif expected_value is True:
                    return False
                elif isinstance(expected_value, list):
                    # For protocol/string lists, unknown does not match an explicit list
                    return False

            if isinstance(expected_value, list):
                # Check list membership (case-insensitive for strings)
                if isinstance(actual_value, str):
                    expected_upper = [str(x).upper() for x in expected_value]
                    if actual_value.upper() not in expected_upper:
                        return False
                elif isinstance(actual_value, list):
                    if not any(item in actual_value for item in expected_value):
                        return False
                else:
                    if actual_value not in expected_value:
                        return False
            else:
                if actual_value != expected_value:
                    return False

        return True

    def _create_finding(self, rule: dict[str, Any], component: dict[str, Any], is_flow: bool) -> dict[str, Any]:
        """Format an identified threat finding."""
        finding: dict[str, Any] = {
            "threat_id": rule["id"],
            "category": rule["category"],
            "stride_letter": rule.get("stride_letter", ""),
            "severity": rule["severity"],
            "target_type": rule["applies_to"],
            "component_id": component.get("id"),
            "component_name": component.get("name", component.get("id")),
            "title": rule["title"],
            "description": rule["description"],
            "mitigation": rule.get("mitigation", ""),
            "references": rule.get("references", []),
        }

        if is_flow:
            finding["source"] = component.get("source")
            finding["target"] = component.get("target")
            if "protocol" in component:
                finding["protocol"] = component.get("protocol")

        if component.get("file_path"):
            finding["file_path"] = component.get("file_path")
        elif component.get("path"):
            finding["file_path"] = component.get("path")

        return finding


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        target_file = sys.argv[1]
        with open(target_file, "r", encoding="utf-8") as f:
            target_dfd = json.load(f)

        engine = StrideEngine()
        result = engine.analyze(target_dfd)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Usage: python defensive_stride_engine.py <dfd_json_file>")
