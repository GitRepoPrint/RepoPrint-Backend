"""Carregador do catálogo de regras e categorias LINDDUN."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List


@lru_cache(maxsize=1)
def load_catalog() -> Dict[str, Any]:
    catalog_path = Path(__file__).with_name("linddun_catalog.json")
    if not catalog_path.exists():
        # Fallback para o directório original se não estiver local
        fallback = Path(__file__).resolve().parent.parent.parent / "engine" / "linddun" / "linddun_catalog.json"
        catalog_path = fallback

    with open(catalog_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_categories() -> Dict[str, str]:
    return load_catalog().get("categories", {})


def get_dfd_properties() -> Dict[str, str]:
    return load_catalog().get("dfd_properties", {})


def get_data_types() -> List[str]:
    return load_catalog().get("data_types", [])


def get_rules() -> List[Dict[str, Any]]:
    return load_catalog().get("rules", [])
