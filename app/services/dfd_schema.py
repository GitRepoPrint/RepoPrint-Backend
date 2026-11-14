"""Converte o grafo interno de build_dfd() para o schema partilhado (docs/dfd-schema.md, nível "detail").

build_dfd() continua a ser o núcleo da análise e não muda; aqui só se renomeiam tipos e ids, se acrescenta a
proveniência e se tira do grafo tudo o que é apresentação (posições).

Proveniência: o grafo interno não guarda ficheiro e linha dos imports, por isso a evidência é a possível hoje:
  - processos: os ficheiros que os formam (reais);
  - restantes nós e ligações: uma nota com o import ou a rota que os originou;
  - confiança: "high" para processos (existem no repositório), "medium" para o resto (vem de heurísticas
    sobre imports, URLs e nomes de métodos).
As linhas dos pontos de chamada continuam a ir em extra.flows[].resources[].site.
"""
import re
from datetime import datetime, timezone

from app.services.dfd_catalog import DATASTORE, EXTERNAL, QUEUE, TECHNOLOGY

SCHEMA_VERSION= "1"
CLIENT_ID= "external_entity:client"
ROOT_GROUP= "(root)"
MAX_FILE_EVIDENCE= 5
MAX_NOTE_EVIDENCE= 3


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def _identity(node: dict) -> dict:
    """Id novo, tipo, tecnologia e etiquetas de um nó interno."""
    data= node["data"]
    label= data["label"]
    kind= node["type"]
    if kind == "process":
        path= data["path"]
        path= "." if path == ROOT_GROUP else path
        return {"id": f"process:{path}", "kind": "process", "name": label, "path": path}
    if kind == "entity":
        return {"id": CLIENT_ID, "kind": "external_entity", "name": label, "tags": ["client"]}
    if kind == EXTERNAL and "url" in data:
        # sítio encontrado num URL do código: não se sabe que tecnologia é
        return {"id": f"external_entity:{label.lower()}", "kind": "external_entity", "name": label, "tags": ["third-party"]}
    technology= TECHNOLOGY.get((kind, label)) or slug(label)
    if kind == EXTERNAL:
        return {"id": f"external_entity:{technology}", "kind": "external_entity", "name": label,
                "technology": technology, "tags": ["third-party"]}
    if kind == DATASTORE:
        return {"id": f"data_store:{technology}", "kind": "data_store", "name": label, "technology": technology}
    if kind == QUEUE:
        return {"id": f"data_store:{technology}", "kind": "data_store", "name": label,
                "technology": technology, "tags": ["queue"]}
    raise ValueError(f"tipo de nó desconhecido: {kind!r}")


def _notes(values: list[str]) -> list[dict]:
    unique= list(dict.fromkeys(values))[:MAX_NOTE_EVIDENCE]
    return [{"note": f"seen via {value}"} for value in unique] or [{"note": "found in source code analysis"}]


def to_schema(graph: dict, owner: str, repo: str, group_by: str) -> dict:
    """graph: resultado de build_dfd() com a chave "stats" já preenchida."""
    identities= {n["id"]: _identity(n) for n in graph["nodes"]}
    new_id= lambda old: identities[old]["id"] if old in identities else old

    # origem das etiquetas de evidência dos nós que não são processos: as ligações que lá chegam ou saem
    seen_via: dict[str, list[str]]= {}
    for e in graph["edges"]:
        for end in (e["source"], e["target"]):
            if identities.get(end, {}).get("kind") != "process":
                seen_via.setdefault(end, []).extend(e["data"].get("via", []))

    nodes= []
    for old in graph["nodes"]:
        ident= identities[old["id"]]
        data= old["data"]
        if ident["kind"] == "process":
            evidence= [{"file": f} for f in data.get("files", [])[:MAX_FILE_EVIDENCE]] or [{"note": "directory with source files"}]
            confidence= "high"
            extra= {k: v for k, v in data.items() if k not in {"label", "kind", "path"}}
        else:
            evidence= _notes(seen_via.get(old["id"], []))
            confidence= "medium"
            extra= {"url": data["url"]} if "url" in data else {}
        nodes.append({
            "id": ident["id"], "kind": ident["kind"], "name": ident["name"],
            "technology": ident.get("technology"), "tags": ident.get("tags", []),
            "parent": None, "path": ident.get("path"),
            "provenance": {"source": "code", "confidence": confidence, "evidence": evidence},
            "extra": extra,
        })

    edges= []
    for old in graph["edges"]:
        source, target= new_id(old["source"]), new_id(old["target"])
        data= old["data"]
        evidence= _notes(data.get("via", []))
        if group_by == "file" and identities.get(old["source"], {}).get("kind") == "process":
            evidence= [{"file": identities[old["source"]]["path"]}, *evidence]
        extra= {k: data[k] for k in ("count", "via", "routes") if k in data}
        edges.append({
            "id": f"{source}->{target}:{old['label']}", "source": source, "target": target, "label": old["label"],
            "operations": data.get("operations", []), "protocol": None,
            "provenance": {"source": "code", "confidence": "medium", "evidence": evidence},
            "extra": extra,
        })

    flows= []
    for flow in graph["flows"]:
        flows.append({
            **flow,
            "process": new_id(flow["process"]),
            "resources": [{**r, "id": new_id(r["id"]), "path": [new_id(step) for step in r["path"]]} for r in flow["resources"]],
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "repository": {"owner": owner, "repo": repo, "ref": None},
        "level": "detail",
        "nodes": nodes,
        "edges": edges,
        "meta": {"producer": "backend-detail", "generated_at": datetime.now(timezone.utc), "group_by": group_by, "stats": graph["stats"]},
        "extra": {"flows": flows},
    }
