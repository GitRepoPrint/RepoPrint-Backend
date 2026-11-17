"""Fluxos de dados: que recursos (bases de dados, APIs externas, filas) cada função, e por isso cada ponto de
entrada, acaba por tocar.

Trabalha sobre o que o parser já extraiu: definições com a sua extensão, chamadas com a sua posição, variáveis com
tipo ou inicializador e os nomes que cada import traz. Daí resolve cada chamada para (a) um acesso a um recurso,
(b) uma função do repositório, em qualquer ficheiro, ou (c) nada, e segue as chamadas internas até encontrar recursos.
"""
import posixpath
import re
from bisect import bisect_left
from dataclasses import dataclass, field

from app.services.code_parser import ParsedFile
from app.services.dfd_catalog import DATASTORE, ENTRYPOINT, EXTERNAL, QUEUE, classify
from app.services.resolver import PathIndex

MAX_DEPTH= 8
MAX_IMPLEMENTERS= 25  # uma base com mais subclasses que isto é herança (Field, Model, Exception), não injeção de dependências
MAX_IMPLEMENTATIONS= 6
MAX_REACH_PER_FUNCTION= 60

READ_WORDS= (
    "get", "find", "select", "query", "read", "fetch", "load", "list", "count", "exist", "scan", "search", "retriev",
    "first", "lookup", "head", "download", "stream", "view", "check", "is", "has", "aggregat", "distinct", "peek",
    "all", "filter", "exclude", "order", "annotat", "prefetch", "only", "defer", "where", "take", "skip", "any", "to",
)
WRITE_WORDS= (
    "insert", "save", "update", "delet", "remov", "creat", "put", "set", "write", "add", "commit", "push", "publish",
    "send", "post", "patch", "upsert", "persist", "merg", "drop", "truncat", "stor", "flush", "incr", "decr", "expire",
    "emit", "produc", "enqueu", "delay", "upload", "bulk", "clear", "reset", "append", "replac", "renam", "destroy",
)
QUEUE_READ_WORDS= ("consum", "subscrib", "poll", "receiv", "listen", "pop", "get", "fetch", "read", "pull", "ack", "next")
HTTP_READ= {"get", "head", "options"}
HTTP_WRITE= {"post", "put", "patch", "delete"}

# Métodos que ligam ou constroem o cliente em vez de lhe tocar nos dados
FACTORIES= {
    "connect", "create_engine", "createclient", "createpool", "pool", "client", "session", "sessionmaker", "cursor",
    "get_connection", "getconnection", "from_url", "database", "db", "collection", "table", "get_database",
    "get_collection", "getrepository", "get_repository", "repository", "transaction", "begin", "channel", "queue",
    "topic", "bucket", "get_bucket", "getdb", "get_db", "get_session", "getsession", "model", "schema", "use",
    "init", "configure", "setup", "initialize", "new", "newclient", "newpool", "newsession", "newdb", "open",
    "opendb", "connectdb", "dial",
}
FACTORY_PREFIXES= ("create", "new", "open", "connect", "make", "from_", "from", "build", "init")

# Nomes que, numa variável sem origem conhecida, quase de certeza são um cliente de base de dados
CONVENTIONAL_DATASTORE_NAMES= {
    "db", "database", "session", "conn", "connection", "cursor", "pool", "collection", "repo", "repository", "em",
    "entitymanager", "jdbctemplate", "mongo", "prisma", "knex", "redis", "cache", "dao", "dbcontext", "sequelize",
    "mapper", "objects",
}
CONVENTIONAL_SUFFIXES= ("repository", "repo", "dao", "mapper", "store", "template")
SQL_READ_RE= re.compile(r"^\s*\(?\s*(select|with|show|describe|explain|pragma)\b", re.I)
SQL_WRITE_RE= re.compile(r"^\s*(insert|update|delete|replace|create|drop|alter|truncate|merge|upsert|grant|revoke)\b", re.I)
REPOSITORY_LIKE_RE= re.compile(r"(repository|repo|dao)$|^(dbcontext|entitymanager|jdbctemplate|_?context)$", re.I)
CONNECT_WORD_RE= re.compile(r"[a-z]+|[A-Z][a-z]*|[A-Z]+(?![a-z])|\d+")


@dataclass(frozen=True)
class Resource:
    kind: str
    label: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.label}"


@dataclass
class Func:
    id: str
    path: str
    name: str
    cls: str | None
    start: int
    end: int
    line: int
    calls: list[dict]= field(default_factory=list)
    urls: list[str]= field(default_factory=list)

    @property
    def qualname(self) -> str:
        return f"{self.cls}.{self.name}" if self.cls else self.name


def _first_word(method: str) -> str:
    words= CONNECT_WORD_RE.findall(method.lstrip("_"))
    return words[0].lower() if words else ""


def operation(kind: str, method: str, sql: str | None= None) -> str | None:
    """"read", "write" ou "access" conforme o nome do método; None para métodos que só constroem o cliente.
    Se o primeiro argumento é SQL, é ele que diz o que se faz (execute("insert ...") escreve, mesmo chamando-se execute)."""
    if method.lower() in FACTORIES:
        return None
    if kind == DATASTORE and sql:
        if SQL_WRITE_RE.match(sql):
            return "write"
        if SQL_READ_RE.match(sql):
            return "read"
    if re.search(r"or_?create|update_?or|find_?or_?create", method, re.I):
        return "write"  # get_or_create lê, mas o que a distingue é que escreve
    word= _first_word(method)
    if kind == EXTERNAL:
        if word in HTTP_READ:
            return "read"
        return "write" if word in HTTP_WRITE else "access"
    if kind == QUEUE:
        if word.startswith(WRITE_WORDS):
            return "write"
        return "read" if word.startswith(QUEUE_READ_WORDS) else "access"
    if word.startswith(WRITE_WORDS):
        return "write"
    return "read" if word.startswith(READ_WORDS) else "access"


class FlowAnalyzer:
    def __init__(self, parsed: list[ParsedFile], index: PathIndex, host_of):
        self.files= {p.path: p for p in parsed}
        self.index= index
        self.host_of= host_of
        self.funcs: dict[str, list[Func]]= {}
        self.by_name: dict[str, dict[str, list[Func]]]= {}
        self.classes: dict[str, list[str]]= {}
        self.implementers: dict[str, list[str]]= {}
        self.dir_files: dict[str, list[str]]= {}
        self._names: dict[str, dict]= {}
        self._reach: dict[str, dict]= {}
        self._file_reach: dict[str, dict]= {}
        self._file_resources: dict[str, list[Resource]]= {}
        self._resolved: dict[tuple[str, str, str | None], tuple | None]= {}
        self._implementing_cache: dict[str, list[str]]= {}
        self._implementations_cache: dict[tuple[str, str], list[Func]]= {}
        for p in parsed:
            self._index_file(p)
        # se o repositório inteiro só fala com uma base de dados, um objeto "…Repository" injetado usa essa
        stores= {r for p in parsed for r in self.file_resources(p.path) if self._is_database(r)}
        self.sole_datastore: Resource | None= next(iter(stores)) if len(stores) == 1 else None

    # ------------------------------------------------------------ preparação

    def _index_file(self, p: ParsedFile):
        sites= sorted(p.call_sites, key=lambda c: c["start"])
        starts= [c["start"] for c in sites]
        urls= sorted(p.url_sites, key=lambda u: u[1])
        url_starts= [u[1] for u in urls]
        funcs: list[Func]= []
        seen: set[tuple[int, int]]= set()
        for d in p.definitions:
            if d["kind"] == "class":
                self.classes.setdefault(d["name"], []).append(p.path)
                for base in d.get("bases", []):
                    self.implementers.setdefault(base, []).append(d["name"])
            if p.language == "go" and p.path not in self.dir_files.setdefault(posixpath.dirname(p.path), []):
                self.dir_files[posixpath.dirname(p.path)].append(p.path)
            if d["kind"] not in ("function", "method") or "start" not in d or (d["start"], d["end"]) in seen:
                continue
            seen.add((d["start"], d["end"]))
            func= self._func(p.path, d["name"], d.get("class"), d["start"], d["end"], d["line"], sites, starts, urls, url_starts)
            funcs.append(func)
        self.funcs[p.path]= funcs
        index: dict[str, list[Func]]= {}
        for func in funcs:
            index.setdefault(func.name, []).append(func)
        self.by_name[p.path]= index

    @staticmethod
    def _func(path, name, cls, start, end, line, sites, starts, urls, url_starts) -> Func:
        lo, hi= bisect_left(starts, start), bisect_left(starts, end)
        ulo, uhi= bisect_left(url_starts, start), bisect_left(url_starts, end)
        return Func(f"{path}::{cls + '.' if cls else ''}{name}@{start}", path, name, cls, start, end, line,
                    sites[lo:hi], [u[0] for u in urls[ulo:uhi]])

    @staticmethod
    def _is_database(resource: Resource) -> bool:
        return resource.kind == DATASTORE and resource.label != "File storage"

    def file_resources(self, path: str) -> list[Resource]:
        """Recursos que o ficheiro importa, na ordem em que aparecem."""
        if path not in self._file_resources:
            found: list[Resource]= []
            for module in self.files[path].imports:
                category= classify(module)
                if category and category[0] != ENTRYPOINT and Resource(*category) not in found:
                    found.append(Resource(category[0], category[1]))
            self._file_resources[path]= found
        return self._file_resources[path]

    # ------------------------------------------------------------ nomes

    def _class_file(self, name: str, importer: str) -> str | None:
        paths= [p for p in self.classes.get(name, []) if p != importer] or self.classes.get(name, [])
        if not paths:
            return None
        here= importer.split("/")[:-1]
        return max(paths, key=lambda p: len([1 for a, b in zip(here, p.split("/")[:-1]) if a == b]))

    def _bind_import(self, path: str, module: str, original: str):
        language= self.files[path].language
        if original and language == "python":
            joined= module + original if module.endswith(".") else f"{module}.{original}"
            target= self.index.resolve(joined, path)
            # `from pkg import mod` traz um submódulo; `from mod import Name` traz um símbolo, e o resolvedor
            # também acharia o ficheiro `mod` (por cortar o fim), por isso o nome do ficheiro tem de ser o importado
            if target and original in (posixpath.splitext(posixpath.basename(target))[0], posixpath.basename(posixpath.dirname(target))):
                return ("internal", target, None)
        # "example.com/app/common" é a pasta common do próprio repositório (o módulo de Go começa com o dono)
        target= self.index.resolve(module, path) or self.index.resolve_by_owner_prefix(module, path)
        if target and target != path:
            return ("internal", target, original or None)
        category= classify(module) or (classify(f"{module}.{original}") if original else None)
        if category and category[0] != ENTRYPOINT:
            return ("resource", Resource(category[0], category[1]))
        return None

    def _lookup(self, table: dict, path: str, name: str):
        if name in table:
            return table[name]
        target= self._class_file(name, path)
        return ("internal", target, name) if target else None

    def names(self, path: str) -> dict:
        """O que cada nome local do ficheiro é: um recurso, ou uma função/classe/módulo de outro ficheiro."""
        if path in self._names:
            return self._names[path]
        p= self.files[path]
        table: dict[str, tuple]= {}
        for local, module, original in p.bindings:
            if local not in table and (bound := self._bind_import(path, module, original)):
                table[local]= bound
        self._names[path]= table
        for _ in range(3):
            changed= False
            for v in p.variables:
                if v["name"] in table:
                    continue
                bound= None
                if v["type"]:
                    bound= self._lookup(table, path, v["type"].split(".")[0])
                if bound is None and v["init"]:
                    bound= self._bind_init(path, v["init"], table)
                if bound:
                    table[v["name"]]= bound
                    changed= True
            if not changed:
                break
        return table

    def _bind_init(self, path: str, init: str, table: dict):
        """`x = Pool()`, `c = redis.Redis()`, `cur = conn.cursor()` ou `self.db = pool`."""
        segments= [s for s in re.split(r"[.:]+", re.sub(r"^(self|this)\.", "", init)) if s]
        if not segments:
            return None
        last= segments[-1]
        if len(segments) == 1:
            bound= self._lookup(table, path, last)
            # uma função importada (x = carregar()) devolve dados, não é o módulo onde vive
            if bound and bound[0] == "internal" and bound[2] and bound[2][:1].islower():
                return None
            return bound
        factory= last[:1].isupper() or last.lower() in FACTORIES or last.lower().startswith(FACTORY_PREFIXES)
        for name in reversed(segments[:-1]):
            bound= table.get(name)
            if bound and factory:
                # o resultado de db.get_user() não é o cliente; o de db.cursor() ou Pool() é
                if bound[0] == "internal":
                    if last[:1].isupper():
                        return ("internal", bound[1], last)
                    # common.GetDB(): uma função do repositório que devolve o cliente da base de dados que ela usa
                    stores= [r for r in self.file_resources(bound[1]) if r.kind in (DATASTORE, QUEUE)]
                    return ("resource", stores[0]) if stores else None
                return bound
        return None

    # ------------------------------------------------------------ chamadas

    def _same_file(self, path: str, name: str, cls: str | None) -> Func | None:
        found= list(self.by_name.get(path, {}).get(name, []))
        if not found and self.files[path].language == "go":
            # em Go os ficheiros da mesma pasta são um só pacote e chamam-se sem import
            for sibling in self.dir_files.get(posixpath.dirname(path), []):
                found += self.by_name.get(sibling, {}).get(name, [])
        return next((f for f in found if f.cls == cls), found[0] if found else None)

    def _in_file(self, path: str, name: str, cls: str | None) -> list[Func]:
        found= self.by_name.get(path, {}).get(name, [])
        return [f for f in found if f.cls == cls] or found

    def _internal(self, bound: tuple, method: str | None, importer: str):
        _, target, symbol= bound
        if target == importer and method is None:
            return None
        if method is None:  # chamada direta de um símbolo importado
            if symbol and (found := self._in_file(target, symbol, None)):
                return ("funcs", found[:1])
            constructors= [f for f in self.funcs.get(target, []) if f.cls == symbol and f.name in ("__init__", "constructor", "new", symbol)]
            return ("funcs", constructors[:1]) if constructors else None  # construir um objeto não toca em recursos
        found= self._in_file(target, method, symbol if symbol and symbol in self.classes else None)
        if symbol and symbol in self.classes:
            # interface ou classe base: quem faz o trabalho são as classes que a implementam (injeção de dependências)
            found= [*found[:1], *self._implementations(symbol, method)]
        if found:
            return ("funcs", found[:4])
        # método que não se vê no ficheiro: de uma interface, de um tipo herdado ou de um módulo que não se analisou
        return ("module", target, method) if symbol is None else ("object", target, method, symbol)

    def _implementing(self, base: str) -> list[str]:
        """Classes que implementam ou estendem `base`, num só nível e só se forem poucas (senão é herança, não DI)."""
        if base not in self._implementing_cache:
            names= self.implementers.get(base, [])
            self._implementing_cache[base]= list(dict.fromkeys(names)) if len(names) <= MAX_IMPLEMENTERS else []
        return self._implementing_cache[base]

    def _implementations(self, base: str, method: str) -> list[Func]:
        key= (base, method)
        if key not in self._implementations_cache:
            found: list[Func]= []
            for name in self._implementing(base):
                for path in self.classes.get(name, []):
                    found += [f for f in self.by_name.get(path, {}).get(method, []) if f.cls == name and f.calls]
                if len(found) >= MAX_IMPLEMENTATIONS:
                    break
            self._implementations_cache[key]= found[:MAX_IMPLEMENTATIONS]
        return self._implementations_cache[key]

    def resolve_call(self, path: str, callee: str, cls: str | None):
        """Resolve uma chamada: ("resource", Resource, método), ("funcs", [Func]), ("object"|"module", ...) ou None."""
        key= (path, callee, cls)
        if key not in self._resolved:
            self._resolved[key]= self._resolve_call(path, callee, cls)
        return self._resolved[key]

    def _resolve_call(self, path: str, callee: str, cls: str | None):
        segments= [s for s in re.split(r"[.:]+", re.sub(r"^(self|this|super|Self)(\.|::)", "", callee)) if s]
        if not segments:
            return None
        table= self.names(path)
        method= segments[-1]
        if len(segments) == 1:
            if (func := self._same_file(path, method, cls)):
                return ("funcs", [func])
            bound= table.get(method)
            if bound and bound[0] == "internal":
                return self._internal(bound, None, path)
            if bound and bound[0] == "resource":
                # Pool(), Redis(), new SqlConnection(): construir o cliente não lê nem escreve dados
                return None if method[:1].isupper() else ("resource", bound[1], method)
            return ("resource", Resource(EXTERNAL, "HTTP API"), "get") if method == "fetch" else None

        bound= [table[s] for s in segments[:-1] if s in table]
        if bound:
            last= bound[-1]
            return ("resource", last[1], method) if last[0] == "resource" else self._internal(last, method, path)
        if any(s.lower() in CONVENTIONAL_DATASTORE_NAMES or s.lower().endswith(CONVENTIONAL_SUFFIXES) for s in segments[:-1]):
            stores= [r for r in self.file_resources(path) if self._is_database(r)]
            if stores:
                return ("resource", stores[0], method)
        if self.sole_datastore and any(REPOSITORY_LIKE_RE.search(s) for s in segments[:-1]):
            return ("resource", self.sole_datastore, method)
        return None

    # ------------------------------------------------------------ alcance

    @staticmethod
    def _entry(resource: Resource, op: str, chain: list[str], site: tuple) -> dict:
        return {"resource": resource, "ops": {op}, "chain": chain, "site": site}

    @staticmethod
    def _merge(into: dict, rid: str, entry: dict):
        current= into.get(rid)
        if current is None:
            if len(into) < MAX_REACH_PER_FUNCTION:
                into[rid]= {**entry, "ops": set(entry["ops"])}
            return
        current["ops"] |= entry["ops"]
        if len(entry["chain"]) < len(current["chain"]):
            current["chain"], current["site"]= entry["chain"], entry["site"]

    def _from_call(self, func_path: str, callee: str, cls: str | None, line: int, stack: tuple, into: dict, sql: str | None= None):
        action= self.resolve_call(func_path, callee, cls)
        if action is None:
            return
        if action[0] == "resource":
            _, resource, method= action
            if (op := operation(resource.kind, method, sql)) is not None:
                self._merge(into, resource.id, self._entry(resource, op, [], (func_path, line, callee)))
        elif action[0] == "funcs":
            for target in action[1]:
                for rid, entry in self.reach(target, stack).items():
                    self._merge(into, rid, {**entry, "chain": [target.path, *entry["chain"]]})
        elif action[0] == "object":
            # `repo.save()` onde o tipo de repo vive noutro ficheiro sem corpo para save: o que o ficheiro importa
            # diz *onde* se escreve (as bases de dados) e o nome do método diz *o que* se faz
            _, target, method, symbol= action
            files= [target, *[p for name in self._implementing(symbol) for p in self.classes.get(name, [])]]
            found= [(path, r) for path in files for r in self.file_resources(path) if r.kind in (DATASTORE, QUEUE)]
            if not found and self.sole_datastore and REPOSITORY_LIKE_RE.search(re.sub(r"^I(?=[A-Z])", "", symbol)):
                found= [(target, self.sole_datastore)]  # IRepository<T> de uma biblioteca: usa a única base de dados do projeto
            for path, resource in found:
                if (op := operation(resource.kind, method, sql)) is not None:
                    self._merge(into, resource.id, self._entry(resource, op, [path], (path, 1, callee)))
        elif action[0] == "module":
            _, target, method= action
            for rid, entry in self.file_reach(target, stack).items():
                ops= entry["ops"]
                if ops == {"access"} and not entry["chain"] and entry["resource"].kind in (DATASTORE, QUEUE):
                    # o módulo só importa o recurso (ex.: exporta o cliente do Prisma): o método diz o que se faz
                    ops= {operation(entry["resource"].kind, method, sql) or "access"}
                self._merge(into, rid, {**entry, "ops": ops, "chain": [target, *entry["chain"]]})

    def reach(self, func: Func, stack: tuple= ()) -> dict:
        """Recursos que a função toca, direta ou indiretamente, com o caminho de ficheiros até cada um."""
        if func.id in self._reach:
            return self._reach[func.id]
        if func.id in stack or len(stack) >= MAX_DEPTH:
            return {}
        stack= (*stack, func.id)
        out: dict[str, dict]= {}
        for call in func.calls:
            self._from_call(func.path, call["callee"], func.cls, call["line"], stack, out, call.get("arg"))
        for url in func.urls:
            if (host := self.host_of(url)):
                self._merge(out, f"{EXTERNAL}:{host}", self._entry(Resource(EXTERNAL, host), "access", [], (func.path, func.line, url)))
        self._reach[func.id]= out
        return out

    def file_reach(self, path: str, stack: tuple= ()) -> dict:
        """Quando não se acha a função concreta (interface, código gerado) conta-se o que o ficheiro todo toca."""
        if path in self._file_reach:
            return self._file_reach[path]
        if f"file:{path}" in stack or len(stack) >= MAX_DEPTH:
            return {}
        out: dict[str, dict]= {}
        for func in self.funcs.get(path, []):
            for rid, entry in self.reach(func, (*stack, f"file:{path}")).items():
                self._merge(out, rid, entry)
        for resource in self.file_resources(path):
            self._merge(out, resource.id, self._entry(resource, "access", [], (path, 1, "import")))
        self._file_reach[path]= out
        return out

    # ------------------------------------------------------------ resultados

    def access_ops(self) -> dict[tuple[str, str], set[str]]:
        """(ficheiro, recurso) -> operações feitas diretamente nesse ficheiro, em qualquer ponto dele."""
        ops: dict[tuple[str, str], set[str]]= {}
        for path, p in self.files.items():
            funcs= self.funcs.get(path, [])
            for call in p.call_sites:
                inside= [f for f in funcs if f.start <= call["start"] < f.end]
                cls= max(inside, key=lambda f: f.start).cls if inside else None
                action= self.resolve_call(path, call["callee"], cls)
                if action and action[0] == "resource":
                    _, resource, method= action
                    if (op := operation(resource.kind, method, call.get("arg"))) is not None:
                        ops.setdefault((path, resource.id), set()).add(op)
        return ops

    def handlers(self, path: str, route: dict) -> list[Func]:
        """Funções que tratam a rota: a do decorador, a anónima passada à chamada, a que o nome indica ou, se o nome
        é de uma classe (vistas do Django, controladores), os seus métodos."""
        span= route.get("span")
        if span:
            for func in self.funcs.get(path, []):
                if (func.start, func.end) == tuple(span):
                    return [func]
            p= self.files[path]
            sites= sorted(p.call_sites, key=lambda c: c["start"])
            urls= sorted(p.url_sites, key=lambda u: u[1])
            return [self._func(path, f"{route['method']} {route['path']}", None, span[0], span[1], route["line"],
                               sites, [c["start"] for c in sites], urls, [u[1] for u in urls])]
        name= (route.get("handler") or "").strip()
        if not name or not re.fullmatch(r"[\w.:]+", name):
            return []
        action= self.resolve_call(path, name, None)
        if action and action[0] == "funcs":
            return [action[1][0]]
        table, tail= self.names(path), re.split(r"[.:]+", name)[-1]
        bound= table.get(tail) or (table.get(name.split(".")[0]) if "." in name else None)
        cls_file= bound[1] if bound and bound[0] == "internal" else (path if tail in self.classes and path in self.classes[tail] else None)
        if cls_file and tail in self.classes:
            return [f for f in self.funcs.get(cls_file, []) if f.cls == tail]
        return []

    def flows(self, routes_by_path: dict[str, list[dict]]) -> list[dict]:
        found: list[dict]= []
        for path, routes in routes_by_path.items():
            for route in routes:
                funcs= self.handlers(path, route)
                reached: dict[str, dict]= {}
                for func in funcs:
                    for rid, entry in self.reach(func).items():
                        self._merge(reached, rid, entry)
                if reached:
                    same_class= funcs[0].cls and len(funcs) > 1 and all(f.cls == funcs[0].cls for f in funcs)
                    found.append({"route": route, "file": path, "handler": funcs[0], "reach": reached,
                                  "handler_name": funcs[0].cls if same_class else funcs[0].name})
        return found
