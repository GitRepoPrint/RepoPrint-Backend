"""Deteção de rotas HTTP (pontos de entrada) a partir da árvore do tree-sitter.

Há dois estilos: anotações/decoradores (Spring, JAX-RS, NestJS, ASP.NET, FastAPI, Flask, actix, Rocket, Symfony)
e chamadas de registo (Express, Gin, Laravel, Rails, Django, minimal APIs, Axum). As anotações têm todas a forma
textual "@Nome(args)", por isso são lidas pelo texto e o método/classe alvo vem da árvore.
"""
import re

from tree_sitter import Node

from app.services.dfd_catalog import ENTRYPOINT, classify

VERBS= {"get", "post", "put", "delete", "patch", "head", "options"}
ANNOTATION_NODES= {"annotation", "marker_annotation", "decorator", "attribute_item"}
ATTRIBUTE_LANGUAGES= {"csharp", "php"}
ANNOTATION_WRAPPERS= {"modifiers", "attribute_list", "attribute_group", "annotation_list"}
CLASS_KINDS= {"class_declaration", "class_definition", "struct_item", "interface_declaration", "record_declaration", "object_declaration"}
TARGET_KINDS= CLASS_KINDS | {"method_declaration", "method_definition", "function_definition", "function_declaration", "function_item", "constructor_declaration"}

SPRING= {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}
NEST= {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH", "Head": "HEAD", "Options": "OPTIONS", "All": "ANY"}
JAXRS= {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
ASPNET= {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT", "HttpDelete": "DELETE", "HttpPatch": "PATCH", "HttpHead": "HEAD", "HttpOptions": "OPTIONS"}
PREFIX_ANNOTATIONS= {"RequestMapping", "Path", "Controller", "Route", "RoutePrefix", "Prefix"}
PYTHON_ROUTE_NAMES= {"route", "api_route", "websocket", "websocket_route"}

# bibliotecas cliente: "axios.get('/x', ...)" parece um registo de rota mas é um pedido
CLIENT_RECEIVERS= {
    "axios", "http", "https", "request", "requests", "superagent", "fetch", "client", "ky", "got", "$", "jquery",
    "$http", "cy", "page", "browser", "wretch", "httpx", "session", "urllib", "rest", "resttemplate", "webclient",
    "supertest", "agent", "api_client", "apiclient", "testclient", "self.client", "http_client", "httpclient",
}
TEST_PATH_RE= re.compile(r"(^|/)(tests?|__tests__|specs?|e2e|testing|__mocks__)/|(^|/)(test_[^/]*|[^/]*_test\.\w+|[^/]*\.(test|spec)\.\w+|[^/]*(Test|Tests|IT)\.(java|kt|cs|scala))$")
STRING_RE= re.compile(r"""(?:"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)')""")
ANNOTATION_RE= re.compile(r"^(?:#!?\[|@|\[)?\s*([\w.:]+)\s*(?:\((.*)\))?\s*\]?$", re.S)
MAX_ROUTES= 500


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH_RE.search(path))


def _text(node: Node) -> str:
    return node.text.decode("utf8", errors="replace") if node.text else ""


def _strings(text: str) -> list[str]:
    return [a or b for a, b in STRING_RE.findall(text)]


def join_path(*parts: str) -> str:
    segments= [p.strip("/") for p in parts if p and p.strip("/")]
    return "/" + "/".join(segments)


def web_context(imports) -> bool:
    return any((c := classify(m)) and c[0] == ENTRYPOINT for m in imports) or "net/http" in imports


# ---------------------------------------------------------------- anotações

def is_annotation(node: Node, language: str) -> bool:
    return node.type in ANNOTATION_NODES or (node.type == "attribute" and language in ATTRIBUTE_LANGUAGES)


def _parse_annotation(node: Node) -> tuple[str, str] | None:
    match= ANNOTATION_RE.match(_text(node).strip())
    return (match.group(1), match.group(2) or "") if match else None


def _target(node: Node, language: str) -> Node | None:
    """Declaração (método, função ou classe) que a anotação decora."""
    parent= node.parent
    while parent is not None and parent.type in ANNOTATION_WRAPPERS:
        node, parent= parent, parent.parent
    if parent is None:
        return None
    if parent.type in TARGET_KINDS:
        return parent
    if parent.type == "decorated_definition":
        return parent.child_by_field_name("definition")
    # decoradores de TypeScript e atributos de Rust são irmãos que precedem a declaração
    sibling= node.next_named_sibling
    while sibling is not None and (is_annotation(sibling, language) or sibling.type == "comment"):
        sibling= sibling.next_named_sibling
    return sibling if sibling is not None and sibling.type in TARGET_KINDS else None


def _collect_annotations(container: Node, language: str, out: list[Node]):
    for child in container.children:
        if is_annotation(child, language):
            out.append(child)
        elif child.type in ANNOTATION_WRAPPERS:
            _collect_annotations(child, language, out)


def _annotations_of(target: Node, language: str) -> list[tuple[str, str]]:
    found: list[Node]= []
    _collect_annotations(target, language, found)
    parent= target.parent
    if parent is not None and parent.type in ("decorated_definition", "export_statement"):
        _collect_annotations(parent, language, found)
    sibling= target.prev_named_sibling
    while sibling is not None and (is_annotation(sibling, language) or sibling.type == "comment"):
        if is_annotation(sibling, language):
            found.append(sibling)
        sibling= sibling.prev_named_sibling
    return [a for n in found if (a := _parse_annotation(n))]


def _first_path(args: str) -> str | None:
    named= re.search(r"""(?:value|path|template)\s*[=:]\s*(\{[^}]*\}|\[[^\]]*\]|"[^"]*"|'[^']*')""", args)
    positional= re.match(r"""\s*(\{[^}]*\}|\[[^\]]*\]|"[^"]*"|'[^']*')""", args)
    found= _strings((named or positional).group(1)) if (named or positional) else []
    return found[0] if found else None


def _methods_in(args: str) -> list[str]:
    spring= re.findall(r"RequestMethod\.(\w+)", args)
    listed= re.search(r"methods?\s*[=:]\s*[\[{(]([^\]})]*)[\]})]", args)
    if listed:
        spring += [m.upper() for m in _strings(listed.group(1)) or re.findall(r"\w+", listed.group(1))]
    single= re.search(r"""method\s*=\s*["'](\w+)["']""", args)
    if single:
        spring.append(single.group(1).upper())
    return [m.upper() for m in spring]


def _owner_class(target: Node) -> Node | None:
    node= target.parent
    while node is not None:
        if node.type in CLASS_KINDS:
            return node
        node= node.parent
    return None


def _class_prefix(target: Node, language: str) -> str:
    owner= _owner_class(target)
    if owner is None:
        return ""
    for name, args in _annotations_of(owner, language):
        if name.split(".")[-1].split("::")[-1] in PREFIX_ANNOTATIONS:
            prefix= _first_path(args)
            if prefix:
                name_of= owner.child_by_field_name("name")
                controller= re.sub(r"Controller$", "", _text(name_of)) if name_of is not None else ""
                return prefix.replace("[controller]", controller.lower())
    return ""


def annotation_routes(node: Node, language: str, seen: set) -> list[dict]:
    """Rotas que a anotação (e as restantes do mesmo método) declara. Cada método só é processado uma vez."""
    target= _target(node, language)
    if target is None or (target.start_byte, target.end_byte) in seen:
        return []
    seen.add((target.start_byte, target.end_byte))
    if target.type in CLASS_KINDS:
        return []

    name_node= target.child_by_field_name("name")
    handler= _text(name_node) if name_node is not None else ""
    line= target.start_point[0] + 1
    annotations= _annotations_of(target, language)
    prefix= _class_prefix(target, language)
    sibling_path= next((_first_path(a) for n, a in annotations if n.split(".")[-1].split("::")[-1] in {"Path", "Route"}), None)

    routes: list[dict]= []

    def add(methods: list[str], path: str | None, gated: bool):
        full= join_path(prefix, path if path is not None else sibling_path or "").replace("[action]", handler.lower())
        for method in methods or ["ANY"]:
            routes.append({"method": method, "path": full, "handler": handler, "line": line, "gated": gated,
                           "span": (target.start_byte, target.end_byte)})

    for full_name, args in annotations:
        base= full_name.replace("::", ".").split(".")[-1]
        has_receiver= "." in full_name.replace("::", ".")
        if base in SPRING:
            add([SPRING[base]], _first_path(args), False)
        elif base == "RequestMapping":
            add(_methods_in(args), _first_path(args), False)
        elif base in ASPNET:
            add([ASPNET[base]], _first_path(args), False)
        elif base in JAXRS and not args.strip():  # com argumentos seria um cliente Retrofit
            add([base], None, False)
        elif base in NEST and language in ("typescript", "tsx", "javascript", "java", "kotlin"):
            add([NEST[base]], _first_path(args) or "", False)
        elif base == "Route" and target.type not in CLASS_KINDS and not any(n in ASPNET for n, _ in annotations):
            add(_methods_in(args), _first_path(args), False)
        elif base.islower() and base in VERBS and _first_path(args) is not None:
            add([base.upper()], _first_path(args), True)  # FastAPI, Flask 2, Litestar, actix, Rocket
        elif base in PYTHON_ROUTE_NAMES and has_receiver and _first_path(args) is not None:
            add(_methods_in(args) or (["WS"] if "websocket" in base else ["GET"]), _first_path(args), True)
    return routes


# ----------------------------------------------------------------- chamadas

def _args(node: Node) -> list[Node]:
    container= node.child_by_field_name("arguments") or next((c for c in node.children if "argument" in c.type), None)
    if container is None:
        return []
    args= [c for c in container.children if c.is_named and c.type != "comment"]
    # C#, PHP e Kotlin embrulham cada argumento num nó "argument"
    return [c.named_children[0] if c.type in ("argument", "value_argument") and c.named_child_count == 1 else c for c in args]


HANDLER_NODES= {
    "arrow_function", "function_expression", "function", "func_literal", "lambda", "lambda_expression",
    "anonymous_function", "anonymous_function_creation_expression", "closure_expression", "lambda_literal",
}
BLOCK_NODES= {"do_block", "block", "lambda"}  # Sinatra/Rails: get "/x" do ... end


def _handler_span(node: Node, args: list[Node]) -> tuple[int, int] | None:
    """Corpo do handler quando é uma função anónima passada à chamada (Express, Gin, Laravel, Sinatra...)."""
    for arg in reversed(args):
        if arg.type in HANDLER_NODES:
            return (arg.start_byte, arg.end_byte)
    block= next((c for c in node.children if c.type in BLOCK_NODES), None)
    return (block.start_byte, block.end_byte) if block is not None else None


def _string_arg(arg: Node) -> str | None:
    if "string" not in arg.type:
        return None
    found= _strings(_text(arg))
    if found:
        return found[0]
    inner= _text(arg).strip("\"'`")
    return inner


def _handler_name(args: list[Node], minimum: int= 2) -> str:
    if len(args) < minimum:
        return ""
    last= _text(args[-1]).strip().strip("\"'")
    last= re.sub(r"\.as_view\(.*\)$", "", last)
    return last if re.fullmatch(r"[\w.:\\@\[\]'\"]+", last) and len(last) < 80 else ""


def call_routes(node: Node, callee: str, language: str, path: str) -> list[dict]:
    args= _args(node)
    if not args:
        return []
    first= _string_arg(args[0])
    name= re.split(r"[.:]+", callee)[-1]
    receiver= re.sub(r"\(.*?\)", "", callee[: max(len(callee) - len(name), 0)]).rstrip(".:")
    receiver_name= receiver.split(".")[-1].split("::")[-1].lower()
    line= node.start_point[0] + 1
    lowered= name.lower()

    span= _handler_span(node, args)

    def route(method: str, route_path: str, handler: str= "", gated: bool= True):
        return {"method": method, "path": join_path(route_path), "handler": handler, "line": line, "gated": gated, "span": span}

    # app.get("/x", handler), r.GET("/x", h), Route::get("/x", ...)
    if (lowered in VERBS or lowered in ("all", "any")) and first is not None:
        laravel= callee.startswith(("Route::", "Route."))
        slashless= laravel or path.endswith("routes.rb")
        if receiver_name in CLIENT_RECEIVERS or receiver.lower() in CLIENT_RECEIVERS:
            return []
        if not (first.startswith(("/", "*")) or first == "" or slashless):
            return []
        if len(args) < 2 and language not in ("ruby",):
            return []
        method= "ANY" if lowered in ("all", "any") else lowered.upper()
        return [route(method, first, _handler_name(args), gated=not (laravel or path.endswith("routes.rb")))]

    if re.fullmatch(r"Map(Get|Post|Put|Delete|Patch)", name) and first is not None:  # ASP.NET minimal API
        return [route(name[3:].upper(), first, _handler_name(args), gated=False)]

    if name in ("HandleFunc", "Handle") and first is not None:  # net/http, gorilla/mux, chi
        method, _, route_path= first.rpartition(" ") if " " in first else ("", "", first)
        if route_path.startswith("/"):
            return [route(method.upper() or "ANY", route_path, _handler_name(args))]

    if name in ("add_url_rule", "add_api_route", "add_route", "add_api_websocket_route") and first and first.startswith("/"):
        methods= _methods_in(_text(node)) or ["GET"]
        return [route(m, first, _handler_name(args)) for m in methods]

    if callee in ("path", "re_path", "url") and first is not None and language == "python":  # Django
        second= _text(args[1]) if len(args) > 1 else ""
        if second.startswith("include("):
            return []
        pattern= re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", first.lstrip("^").rstrip("$"))
        pattern= re.sub(r"/\?$", "", pattern)
        return [{**route("ANY", pattern, _handler_name(args)), "gated": False, "django": True}]

    if callee in ("resources", "resource") and language == "ruby" and path.endswith("routes.rb"):  # Rails
        symbol= _text(args[0]).lstrip(":").strip("\"'")
        return [route("ANY", symbol, "", gated=False)] if symbol else []

    if name == "route" and language == "rust" and first and first.startswith("/") and len(args) > 1:  # Axum
        methods= re.findall(r"\b(get|post|put|delete|patch|head|options)\s*\(\s*([\w:]+)", _text(args[1]))
        return [route(m.upper(), first, h) for m, h in methods]

    if name == "route" and "object" in args[0].type:  # fastify/hapi: route({ method, url, handler })
        body= _text(args[0])
        found= re.search(r"""(?:url|path)\s*:\s*["']([^"']+)["']""", body)
        methods= re.search(r"""method\s*:\s*(\[[^\]]*\]|["']\w+["'])""", body)
        if found:
            listed= [m.upper() for m in _strings(methods.group(1))] if methods else []
            return [route(m, found.group(1)) for m in listed or ["ANY"]]

    chained= re.search(r"""\.route\(["']([^"']+)["']\)(?:\.\w+\([^()]*\))*\.(\w+)$""", callee)  # app.route("/x").get(a).post(b)
    if chained and chained.group(2).lower() in VERBS:
        return [route(chained.group(2).upper(), chained.group(1), _handler_name(args, 1))]
    return []


def finalise(routes: list[dict], imports, language: str) -> list[dict]:
    """Remove duplicados. As rotas que dependem de uma framework web ficam marcadas como "unconfirmed" se o
    ficheiro não a importa: quem decide é o repositório (um ficheiro de rotas muitas vezes só recebe o `app`)."""
    context= web_context(imports)
    django= any(m.startswith("django") for m in imports)
    best: dict[tuple[str, str], dict]= {}
    for item in routes:
        if item.get("django") and not django:
            continue
        key= (item["method"], item["path"])
        current= best.get(key)
        # a mesma rota vista como decorador e como chamada: mantém a que sabe o handler
        if current is None or (not current["handler"] and item["handler"]):
            best[key]= item
    result= []
    for item in sorted(best.values(), key=lambda r: r["line"])[:MAX_ROUTES]:
        clean= {k: v for k, v in item.items() if k not in ("gated", "django")}
        if item["gated"] and not context:
            clean["unconfirmed"]= True
        result.append(clean)
    return result
