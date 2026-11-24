"""Motor e endpoints de análise de privacidade LINDDUN."""
from app.engines.linddun.catalog import (
    get_categories,
    get_data_types,
    get_dfd_properties,
    get_rules,
    load_catalog,
)
from app.engines.linddun.engine import LinddunEngine
from app.engines.linddun.mapper import map_dfd_to_linddun
from app.engines.linddun.router import router
from app.engines.linddun.schemas import (
    LinddunCategory,
    LinddunFinding,
    LinddunReport,
    LinddunSeverity,
    LinddunSummary,
    TargetType,
)

__all__ = [
    "LinddunEngine",
    "map_dfd_to_linddun",
    "load_catalog",
    "get_categories",
    "get_rules",
    "get_dfd_properties",
    "get_data_types",
    "router",
    "LinddunReport",
    "LinddunFinding",
    "LinddunSummary",
    "LinddunCategory",
    "LinddunSeverity",
    "TargetType",
]
