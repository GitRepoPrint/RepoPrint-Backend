import re
from dataclasses import dataclass, field
from functools import lru_cache

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from app.services import routes

# Os nomes dos nós variam de linguagem para linguagem, por isso o parser é guiado por
# conjuntos de tipos de nós em vez de queries por linguagem. Assim qualquer linguagem do
# language pack produz pelo menos imports, definições, chamadas e URLs.
IMPORT_NODES= {
    "import_statement", "import_declaration", "import_from_statement", "future_import_statement",
    "import_spec", "use_declaration", "using_directive", "using_statement", "preproc_include",
    "namespace_use_declaration", "require_expression", "require_once_expression", "include_expression",
    "include_once_expression", "import_directive", "import_clause_statement", "open_statement",
    "extern_crate_declaration", "use_statement", "import_header", "import_list",
}
IMPORT_CALLS= {"require", "require_relative", "require_once", "load", "autoload", "import", "include", "dofile"}
CALL_NODES= {
    "call_expression", "call", "method_invocation", "invocation_expression", "function_call_expression",
    "member_call_expression", "scoped_call_expression", "method_call", "function_call", "command_call",
    "object_creation_expression", "new_expression", "macro_invocation", "call_instruction",
}
DEFINITION_NODES= {
    "function_definition": "function", "function_declaration": "function", "function_item": "function",
    "method_definition": "method", "method_declaration": "method", "method": "method", "function": "function",
    "arrow_function": None, "class_definition": "class", "class_declaration": "class", "class": "class",
    "struct_item": "class", "struct_specifier": "class", "interface_declaration": "class", "trait_item": "class",
    "impl_item": "class", "module": None, "def": "function", "defn": "function", "func_literal": None,
    "constructor_declaration": "method", "protocol_declaration": "class", "object_declaration": "class",
    "enum_declaration": "class", "record_declaration": "class", "singleton_method": "method",
    "fun_declaration": "function", "function_signature": "function", "subroutine": "function",
}
URL_RE= re.compile(r"""https?://[^\s"'`<>\\)\]}]+""")
MAX_STRING_BYTES= 2000
MAX_CALL_TEXT= 120


# Atributo de cada tag que carrega código ou conteúdo de outro sítio
RESOURCE_ATTRS= {
    "script": "src", "iframe": "src", "embed": "src", "object": "data", "link": "href",
    "img": "src", "source": "src", "video": "src", "audio": "src",
}
INPUT_TAGS= {"input", "textarea", "select"}
NON_JS_SCRIPT_TYPES= {"application/json", "application/ld+json", "text/template", "text/x-template", "importmap", "speculationrules"}
EVENT_HANDLER_RE= re.compile(r"^on[a-z]{3,}$")
MAX_HTML_ITEMS= 200

# Linguagens de templates e PHP guardam o HTML como texto simples, por isso usam um regex
TEMPLATE_LANGUAGES= {
    "htmldjango", "jinja2", "twig", "liquid", "blade", "razor", "rshtml", "gotmpl", "heex", "eex", "tera",
    "vento", "embeddedtemplate", "templ", "fusion", "glimmer", "php", "slim", "haml", "pug", "cfml",
}
TAG_RE= re.compile(r"<\s*(form|script|iframe|embed|object|link|img|source|video|audio|input|textarea|select)\b([^>]*)>", re.I)
ATTR_RE= re.compile(r"""([\w:@.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""")
SCRIPT_BLOCK_RE= re.compile(r"<\s*script\b([^>]*)>(.*?)<\s*/\s*script\s*>", re.I | re.S)


@dataclass
class HtmlInfo:
    forms: list[dict]= field(default_factory=list)
    resources: list[dict]= field(default_factory=list)
    inline_scripts: int= 0
    inline_handlers: int= 0
    inputs: int= 0

    @property
    def empty(self) -> bool:
        return not (self.forms or self.resources or self.inline_scripts or self.inline_handlers or self.inputs)


@dataclass
class ParsedFile:
    path: str
    language: str
    imports: list[str]= field(default_factory=list)
    definitions: list[dict]= field(default_factory=list)
    calls: list[str]= field(default_factory=list)
    urls: list[str]= field(default_factory=list)
    html: HtmlInfo= field(default_factory=HtmlInfo)
    routes: list[dict]= field(default_factory=list)
    web_context: bool= False
    has_errors: bool= False


@lru_cache(maxsize=None)
def _parser(language: str):
    # o language pack descarrega o parser na primeira vez que a linguagem é usada
    return get_parser(language)


def _text(node: Node) -> str:
    return node.text.decode("utf8", errors="replace") if node.text else ""


def _string_value(text: str) -> str | None:
    match= re.search(r"""["'`]([^"'`]+)["'`]""", text)
    return match.group(1) if match else None


def _module_of_import(node: Node) -> str | None:
    for field_name in ("module_name", "source", "path", "module"):
        child= node.child_by_field_name(field_name)
        if child is not None:
            text= _text(child)
            return _string_value(text) or text.strip().strip("<>") or None

    text= _text(node).strip()
    quoted= _string_value(text)
    if quoted:
        return quoted
    if text.startswith("#include"):
        match= re.search(r"[<\"]([^>\"]+)[>\"]", text)
        return match.group(1) if match else None

    text= re.sub(r"^\s*(?:pub\s+)?(?:import|from|use|using|open|extern\s+crate|require(?:_once)?|include(?:_once)?)\b\s*", "", text)
    text= re.sub(r"^(?:static|type|global|function|const)\s+", "", text)
    match= re.match(r"[\w.:\\/@$-]+", text)
    if not match:
        return None
    module= match.group(0).rstrip(".:;")
    # "import a.b as c" e "use a::{b, c}" ficam só com o módulo
    return re.sub(r"::\{.*$", "", module) or None


def _callee(node: Node) -> str | None:
    function= node.child_by_field_name("function") or node.child_by_field_name("constructor") or node.child_by_field_name("type")
    if function is None:
        owner= node.child_by_field_name("object") or node.child_by_field_name("receiver") or node.child_by_field_name("scope")
        name= node.child_by_field_name("name") or node.child_by_field_name("method")
        if name is None:
            named= [c for c in node.children if c.is_named]
            if not named:
                return None
            function= named[0]
        else:
            function= name
            if owner is not None:
                return f"{_text(owner)}.{_text(name)}"[:MAX_CALL_TEXT]
    text= re.sub(r"\s+", "", _text(function))
    return text[:MAX_CALL_TEXT] or None


def _first_string_arg(node: Node) -> str | None:
    args= node.child_by_field_name("arguments")
    if args is None:
        args= next((c for c in node.children if "argument" in c.type), None)
    return _string_value(_text(args)[:MAX_STRING_BYTES]) if args is not None else None


@dataclass
class _State:
    imports: dict[str, None]= field(default_factory=dict)
    calls: dict[str, None]= field(default_factory=dict)
    urls: dict[str, None]= field(default_factory=dict)
    definitions: list[dict]= field(default_factory=list)
    html: HtmlInfo= field(default_factory=HtmlInfo)
    saw_tags: bool= False
    path: str= ""
    language: str= ""
    routes: list[dict]= field(default_factory=list)
    route_targets: set= field(default_factory=set)


def _record_tag(info: HtmlInfo, tag: str, attrs: dict[str, str], jsx: bool= False):
    if tag in INPUT_TAGS:
        info.inputs += 1
    if not jsx:  # em JSX o onClick={fn} é uma função normal, não código embutido
        for name, value in attrs.items():
            if EVENT_HANDLER_RE.match(name) or value.strip().lower().startswith("javascript:"):
                info.inline_handlers += 1
    if tag == "form" and len(info.forms) < MAX_HTML_ITEMS:
        action= attrs.get("action", "").strip()
        method= attrs.get("method", "").strip().upper()
        if not method:
            # <form onSubmit={fn}> e <form action={fn}> em React são tratados em JavaScript, não por GET
            method= "JS" if jsx and not action and ("onsubmit" in attrs or "action" in attrs) else "GET"
        info.forms.append({"action": action, "method": method})
    url= attrs.get(RESOURCE_ATTRS.get(tag, ""), "").strip()
    item= {"tag": tag, "url": url}
    if url and item not in info.resources and len(info.resources) < MAX_HTML_ITEMS:
        info.resources.append(item)


def _tag_attributes(tag: Node) -> dict[str, str]:
    attrs: dict[str, str]= {}
    for attribute in tag.children:
        if attribute.type != "attribute":
            continue
        name= next((c for c in attribute.children if c.type == "attribute_name"), None)
        if name is None:
            continue
        value= next((c for c in attribute.children if c.type in ("attribute_value", "quoted_attribute_value")), None)
        attrs[_text(name).lower()]= (_string_value(_text(value)) or _text(value)) if value is not None else ""
    return attrs


def _jsx_value(node: Node | None) -> str:
    """Valor estático de um atributo JSX; vazio se for dinâmico (src={variavel})."""
    if node is None:
        return ""
    text= _text(node).strip()
    if node.type == "jsx_expression":
        text= text[1:-1].strip()
    literal= re.fullmatch(r"""(["'`])(.*)\1""", text, re.S)
    return literal.group(2) if literal else ""


def _jsx_tag(node: Node):
    name= next((c for c in node.children if c.is_named and c.type != "jsx_attribute"), None)
    tag= _text(name) if name is not None else ""
    # só tags HTML (minúsculas) e o <Script> do Next.js; os restantes são componentes próprios
    if tag == "Script":
        return "script"
    return tag if tag.islower() and tag.isalpha() else None


def _jsx_attributes(node: Node) -> dict[str, str]:
    attrs: dict[str, str]= {}
    for attribute in node.children:
        if attribute.type != "jsx_attribute":
            continue
        parts= [c for c in attribute.children if c.is_named]
        if parts:
            attrs[_text(parts[0]).lower()]= _jsx_value(parts[1] if len(parts) > 1 else None) if len(parts) > 1 else ""
    return attrs


def _script_language(attrs: dict[str, str]) -> str | None:
    if attrs.get("type", "").lower() in NON_JS_SCRIPT_TYPES:
        return None
    return "typescript" if attrs.get("lang", "").lower() in ("ts", "typescript") else "javascript"


def _inline_script(state: _State, attrs: dict[str, str], body: bytes, line_offset: int):
    language= _script_language(attrs)
    if language is None:
        return
    state.html.inline_scripts += 1
    if body.strip():
        try:
            _walk(_parser(language).parse(body).root_node, state, line_offset, language)
        except Exception:
            pass


def _is_docstring(node: Node) -> bool:
    # uma string sozinha numa instrução é um docstring (ou um "use strict"), nunca um fluxo de dados
    parent= node.parent
    if parent is None or "string" not in node.type:
        return False
    if parent.type == "expression_statement":
        return parent.named_child_count == 1
    # no Python o docstring aparece como "string" diretamente dentro do bloco ou do módulo
    return node.type == "string" and parent.type in ("block", "module")


def _walk(root: Node, state: _State, line_offset: int= 0, language: str | None= None):
    language= language or state.language
    detect_routes= not routes.is_test_path(state.path)
    stack= [root]
    while stack:
        node= stack.pop()
        if _is_docstring(node):
            continue
        kind= node.type
        stack.extend(reversed(node.children))

        if kind in ("start_tag", "self_closing_tag"):
            state.saw_tags= True
            tag= next((c for c in node.children if c.type == "tag_name"), None)
            if tag is not None:
                _record_tag(state.html, _text(tag).lower(), _tag_attributes(node))
        elif kind in ("jsx_opening_element", "jsx_self_closing_element"):
            tag= _jsx_tag(node)
            if tag:
                _record_tag(state.html, tag, _jsx_attributes(node), jsx=True)
        elif kind == "script_element":
            start= next((c for c in node.children if c.type == "start_tag"), None)
            body= next((c for c in node.children if c.type == "raw_text"), None)
            attrs= _tag_attributes(start) if start is not None else {}
            if "src" not in attrs and body is not None:
                _inline_script(state, attrs, body.text or b"", line_offset + body.start_point[0])
            elif "src" not in attrs:
                state.html.inline_scripts += 1
        elif detect_routes and routes.is_annotation(node, language):
            state.routes.extend(routes.annotation_routes(node, language, state.route_targets))
        elif kind in IMPORT_NODES:
            module= _module_of_import(node)
            if module:
                state.imports[module]= None
        elif kind in CALL_NODES:
            callee= _callee(node)
            if callee:
                if callee.split(".")[-1] in IMPORT_CALLS or callee in IMPORT_CALLS:
                    module= _first_string_arg(node)
                    if module:
                        state.imports[module]= None
                else:
                    state.calls[callee]= None
                    if detect_routes:
                        state.routes.extend(routes.call_routes(node, callee, language, state.path))
        elif kind in DEFINITION_NODES and DEFINITION_NODES[kind]:
            name= node.child_by_field_name("name")
            if name is not None:
                state.definitions.append({
                    "kind": DEFINITION_NODES[kind],
                    "name": _text(name),
                    "line": line_offset + node.start_point[0] + 1,
                })

        if "string" in kind and node.end_byte - node.start_byte <= MAX_STRING_BYTES:
            for url in URL_RE.findall(_text(node)):
                state.urls[url.rstrip(".,;:")]= None


def _scan_template(source: bytes, state: _State):
    text= source.decode("utf8", errors="replace")
    for match in SCRIPT_BLOCK_RE.finditer(text):
        attrs= {m[0].lower(): m[1] or m[2] or m[3] for m in ATTR_RE.findall(match.group(1))}
        if "src" not in attrs:
            _inline_script(state, attrs, match.group(2).encode(), text.count("\n", 0, match.start(2)))
    for match in TAG_RE.finditer(text):
        tag= match.group(1).lower()
        attrs= {m[0].lower(): m[1] or m[2] or m[3] for m in ATTR_RE.findall(match.group(2))}
        _record_tag(state.html, tag, attrs)


def parse_file(path: str, language: str, source: bytes) -> ParsedFile:
    parsed= ParsedFile(path, language)
    tree= _parser(language).parse(source)
    parsed.has_errors= tree.root_node.has_error

    state= _State(path=path, language=language)
    _walk(tree.root_node, state)
    if language in TEMPLATE_LANGUAGES and not state.saw_tags:
        _scan_template(source, state)

    parsed.imports= list(state.imports)
    parsed.calls= list(state.calls)
    parsed.urls= list(state.urls)
    parsed.definitions= state.definitions
    parsed.html= state.html
    parsed.routes= routes.finalise(state.routes, state.imports, language)
    parsed.web_context= routes.web_context(state.imports)
    return parsed
