import json
from typing import Any, Dict, List, Optional


class LinddunEngine:
    def __init__(self, catalog_path: str = "linddun_catalog.json"):
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)

        self.categories = catalog.get("categories", {})
        self.dfd_properties = catalog.get("dfd_properties", {})
        self.data_types = catalog.get("data_types", [])
        self.rules = catalog.get("rules", [])

    def analyze(self, dfd: Dict[str, Any]) -> Dict[str, Any]:
        findings: List[Dict[str, Any]] = []

        elements = dfd.get("elements", [])
        data_flows = dfd.get("data_flows", [])

        # 1. Avaliar regras para nos (process e data_store)
        for elem in elements:
            elem_type = elem.get("type")
            elem_props = elem.get("properties", {})

            for rule in self.rules:
                if rule.get("applies_to") != elem_type:
                    continue

                if self._matches_conditions(rule.get("conditions", {}), elem_props):
                    findings.append(self._create_finding(rule, elem, is_flow=False))

        # 2. Avaliar regras para fluxos (data_flow)
        for flow in data_flows:
            flow_props = flow.get("properties", {})

            for rule in self.rules:
                if rule.get("applies_to") != "data_flow":
                    continue

                if self._matches_conditions(rule.get("conditions", {}), flow_props):
                    findings.append(self._create_finding(rule, flow, is_flow=True))

        return {
            "repository": dfd.get("repository", "unknown"),
            "total_findings": len(findings),
            "findings": findings
        }

    def _matches_conditions(self, conditions: Dict[str, Any], properties: Dict[str, Any]) -> bool:
        for key, expected_value in conditions.items():
            actual_value = properties.get(key)

            if isinstance(expected_value, list):
                if isinstance(actual_value, list):
                    if not any(item in actual_value for item in expected_value):
                        return False
                else:
                    if actual_value not in expected_value:
                        return False
            else:
                if actual_value != expected_value:
                    return False

        return True

    def _create_finding(self, rule: Dict[str, Any], component: Dict[str, Any], is_flow: bool) -> Dict[str, Any]:
        finding = {
            "threat_id": rule["id"],
            "category": rule["category"],
            "severity": rule["severity"],
            "target_type": rule["applies_to"],
            "component_id": component.get("id"),
            "component_name": component.get("name"),
            "title": rule["title"],
            "description": rule["description"],
            "gdpr_articles": rule.get("gdpr_articles", []),
            "mitigation": rule.get("mitigation", "")
        }

        if is_flow:
            finding["source"] = component.get("source")
            finding["target"] = component.get("target")

        if component.get("file_path"):
            finding["file_path"] = component.get("file_path")

        return finding