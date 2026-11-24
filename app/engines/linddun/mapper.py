"""Mapeador e enriquecedor de DFD para o modelo de propriedades LINDDUN.

Analisa os nós e fluxos de dados gerados a partir do código-fonte (DfdGraph) e infere
as propriedades de privacidade avaliadas pelas regras do LINDDUN (ex: isEncrypted,
storesCredentials, publicNetwork, data_type, etc.).
"""
import re
from typing import Any, Dict, List, Union

from app.schemas.dfd import DfdGraph, DfdNode, DfdEdge, DfdFlow

# Padrões para inferência de identificadores diretos, pseudónimos e credenciais
DIRECT_IDENTIFIER_PATTERNS = re.compile(
    r"(user_?id|email|username|name|full_?name|cpf|ssn|phone|nif|bi|tax_?id|author)",
    re.IGNORECASE,
)
PSEUDO_IDENTIFIER_PATTERNS = re.compile(
    r"(session|cookie|token|device|ip_?address|ip|fingerprint|uuid|guid|tracker|client_?id)",
    re.IGNORECASE,
)
CREDENTIAL_PATTERNS = re.compile(
    r"(pass(word)?|secret|token|api_?key|auth|credential|jwt|bearer|private_?key)",
    re.IGNORECASE,
)
LOG_PATTERNS = re.compile(r"(log|logger|logging|audit|trace|telemetry|metrics)", re.IGNORECASE)
ANALYTICS_PATTERNS = re.compile(
    r"(analytic|tracking|profile|recommend|segment|behavior|event)",
    re.IGNORECASE,
)
FREE_TEXT_PATTERNS = re.compile(
    r"(comment|message|post|content|description|text|feedback|review|bio|note)",
    re.IGNORECASE,
)


def _infer_data_type(label: str) -> str:
    """Infere a sensibilidade dos dados com base em nomes de rotas, campos ou labels."""
    if CREDENTIAL_PATTERNS.search(label):
        return "credential"
    if DIRECT_IDENTIFIER_PATTERNS.search(label):
        return "direct_identifier"
    if PSEUDO_IDENTIFIER_PATTERNS.search(label):
        return "pseudo_identifier"
    return "general_data"


def _infer_process_properties(node: DfdNode, flows: List[DfdFlow]) -> Dict[str, Any]:
    """Infere propriedades LINDDUN para nós do tipo 'process'."""
    props: Dict[str, Any] = {
        "isWebApplication": True,
        "isALog": False,
        "storesCredentials": False,
        "isEncrypted": True,
        "builds_user_profiles": False,
        "derives_patterns": False,
        "stores_authenticated_identity": False,
        "embeds_metadata": False,
        "observable_side_effects": False,
        "reveals_record_existence": False,
        "performs_unnecessary_enrichment": False,
        "lacks_preference_controls": False,
        "lacks_legal_basis": False,
        "deviates_from_privacy_standards": False,
        "lacks_lifecycle_management": False,
    }

    name_and_path = f"{node.name} {node.path or ''}"

    if LOG_PATTERNS.search(name_and_path):
        props["isALog"] = True
        props["stores_authenticated_identity"] = True

    if CREDENTIAL_PATTERNS.search(name_and_path):
        props["storesCredentials"] = True

    if ANALYTICS_PATTERNS.search(name_and_path):
        props["builds_user_profiles"] = True
        props["derives_patterns"] = True
        props["performs_unnecessary_enrichment"] = True

    # Analisar se algum flow associado a este processo lida com credenciais
    for flow in flows:
        if flow.process == node.id or (node.path and flow.file.endswith(node.path)):
            route_str = f"{flow.route} {flow.handler}"
            if CREDENTIAL_PATTERNS.search(route_str):
                props["storesCredentials"] = True
            if LOG_PATTERNS.search(route_str):
                props["isALog"] = True
            if ANALYTICS_PATTERNS.search(route_str):
                props["builds_user_profiles"] = True
                props["derives_patterns"] = True

    # Se já vierem propriedades explícitas no nó, mantê-las ou sobrepor
    if node.extra and "properties" in node.extra:
        props.update(node.extra["properties"])

    return props


def _infer_datastore_properties(node: DfdNode) -> Dict[str, Any]:
    """Infere propriedades LINDDUN para nós do tipo 'data_store'."""
    props: Dict[str, Any] = {
        "stores_raw_data": True,
        "contains_pseudo_identifiers": False,
        "contains_quasi_identifiers": False,
        "re_identification_risk": False,
        "isEncrypted": False,  # por defeito em repouso requer configuração explícita
        "has_retention_policy": False,  # por defeito bases de dados não têm TTL automático
        "is_append_only": False,
        "prevents_erasure": False,
        "provides_user_access": False,
        "provides_rectification_erasure": False,
    }

    name_and_tech = f"{node.name} {node.technology or ''} {' '.join(node.tags)}"

    if DIRECT_IDENTIFIER_PATTERNS.search(name_and_tech) or PSEUDO_IDENTIFIER_PATTERNS.search(name_and_tech):
        props["contains_pseudo_identifiers"] = True
        props["contains_quasi_identifiers"] = True
        props["re_identification_risk"] = True

    # Caches (ex: Redis / Memcached) têm habitualmente TTL/retention policy
    if "cache" in node.tags or "redis" in name_and_tech.lower() or "memcached" in name_and_tech.lower():
        props["has_retention_policy"] = True

    # Message queues e event logs podem ser append-only
    if "queue" in node.tags or "kafka" in name_and_tech.lower():
        props["is_append_only"] = True
        props["prevents_erasure"] = True

    if node.extra and "properties" in node.extra:
        props.update(node.extra["properties"])

    return props


def _infer_flow_properties(
    edge: DfdEdge,
    nodes_by_id: Dict[str, DfdNode],
    flow_info: Union[DfdFlow, None] = None,
) -> Dict[str, Any]:
    """Infere propriedades LINDDUN para arestas e fluxos de dados."""
    src_node = nodes_by_id.get(edge.source)
    tgt_node = nodes_by_id.get(edge.target)

    is_public = False
    direction = "inbound"

    # Se a origem for externa (ex: utilizador/cliente web) -> inbound da rede pública
    if src_node and src_node.kind == "external_entity":
        is_public = True
        direction = "inbound"
    # Se o destino for externo (ex: API externa de terceiros ou cliente) -> outbound
    elif tgt_node and tgt_node.kind == "external_entity":
        is_public = True
        direction = "outbound"

    # Protocolo
    protocol = (edge.protocol or "").lower()
    is_encrypted = protocol in {"https", "tls", "ssl", "ssh"}

    # Inferência do tipo de dados a partir do label e do flow correspondente
    label_full = f"{edge.label} {' '.join(edge.operations)}"
    if flow_info:
        label_full += f" {flow_info.route} {flow_info.handler} {flow_info.method}"

    data_type = _infer_data_type(label_full)

    props: Dict[str, Any] = {
        "direction": direction,
        "data_type": data_type,
        "publicNetwork": is_public,
        "isEncrypted": is_encrypted,
        "is_pseudonymized": False,
        "isSigned": False,
        "has_distinguishable_patterns": False,
        "contains_inferable_identity": data_type in {"direct_identifier", "pseudo_identifier"},
        "accepts_free_form_text": bool(FREE_TEXT_PATTERNS.search(label_full)),
        "recycles_global_identifiers": False,
        "generates_receipt_evidence": False,
        "reveals_resource_existence": False,
        "collects_excessive_sensitivity": False,
        "high_collection_frequency": False,
        "exposes_to_third_parties": bool(tgt_node and tgt_node.kind == "external_entity"),
        "lacks_processing_notice": is_public and direction == "inbound",
        "shares_data_of_others": False,
    }

    if edge.extra and "properties" in edge.extra:
        props.update(edge.extra["properties"])

    return props


def map_dfd_to_linddun(dfd: Union[DfdGraph, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Converte e enriquece um DFD (seja DfdGraph ou dict canónico) para a estrutura
    esperada pelo motor de regras LINDDUN.
    """
    if isinstance(dfd, dict) and "elements" in dfd and "data_flows" in dfd:
        # Já está no formato canónico (ex: testes de CW685), manter propriedades existentes
        return dfd

    # Se for DfdGraph (ou dicionário com nós e arestas do CodeToDFD)
    if isinstance(dfd, dict):
        dfd_obj = DfdGraph.model_validate(dfd)
    else:
        dfd_obj = dfd

    nodes_by_id: Dict[str, DfdNode] = {node.id: node for node in dfd_obj.nodes}
    flows = dfd_obj.extra.flows if dfd_obj.extra else []

    elements: List[Dict[str, Any]] = []
    for node in dfd_obj.nodes:
        if node.kind == "process":
            props = _infer_process_properties(node, flows)
            target_type = "process"
        elif node.kind == "data_store":
            props = _infer_datastore_properties(node)
            target_type = "data_store"
        else:
            props = {
                "publicNetwork": True,
                "shares_personal_data": True,
            }
            if node.extra and "properties" in node.extra:
                props.update(node.extra["properties"])
            target_type = "actor"

        elements.append({
            "id": node.id,
            "name": node.name,
            "type": target_type,
            "technology": node.technology,
            "file_path": node.path,
            "properties": props,
        })

    # Mapear arestas para data_flows
    data_flows: List[Dict[str, Any]] = []

    # Criar um índice de flows por process para cruzamento
    flow_map: Dict[str, DfdFlow] = {f.id: f for f in flows}

    for edge in dfd_obj.edges:
        associated_flow = flow_map.get(edge.id)
        props = _infer_flow_properties(edge, nodes_by_id, associated_flow)

        data_flows.append({
            "id": edge.id,
            "name": edge.label,
            "source": edge.source,
            "target": edge.target,
            "properties": props,
            "operations": edge.operations,
            "protocol": edge.protocol,
        })

    repo_name = f"{dfd_obj.repository.owner}/{dfd_obj.repository.repo}"

    return {
        "repository": repo_name,
        "elements": elements,
        "data_flows": data_flows,
    }
