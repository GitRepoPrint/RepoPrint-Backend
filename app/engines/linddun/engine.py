"""Motor de análise de privacidade LINDDUN.

Avalia regras LINDDUN sobre os nós (process, data_store) e fluxos (data_flow) de um DFD
e produz um relatório estruturado de ameaças de privacidade (findings).
"""
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from app.schemas.dfd import DfdGraph
from app.engines.linddun.schemas import LinddunFinding, LinddunReport, LinddunSummary
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
        Executa a análise de ameaças LINDDUN sobre um grafo DFD (DfdGraph ou dict canónico).
        """
        # Mapear e normalizar propriedades se necessário
        normalized_dfd = map_dfd_to_linddun(dfd)

        findings: List[LinddunFinding] = []
        elements = normalized_dfd.get("elements", [])
        data_flows = normalized_dfd.get("data_flows", [])

        # 1. Avaliar regras para elementos/nós (process, data_store, actor/external_entity)
        for elem in elements:
            elem_type = elem.get("type")
            elem_props = elem.get("properties", {})

            for rule in self.rules:
                target_rule_type = rule.get("applies_to")
                # Permitir correspondência flexível para actors / external_entity
                if target_rule_type != elem_type:
                    if not (target_rule_type in {"actor", "external_entity"} and elem_type in {"actor", "external_entity"}):
                        continue

                if self._matches_conditions(rule.get("conditions", {}), elem_props):
                    findings.append(self._create_finding(rule, elem, is_flow=False))

        # 2. Avaliar regras para fluxos de dados (data_flow)
        for flow in data_flows:
            flow_props = flow.get("properties", {})

            for rule in self.rules:
                if rule.get("applies_to") != "data_flow":
                    continue

                if self._matches_conditions(rule.get("conditions", {}), flow_props):
                    findings.append(self._create_finding(rule, flow, is_flow=True))

        # 3. Estatísticas sumárias
        # Inicializar todas as categorias LINDDUN e severidades para consumo direto na UI (ex: gráficos radar/barras)
        by_category = {cat: 0 for cat in self.categories.values()}
        for f in findings:
            by_category[f.category] = by_category.get(f.category, 0) + 1

        by_severity = {sev: 0 for sev in ["Critical", "High", "Medium", "Low", "Info"]}
        for f in findings:
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1

        summary = LinddunSummary(
            total_findings=len(findings),
            by_category=by_category,
            by_severity=by_severity,
        )

        repo_name = normalized_dfd.get("repository", "unknown")

        return LinddunReport(
            repository=repo_name,
            analyzed_at=datetime.now(timezone.utc),
            summary=summary,
            findings=findings,
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

    def _create_finding(self, rule: Dict[str, Any], component: Dict[str, Any], is_flow: bool) -> LinddunFinding:
        return LinddunFinding(
            threat_id=rule["id"],
            category=rule["category"],
            severity=rule["severity"],
            target_type=rule["applies_to"],
            component_id=str(component.get("id", "")),
            component_name=component.get("name"),
            title=rule["title"],
            description=rule["description"],
            gdpr_articles=rule.get("gdpr_articles", []),
            mitigation=rule.get("mitigation", ""),
            source=str(component.get("source")) if is_flow and component.get("source") is not None else None,
            target=str(component.get("target")) if is_flow and component.get("target") is not None else None,
            file_path=component.get("file_path"),
        )
