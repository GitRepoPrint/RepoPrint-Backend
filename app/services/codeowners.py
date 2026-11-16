import re
from dataclasses import dataclass

WILDCARDS= ("*", "?")


@dataclass
class Rule:
    pattern: re.Pattern
    owners: list[str]


def _translate(body: str) -> str:
    out= []
    i= 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i+= 3
        elif body.startswith("/**", i) and i + 3 == len(body):
            out.append("/.*")
            i+= 3
        elif body.startswith("**", i):
            out.append(".*")
            i+= 2
        elif body[i]== "*":
            out.append("[^/]*")
            i+= 1
        elif body[i]== "?":
            out.append("[^/]")
            i+= 1
        elif body[i]== "\\" and i + 1 < len(body):
            out.append(re.escape(body[i + 1]))
            i+= 2
        else:
            out.append(re.escape(body[i]))
            i+= 1
    return "".join(out)


def compile_pattern(pattern: str) -> re.Pattern | None:
    if pattern.startswith("!") or "[" in pattern:
        return None
    directory= pattern.endswith("/")
    body= pattern.strip("/")
    if not body:
        return None
    anchored= pattern.startswith("/") or "/" in pattern.rstrip("/")
    last= body.rsplit("/", 1)[-1]
    prefix= "^" if anchored else "^(?:.*/)?"
    if directory:
        suffix= "/.+$"
    elif any(mark in last for mark in WILDCARDS):
        suffix= "$"
    else:
        suffix= "(?:/.*)?$"
    return re.compile(prefix + _translate(body) + suffix)


def parse(text: str) -> list[Rule]:
    rules= []
    for line in text.splitlines():
        line= line.strip()
        if not line or line.startswith("#"):
            continue
        tokens= re.split(r"(?<!\\)\s+", line)
        owners= []
        for token in tokens[1:]:
            if token.startswith("#"):
                break
            if token.startswith("@") or "@" in token:
                owners.append(token)
        pattern= compile_pattern(tokens[0])
        if pattern:
            rules.append(Rule(pattern, owners))
    return rules


def count_owned(paths: list[str], rules: list[Rule]) -> int:
    ordered= list(reversed(rules))
    owned= 0
    for path in paths:
        for rule in ordered:
            if rule.pattern.match(path):
                if rule.owners:
                    owned+= 1
                break
    return owned
