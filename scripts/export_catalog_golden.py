"""Gera o ficheiro de paridade do catálogo para o frontend: para muitas entradas, o que o classify() do backend responde.

O teste do frontend (src/lib/dfd/catalog.test.ts) exige que o classify() em TypeScript dê exatamente as mesmas respostas.
Voltar a correr sempre que o catálogo mudar:

    uv run python scripts/export_catalog_golden.py ../RepoPrint-Frontend/src/lib/dfd/__fixtures__/catalog-golden.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.dfd_catalog import CATALOG, TECHNOLOGY, classify  # noqa: E402

EXTRAS= ["", "my_app.models", "requests_oauthlib", "github.com/acme/app/pkg", "./local", "../x", "@aws-sdk/client-s3",
         "org.postgresql.Driver", "ioredis/cluster"]


def probes():
    for key in CATALOG:
        # o próprio nome, um subcaminho, uma continuação sem separador (não deve bater) e maiúsculas
        yield from (key, key + "/x", key + "x", key.upper())
    yield from EXTRAS


def main(target: str):
    cases= []
    for probe in dict.fromkeys(probes()):
        hit= classify(probe)
        cases.append([probe, None if hit is None else [hit[0], hit[1], TECHNOLOGY[hit]]])
    body= "[\n" + ",\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n]\n"
    Path(target).write_text(body, encoding="utf8")
    print(f"{len(cases)} casos escritos em {target}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
