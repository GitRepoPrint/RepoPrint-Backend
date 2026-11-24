"""Motor de análise de privacidade LINDDUN GO.

Avalia as 33 cartas do LINDDUN GO sobre os nós e fluxos de um DFD e produz um
relatório estruturado de ameaças de privacidade puras (threats), com as respetivas
perguntas de elicitação, hotspots, consequências e informação de contexto.
"""
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from app.schemas.dfd import DfdGraph
from app.engines.linddun.schemas import LinddunReport, LinddunSummary, LinddunThreat
from app.engines.linddun.mapper import map_dfd_to_linddun
from app.engines.linddun.catalog import load_catalog


class LinddunEngine:
    def __init__(self, catalog_path: Optional[str] = None):
        if catalog_path:
            import json
            with open(catalog_path, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        else:
            catalog = load_catalog()

        self.categories = catalog.get("categories", {})
        self.dfd_properties = catalog.get("dfd_properties", {})
        self.data_types = catalog.get("data_types", [])
        self.rules = catalog.get("rules", [])

    def analyze(self, dfd: Union[DfdGraph, Dict[str, Any]]) -> LinddunReport:
        """
        Executa a análise de ameaças LINDDUN GO sobre um grafo DFD.
        """
        normalized_dfd = map_dfd_to_linddun(dfd)

        threats: List[LinddunThreat] = []
        elements = normalized_dfd.get("elements", [])
        data_flows = normalized_dfd.get("data_flows", [])

        # 1. Avaliar regras para elementos/nós (process, data_store, actor/external_entity)
        for elem in elements:
            elem_type = elem.get("type")
            elem_props = elem.get("properties", {})

            for rule in self.rules:
                target_rule_type = rule.get("applies_to")
                if target_rule_type != elem_type:
                    if not (target_rule_type in {"actor", "external_entity"} and elem_type in {"actor", "external_entity"}):
                        continue

                if self._matches_conditions(rule.get("conditions", {}), elem_props):
                    threats.append(self._create_threat(rule, elem, is_flow=False))

        # 2. Avaliar regras para fluxos de dados (data_flow)
        for flow in data_flows:
            flow_props = flow.get("properties", {})

            for rule in self.rules:
                if rule.get("applies_to") != "data_flow":
                    continue

                if self._matches_conditions(rule.get("conditions", {}), flow_props):
                    threats.append(self._create_threat(rule, flow, is_flow=True))

        # 3. Sumário quantitativo puro do LINDDUN GO
        by_category = {cat: 0 for cat in self.categories.values()}
        for t in threats:
            by_category[t.category] = by_category.get(t.category, 0) + 1

        by_threat_source = {"Organizational": 0, "External": 0}
        for t in threats:
            for s in t.threat_sources:
                by_threat_source[s] = by_threat_source.get(s, 0) + 1

        by_target_type = {"process": 0, "data_store": 0, "data_flow": 0, "actor": 0}
        for t in threats:
            by_target_type[t.target_type] = by_target_type.get(t.target_type, 0) + 1

        summary = LinddunSummary(
            total_threats=len(threats),
            by_category=by_category,
            by_threat_source=by_threat_source,
            by_target_type=by_target_type,
        )

        repo_name = normalized_dfd.get("repository", "unknown")

        return LinddunReport(
            repository=repo_name,
            analyzed_at=datetime.now(timezone.utc),
            summary=summary,
            threats=threats,
            findings=threats,
        )

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

    def _create_threat(self, rule: Dict[str, Any], component: Dict[str, Any], is_flow: bool) -> LinddunThreat:
        return LinddunThreat(
            threat_id=rule["id"],
            category=rule["category"],
            title=rule["title"],
            hotspot=rule.get("hotspot", ""),
            threat_sources=rule.get("threat_sources", []),
            target_type=rule["applies_to"],
            component_id=str(component.get("id", "")),
            component_name=component.get("name"),
            description=rule["description"],
            elicitation_questions=rule.get("elicitation_questions", []),
            consequences=rule.get("consequences", []),
            examples=rule.get("examples", []),
            additional_info=rule.get("additional_info", ""),
            source=str(component.get("source")) if is_flow and component.get("source") is not None else None,
            target=str(component.get("target")) if is_flow and component.get("target") is not None else None,
            file_path=component.get("file_path"),
        )
