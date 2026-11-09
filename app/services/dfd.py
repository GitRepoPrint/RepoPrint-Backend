import os
import posixpath
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse

from app.services.code_fetcher import SourceFile
from app.services.flows import FlowAnalyzer
from app.services.resolver import PathIndex as _Index
from app.services.code_parser import ParsedFile, parse_file
from app.services.dfd_catalog import DATASTORE, ENTRYPOINT, EXTERNAL, QUEUE, classify as _classify

CLIENT_ID= "entity:client"
RESOURCE_LABELS= {
    "script": "loads script", "iframe": "embeds iframe", "embed": "embeds content", "object": "embeds content",
    "link": "loads stylesheet/resource", "img": "loads media", "source": "loads media", "video": "loads media",
    "audio": "loads media", "form": "form posts to",
}
EDGE_LABELS= {DATASTORE: "reads/writes", EXTERNAL: "calls", QUEUE: "publishes/consumes"}
MAX_NODE_ROUTES= 100
MAX_FLOWS= 500
MAX_FLOW_RESOURCES= 30


def parse_files(files: list[SourceFile]) -> tuple[list[ParsedFile], dict]:
    parsed: list[ParsedFile]= []
    failed: dict[str, str]= {}
    for file in files:
        if file.language in failed:
            continue
        try:
            parsed.append(parse_file(file.path, file.language, file.source))
        except Exception as e:
            # linguagem sem parser disponível (por exemplo sem rede para o descarregar)
            failed[file.language]= f"{type(e).__name__}: {e}"
    return parsed, failed


HOST_RE= re.compile(r"^[a-z0-9_]([a-z0-9_.-]*[a-z0-9_])?$")
# Máquinas que aparecem em código como exemplo ou marcador de posição, não como sistemas reais
PLACEHOLDER_HOSTS= {
    "localhost", "0.0.0.0", "foo.com", "bar.com", "foobar.com", "domain.com", "mydomain.com", "yourdomain.com",
    "your-domain.com", "mysite.com", "yoursite.com", "website.com", "site.com", "test.com", "host.com",
}
PLACEHOLDER_WORDS= ("tobereplaced", "replaceme", "changeme", "placeholder", "yourhost", "yourserver", "your-")
EXAMPLE_DOMAINS= ("example.com", "example.org", "example.net")  # reservados pela RFC 2606, com subdomínios
RESERVED_TLDS= (".example", ".test", ".invalid", ".localhost", ".localdomain")
# Sítios para onde o código só aponta em comentários, mensagens ou configuração de documentação
REFERENCE_HOSTS= {
    "github.com", "gitlab.com", "bitbucket.org", "stackoverflow.com", "stackexchange.com", "pypi.org", "npmjs.com",
    "www.npmjs.com", "crates.io", "docs.rs", "pkg.go.dev", "rubygems.org", "packagist.org", "wikipedia.org",
    "medium.com", "discord.gg", "gitter.im", "twitter.com", "x.com", "linkedin.com", "creativecommons.org",
    "w3.org", "schema.org", "json-schema.org", "opensource.org", "gnu.org", "apache.org", "readthedocs.io",
    "readthedocs.org", "docs.python.org", "developer.mozilla.org", "docs.djangoproject.com", "tools.ietf.org",
    "datatracker.ietf.org", "rfc-editor.org",
}


def _is_reference_host(host: str) -> bool:
    # api.github.com é uma API de verdade, por isso o github.com só conta ele próprio (e o www)
    if host in {"github.com", "www.github.com"}:
        return True
    return any(host == h or host.endswith("." + h) for h in REFERENCE_HOSTS if h != "github.com")


def _access_label(category: str, ops: set[str] | None) -> str:
    """Etiqueta da aresta processo -> recurso conforme o que o código lá faz; sem prova, a etiqueta genérica."""
    real= (ops or set()) & {"read", "write"}
    if category == DATASTORE:
        if real == {"read"}:
            return "reads"
        if real == {"write"}:
            return "writes"
        return "reads/writes" if real or not ops else "accesses"
    if category == QUEUE:
        if real == {"write"}:
            return "publishes"
        return "consumes" if real == {"read"} else "publishes/consumes"
    return EDGE_LABELS[category]


def _form_label(form: dict) -> str:
    if form["method"] == "JS":
        return f"onSubmit handled in JavaScript{' -> ' + form['action'] if form['action'] else ''}"
    return f"{form['method']} {form['action'] or '(same page)'}"


def _host(url: str, *, references: bool= False) -> str | None:
    """Máquina de um URL absoluto (http, https ou //host); None para caminhos relativos, marcadores de posição
    e, com references=True, sítios de documentação que um URL numa string quase de certeza só referencia."""
    if url.startswith("//"):
        url= "https:" + url
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        host= (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not host or not HOST_RE.match(host) or host.startswith("127."):
        return None
    if host in PLACEHOLDER_HOSTS or host.endswith(RESERVED_TLDS) or any(w in host for w in PLACEHOLDER_WORDS):
        return None
    if any(host == d or host.endswith("." + d) for d in EXAMPLE_DOMAINS):
        return None
    if references and _is_reference_host(host):
        return None
    return host


def build_dfd(parsed: list[ParsedFile], group_by: str= "directory") -> dict:
    def group(path: str) -> str:
        if group_by == "file":
            return path
        return posixpath.dirname(path) or "(root)"

    # uma chamada com cara de rota só conta se o repositório usa alguma framework web
    repo_context= any(p.web_context for p in parsed)
    visible_routes= {
        p.path: [{k: v for k, v in r.items() if k != "unconfirmed"} for r in p.routes if repo_context or not r.get("unconfirmed")]
        for p in parsed
    }

    index= _Index([p.path for p in parsed])
    analyzer= FlowAnalyzer(parsed, index, lambda url: _host(url, references=True))
    group_ops: dict[tuple[str, str], set[str]]= defaultdict(set)
    for (path, resource_id), ops in analyzer.access_ops().items():
        group_ops[(group(path), resource_id)] |= ops

    nodes: dict[str, dict]= {}
    edges: dict[tuple[str, str, str], dict]= {}
    linked: set[tuple[str, str]]= set()

    def node(node_id: str, kind: str, label: str, **data):
        nodes.setdefault(node_id, {"id": node_id, "type": kind, "data": {"label": label, "kind": kind, **data}, "position": {"x": 0, "y": 0}})

    def edge(source: str, target: str, label: str, via: str):
        if source == target:
            return None
        linked.add((source, target))
        item= edges.setdefault((source, target, label), {
            "id": f"{source}->{target}:{label}", "source": source, "target": target, "label": label,
            "data": {"count": 0, "via": []},
        })
        item["data"]["count"] += 1
        if via not in item["data"]["via"] and len(item["data"]["via"]) < 10:
            item["data"]["via"].append(via)
        return item

    members: dict[str, list[ParsedFile]]= defaultdict(list)
    for p in parsed:
        members[group(p.path)].append(p)

    for gid, files in members.items():
        languages= Counter(f.language for f in files)
        definitions= [d for f in files for d in f.definitions]
        page= {
            "forms": sum(len(f.html.forms) for f in files),
            "inputs": sum(f.html.inputs for f in files),
            "inline_scripts": sum(f.html.inline_scripts for f in files),
            "inline_handlers": sum(f.html.inline_handlers for f in files),
            "external_resources": sum(1 for f in files for r in f.html.resources if _host(r["url"])),
        }
        endpoints= [{**{k: v for k, v in r.items() if k != "span"}, "file": f.path} for f in files for r in visible_routes[f.path]]
        node(gid, "process", gid.rsplit("/", 1)[-1] if group_by == "directory" else posixpath.basename(gid),
             path=gid, files=[f.path for f in files][:50], file_count=len(files), languages=dict(languages),
             definitions=[f"{d['kind']} {d['name']}" for d in definitions[:15]], definition_count=len(definitions),
             **({"html": page} if any(page.values()) else {}),
             **({"routes": endpoints[:MAX_NODE_ROUTES], "route_count": len(endpoints)} if endpoints else {}))

    for p in parsed:
        source= group(p.path)
        for module in p.imports:
            known= _classify(module)
            # "import celery" dentro de uma pasta chamada celery continua a ser a biblioteca
            bare= not re.search(r"[./:\\]", module)
            internal= None if (known and bare) else index.resolve(module, p.path)
            if internal:
                edge(source, group(internal), "imports", module)
                continue
            kind= known
            if kind is None:
                if target := index.resolve_by_owner_prefix(module, p.path):
                    edge(source, group(target), "imports", module)
                continue
            category, label= kind
            if category == ENTRYPOINT:
                node(CLIENT_ID, "entity", "User / Client")
                edge(CLIENT_ID, source, "HTTP requests", module)
            else:
                node_id= f"{category}:{label}"
                node(node_id, category, label)
                ops= group_ops.get((source, node_id))
                item= edge(source, node_id, _access_label(category, ops), module)
                if item is not None and ops:
                    item["data"]["operations"]= sorted({*item["data"].get("operations", []), *ops})

        if visible_routes[p.path]:
            node(CLIENT_ID, "entity", "User / Client")
            labels= sorted({f"{r['method']} {r['path']}" for r in visible_routes[p.path]})
            item= edge(CLIENT_ID, source, "HTTP requests", labels[0])
            if item is not None:
                known= item["data"].setdefault("routes", [])
                item["data"]["routes"]= sorted({*known, *labels})[:MAX_NODE_ROUTES * 2]

        for form in p.html.forms:
            node(CLIENT_ID, "entity", "User / Client")
            edge(CLIENT_ID, source, "submits form", _form_label(form))

        for resource in p.html.resources:
            tag, url= resource["tag"], resource["url"]
            host= _host(url)
            if host:
                node(f"external:{host}", EXTERNAL, host, url=url)
                edge(source, f"external:{host}", RESOURCE_LABELS[tag], url)
            elif tag == "script" and (target := index.resolve(url.split("?")[0].split("#")[0], p.path)):
                edge(source, group(target), RESOURCE_LABELS[tag], url)

        for form in p.html.forms:
            if host := _host(form["action"]):
                node(f"external:{host}", EXTERNAL, host, url=form["action"])
                edge(source, f"external:{host}", RESOURCE_LABELS["form"], f"{form['method']} {form['action']}")

        # em JSX o URL de um <script src="..."> também é uma string; já foi contado como recurso da página
        claimed= {r["url"] for r in p.html.resources} | {f["action"] for f in p.html.forms}
        for url in p.urls:
            if url in claimed:
                continue
            host= _host(url, references=True)
            if not host:
                continue
            node(f"external:{host}", EXTERNAL, host, url=url)
            edge(source, f"external:{host}", "calls", url)

    flows= []
    for found in analyzer.flows(visible_routes)[:MAX_FLOWS]:
        route, handler, start= found["route"], found["handler"], group(found["file"])
        resources= []
        for resource_id, entry in sorted(found["reach"].items())[:MAX_FLOW_RESOURCES]:
            resource= entry["resource"]
            node(resource_id, resource.kind, resource.label)
            hops= [start]
            for path in [found["file"], *entry["chain"]]:
                if group(path) != hops[-1]:
                    hops.append(group(path))
            steps= [*hops, resource_id]
            for source, target in zip(steps, steps[1:]):
                if (source, target) not in linked:
                    label= _access_label(resource.kind, entry["ops"]) if target == resource_id else "calls"
                    edge(source, target, label, f"{route['method']} {route['path']}")
            site_file, site_line, site_call= entry["site"]
            # "access" só diz algo quando não se sabe se é leitura ou escrita
            ops= entry["ops"] - {"access"} if entry["ops"] & {"read", "write"} else entry["ops"]
            resources.append({
                "id": resource_id, "kind": resource.kind, "label": resource.label, "operations": sorted(ops),
                "path": steps, "site": {"file": site_file, "line": site_line, "call": site_call},
            })
        flows.append({
            "id": f"{route['method']} {route['path']} [{found['file']}]", "route": f"{route['method']} {route['path']}",
            "method": route["method"], "path": route["path"], "handler": found["handler_name"], "file": found["file"],
            "process": start, "resources": resources,
        })

    for item in edges.values():
        item["data"]["via"].sort()
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "flows": flows}
