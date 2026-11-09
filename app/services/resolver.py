import os
import posixpath
import re
from collections import defaultdict

INDEX_NAMES= {"index", "mod", "__init__", "main", "lib"}


class PathIndex:
    """Procura ficheiros do repositório a partir do nome de um módulo importado."""

    def __init__(self, paths: list[str]):
        self.suffixes: dict[str, list[str]]= defaultdict(list)
        self.exact: dict[str, str]= {}
        for path in sorted(paths):
            stem= os.path.splitext(path)[0]
            stems= [stem]
            if posixpath.basename(stem) in INDEX_NAMES:
                stems.append(posixpath.dirname(stem))
            # um import de Go aponta para uma pasta, por isso as pastas também contam
            stems.append(posixpath.dirname(path))
            for s in stems:
                self.exact.setdefault(s, path)
                parts= [p for p in s.split("/") if p]
                for i in range(len(parts)):
                    bucket= self.suffixes["/".join(parts[i:])]
                    if path not in bucket:
                        bucket.append(path)

    def find(self, key: str, importer: str) -> str | None:
        found= [p for p in self.suffixes.get(key.strip("/"), []) if p != importer]
        if not found:
            return None
        here= posixpath.dirname(importer).split("/")
        # em caso de ambiguidade ganha o ficheiro mais próximo do importador
        return max(found, key=lambda p: len(os.path.commonprefix([here, posixpath.dirname(p).split("/")])))

    def resolve(self, module: str, importer: str) -> str | None:
        module= module.strip()
        folder= posixpath.dirname(importer)

        if module.startswith("."):
            dots= len(module) - len(module.lstrip("."))
            rest= module[dots:]
            if rest.startswith("/") or module.startswith(("./", "../")):
                base= posixpath.normpath(posixpath.join(folder, module))
            else:
                base= posixpath.normpath(posixpath.join(folder, *[".."] * (dots - 1), rest.replace(".", "/")))
            return self._exact(base, importer)

        # include "a.h" e require "./a" podem ser relativos ao ficheiro sem o dizerem
        local= self._exact(posixpath.normpath(posixpath.join(folder, module)), importer)
        if local:
            return local

        normalised= re.sub(r"(::|\\)", "/", module)
        normalised= re.sub(r"^(crate|self|super)/", "", normalised)
        keys= [normalised, os.path.splitext(normalised)[0]]
        if "/" not in normalised:
            keys.append(normalised.replace(".", "/"))
        for key in dict.fromkeys(keys):
            segments= key.split("/")
            # import estático de Java e "use a::b::Item" apontam para dentro do ficheiro
            for trim in range(min(3, len(segments))):
                kept= segments[: len(segments) - trim]
                if kept and (found := self.find("/".join(kept), importer)):
                    if len(kept) > 1 or trim == 0:
                        return found
        return None

    def resolve_by_owner_prefix(self, module: str, importer: str) -> str | None:
        # "github.com/owner/repo/pkg/sub" -> "pkg/sub": só aceita um candidato único
        if "/" not in module:
            return None
        segments= module.split("/")
        for drop in range(1, len(segments)):
            matches= {p for p in self.suffixes.get("/".join(segments[drop:]), []) if p != importer}
            if len(matches) == 1:
                return matches.pop()
        return None

    def _exact(self, base: str, importer: str) -> str | None:
        for key in dict.fromkeys([base, os.path.splitext(base)[0]]):
            path= self.exact.get(key)
            if path and path != importer:
                return path
        return None
