# Mapeia o nome de um módulo importado para o tipo de elemento externo que ele representa num DFD.
# A correspondência é por prefixo, sempre seguida de um separador (., /, :), por isso "requests"
# apanha "requests.adapters" mas não "requests_oauthlib".
#
# Os dados vivem em dfd_catalog.json, partilhado com o overview do frontend (ver docs/dfd-schema.md).

import json
from pathlib import Path

DATASTORE= "datastore"
EXTERNAL= "external"
QUEUE= "queue"
ENTRYPOINT= "entrypoint"

CATEGORIES= {DATASTORE, EXTERNAL, QUEUE, ENTRYPOINT}

CATALOG: dict[str, tuple[str, str]]= {}
TECHNOLOGY: dict[tuple[str, str], str]= {}  # (categoria, etiqueta) -> slug usado nos ids do schema


def _load():
    data= json.loads(Path(__file__).with_name("dfd_catalog.json").read_text(encoding="utf8"))
    for entry in data["entries"]:
        category, label= entry["category"], entry["label"]
        if category not in CATEGORIES:
            raise ValueError(f"categoria desconhecida no catálogo: {category!r}")
        TECHNOLOGY[(category, label)]= entry["technology"]
        for module in entry["modules"]:
            CATALOG[module.lower()]= (category, label)


_load()


def classify(module: str) -> tuple[str, str] | None:
    lowered= module.lower()
    for key, value in CATALOG.items():
        if lowered == key or (lowered.startswith(key) and lowered[len(key)] in "./:\\"):
            return value
    return None
