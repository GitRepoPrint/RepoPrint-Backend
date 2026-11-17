"""STRIDE Threat Modeling Engine.

Analyzes Data Flow Diagrams (DFDs) in JSON format and identifies threats
based on declarative rules adapted from OWASP pytm and the STRIDE methodology.
Operates on a strictly defensive security model (assumes absence of controls in case of doubt).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


class StrideEngine:
    """Evaluation engine for STRIDE threats against DFD representations."""

    def __init__(self, catalog_path: str | Path | None = None) -> None:
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

    def analyze(self, dfd: dict[str, Any]) -> dict[str, Any]:
        """Analyze a DFD JSON structure and return detected STRIDE threats."""
        findings: list[dict[str, Any]] = []

        elements, data_flows = self._normalize_dfd(dfd)

        # 1. Evaluate element rules (process, data_store, external_entity)
        for elem in elements:
            elem_type = elem.get("type")
            elem_props = self._extract_properties(elem)

            for rule in self.rules:
                if rule.get("applies_to") != elem_type:
                    continue

                if self._matches_conditions(rule.get("conditions", {}), elem_props):
                    findings.append(self._create_finding(rule, elem, is_flow=False))

        # 2. Evaluate flow rules (data_flow)
        for flow in data_flows:
            flow_props = self._extract_properties(flow)

            for rule in self.rules:
                if rule.get("applies_to") != "data_flow":
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

        return {
            "repository": dfd.get("repository", "unknown"),
            "total_findings": len(findings),
            "summary_by_category": summary_by_category,
            "summary_by_severity": summary_by_severity,
            "stride_matrix": dict(stride_matrix),
            "findings": findings,
        }

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
        print("Usage: python stride_engine.py <dfd_json_file>")
