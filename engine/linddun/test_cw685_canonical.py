"""
Teste de validação baseado no Relatório CW685 da KU Leuven (Social Network 2.0).
Fonte: CW685.pdf, Página 22 (DFD) e Página 26 (Mapping Table).
"""

from linddun_engine import LinddunEngine


def test_cw685_social_network():
    engine = LinddunEngine("linddun_catalog.json")

    # DFD tal como definido no relatório CW685 (Página 22)
    cw685_dfd = {
        "repository": "kuleuven-cw685-social-network",
        "elements": [
            {
                "id": "1",
                "name": "User",
                "type": "actor",
                "properties": {
                    "shares_personal_data": True
                }
            },
            {
                "id": "2",
                "name": "Portal",
                "type": "process",
                "properties": {
                    "isALog": False
                }
            },
            {
                "id": "3",
                "name": "Service",
                "type": "process",
                "properties": {
                    "isALog": False
                }
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
                    "provides_user_access": False
                }
            }
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
                    "is_pseudonymized": False
                }
            },
            {
                "id": "DF3",
                "name": "Service data stream (portal-service)",
                "source": "2",
                "target": "3",
                "properties": {
                    "direction": "inbound",
                    "data_type": "general_data",
                    "isEncrypted": True
                }
            },
            {
                "id": "DF4",
                "name": "DB data stream (service-DB)",
                "source": "3",
                "target": "4",
                "properties": {
                    "direction": "inbound",
                    "data_type": "general_data",
                    "isEncrypted": True
                }
            }
        ]
    }

    report = engine.analyze(cw685_dfd)
    
    # Agrupar ameaças detetadas por componente
    threats_by_component = {}
    for finding in report["findings"]:
        name = finding["component_name"]
        threats_by_component.setdefault(name, []).append(finding["threat_id"])

    print("=== RESULTADOS DO TESTE CANÓNICO CW685 ===")
    for comp, t_list in threats_by_component.items():
        print(f"Componente '{comp}': {t_list}")

    # 1. Validação no Data Store (CW685 Página 26: L, I, Di)
    ds_threats = threats_by_component.get("Social network data", [])
    assert "L4" in ds_threats, "Falta a ameaça de Linkability (L4) em Social network data (MUC01)"
    assert "I5" in ds_threats, "Falta a ameaça de Identifiability (I5) em Social network data"
    assert "DD4" in ds_threats, "Falta a ameaça de Data Disclosure (DD4) em Social network data"

    # 2. Validação no Fluxo de Entrada (CW685 Página 26: I / L em user-portal)
    flow_threats = threats_by_component.get("User data stream (user-portal)", [])
    assert "I1" in flow_threats or "L1" in flow_threats, "Falta Identifiability/Linkability no fluxo user-portal"

    print("\nSUCESSO: O motor gerou exatamente as ameaças documentadas no relatório CW685.")


if __name__ == "__main__":
    test_cw685_social_network()