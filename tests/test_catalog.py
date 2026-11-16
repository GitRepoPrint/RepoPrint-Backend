import json
from pathlib import Path

import pytest

from app.services import dfd_catalog
from app.services.dfd_catalog import CATALOG, CATEGORIES, TECHNOLOGY, classify
from app.services.dfd_schema import slug

DATA= json.loads(Path(dfd_catalog.__file__).with_name("dfd_catalog.json").read_text(encoding="utf8"))


def test_the_json_file_is_the_only_source_of_the_catalog():
    assert DATA["version"] == 1
    modules= [m.lower() for e in DATA["entries"] for m in e["modules"]]
    assert len(CATALOG) == len(set(modules)) > 250
    assert {e["category"] for e in DATA["entries"]} <= CATEGORIES


def test_a_module_belongs_to_one_entry_only():
    seen= {}
    for entry in DATA["entries"]:
        for module in entry["modules"]:
            assert seen.setdefault(module.lower(), (entry["category"], entry["label"])) == (entry["category"], entry["label"]), module


def test_every_entry_has_a_unique_technology_slug():
    technologies= [(e["category"] in ("external",), e["technology"]) for e in DATA["entries"]]
    # ids do schema são data_store:<tecnologia> (datastore e queue) ou external_entity:<tecnologia>
    assert len(technologies) == len(set(technologies))
    assert all(e["technology"] == slug(e["label"]) for e in DATA["entries"])
    assert len(TECHNOLOGY) == len(DATA["entries"])


@pytest.mark.parametrize("module, expected", [
    ("psycopg2", ("datastore", "PostgreSQL")),
    ("requests", ("external", "HTTP API")),
    ("requests.adapters", ("external", "HTTP API")),
    ("github.com/lib/pq/stdlib", ("datastore", "PostgreSQL")),
    ("celery:tasks", ("queue", "Message queue")),
    ("Redis", ("datastore", "Redis")),
    ("requests_oauthlib", None),
    ("requestsx", None),
    ("my_app.models", None),
])
def test_matching_is_by_prefix_followed_by_a_separator(module, expected):
    assert classify(module) == expected
