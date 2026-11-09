import io
import tarfile

import pytest
from tree_sitter_language_pack import get_parser

from app.services.code_fetcher import read_archive
from app.services.code_parser import parse_file
from app.services.dfd import build_dfd

SAMPLES= {
    "javascript": ('import axios from "axios";\nconst a = require("./a");\naxios.get("https://api.x.com/v1");\n', ["axios", "./a"], "axios.get"),
    "java": ("import java.sql.Connection;\nclass A { void m() { conn.prepareStatement(\"x\"); } }\n", ["java.sql.Connection"], "conn.prepareStatement"),
    "go": ('package main\nimport (\n"net/http"\n db "database/sql")\nfunc main() { http.Get("http://a.com") }\n', ["net/http", "database/sql"], "http.Get"),
    "rust": ('use reqwest::Client;\nfn main() { Client::new(); }\n', ["reqwest::Client"], "Client::new"),
    "c": ('#include <stdio.h>\n#include "a.h"\nint main() { printf("x"); }\n', ["stdio.h", "a.h"], "printf"),
    "ruby": ('require "net/http"\nrequire_relative "a"\nNet::HTTP.get(URI("http://a.com"))\n', ["net/http", "a"], "Net::HTTP.get"),
    "php": ('<?php\nuse App\\Models\\User;\nrequire "a.php";\ncurl_init("http://a.com");\n', ["App\\Models\\User", "a.php"], "curl_init"),
    "csharp": ('using System.Net.Http;\nclass A { void M() { client.GetAsync("x"); } }\n', ["System.Net.Http"], "client.GetAsync"),
    "python": ('import requests\nfrom ..pkg import x\nrequests.get("http://api.x.com")\n', ["requests", "..pkg"], "requests.get"),
}


@pytest.mark.parametrize("language", SAMPLES)
def test_parser_extracts_imports_and_calls_in_any_language(language):
    try:
        get_parser(language)
    except Exception:
        pytest.skip(f"parser de {language} indisponível (precisa de rede na primeira vez)")
    source, imports, call= SAMPLES[language]
    parsed= parse_file("f", language, source.encode())
    assert parsed.imports == imports
    assert call in parsed.calls


def test_urls_are_collected_from_strings_only():
    parsed= parse_file("f.py", "python", b'# http://comment.com\nx = "https://api.x.com/v1"\n')
    assert parsed.urls == ["https://api.x.com/v1"]


def _tarball(files: dict[str, bytes]) -> io.BytesIO:
    buffer= io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info= tarfile.TarInfo(f"owner-repo-abc123/{name}")
            info.size= len(data)
            tar.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    return buffer


def test_archive_keeps_code_and_drops_data_vendored_and_binary_files():
    archive= _tarball({
        "app/main.py": b"import os\n",
        "README.md": b"# hi\n",
        "config.json": b"{}",
        "node_modules/x/index.js": b"x",
        "app/blob.py": b"\0\0\0",
        "web/app.min.js": b"x",
        "docs/conf.py": b"links = {}",
    })
    assert [(f.path, f.language) for f in read_archive(archive)] == [("app/main.py", "python")]


def parsed(path, source):
    return parse_file(path, "python", source.encode())


def test_dfd_links_internal_modules_and_classifies_libraries():
    files= [
        parsed("app/routers/users.py", "from app.services.users import load\nfrom fastapi import APIRouter\n"),
        parsed("app/services/users.py", "import sqlalchemy\nimport requests\nrequests.get('https://api.stripe.com/v1')\n"),
    ]
    graph= build_dfd(files)
    edges= {(e["source"], e["target"], e["label"]) for e in graph["edges"]}
    assert ("app/routers", "app/services", "imports") in edges
    assert ("entity:client", "app/routers", "HTTP requests") in edges
    assert ("app/services", "datastore:SQL database", "reads/writes") in edges
    assert ("app/services", "external:HTTP API", "calls") in edges
    assert ("app/services", "external:api.stripe.com", "calls") in edges


def test_library_named_like_a_local_folder_is_still_the_library():
    files= [parsed("examples/celery/app.py", "import celery\n"), parsed("examples/celery/tasks.py", "x = 1\n")]
    graph= build_dfd(files, "file")
    assert any(n["id"] == "queue:Message queue" for n in graph["nodes"])
    assert not any(e["target"] == "examples/celery/tasks.py" for e in graph["edges"])


def test_group_by_file_gives_one_process_per_file():
    files= [parsed("a/x.py", "import a.y\n"), parsed("a/y.py", "x = 1\n")]
    graph= build_dfd(files, "file")
    assert {n["id"] for n in graph["nodes"]} == {"a/x.py", "a/y.py"}
    assert [(e["source"], e["target"]) for e in graph["edges"]] == [("a/x.py", "a/y.py")]


PAGE= """<form action="/login" method="post"><input name="u"><input name="p"></form>
<script src="https://cdn.x.com/a.js"></script>
<script>
import a from "./a.js";
fetch("https://api.y.com/z");
function go() {}
</script>
<script type="application/ld+json">{"@id": "https://schema.org/x"}</script>
<button onclick="go()">x</button><a href="javascript:alert(1)">y</a>
<iframe src="//ads.z.com/f"></iframe>
"""


def test_html_page_reports_forms_resources_handlers_and_inline_javascript():
    parsed= parse_file("index.html", "html", PAGE.encode())
    assert parsed.html.forms == [{"action": "/login", "method": "POST"}]
    assert parsed.html.resources == [{"tag": "script", "url": "https://cdn.x.com/a.js"}, {"tag": "iframe", "url": "//ads.z.com/f"}]
    assert parsed.html.inputs == 2
    assert parsed.html.inline_handlers == 2  # onclick e javascript:
    assert parsed.html.inline_scripts == 1  # o JSON-LD não é código
    # o JavaScript dentro de <script> é analisado como qualquer outro ficheiro
    assert parsed.imports == ["./a.js"]
    assert parsed.calls == ["fetch"]
    assert parsed.urls == ["https://api.y.com/z"]
    assert [(d["kind"], d["name"], d["line"]) for d in parsed.definitions] == [("function", "go", 6)]


def test_vue_single_file_component_uses_the_same_extraction():
    source= b'<template><form action="/a" method="post"><input v-model="x"/></form></template>\n<script lang="ts">import a from "./a"\nfetch("https://api.q.com")</script>'
    parsed= parse_file("C.vue", "vue", source)
    assert parsed.html.forms == [{"action": "/a", "method": "POST"}]
    assert parsed.imports == ["./a"] and parsed.urls == ["https://api.q.com"]


@pytest.mark.parametrize("language, source", [
    ("jinja2", '<form action="/a" method="post">{{ x|safe }}<input name=q></form><script src="https://cdn.j.com/j.js"></script>'),
    ("htmldjango", '<form action="/a" method="post">{% csrf_token %}<input name="q"></form><script src="https://cdn.j.com/j.js"></script>'),
    ("php", '<?php echo 1; ?>\n<form action="/a" method="POST"><input name="q"></form><script src="https://cdn.j.com/j.js"></script>'),
])
def test_template_languages_and_php_are_scanned_for_html(language, source):
    parsed= parse_file("t", language, source.encode())
    assert parsed.html.forms == [{"action": "/a", "method": "POST"}]
    assert parsed.html.inputs == 1
    assert parsed.html.resources == [{"tag": "script", "url": "https://cdn.j.com/j.js"}]


def test_archive_keeps_html_and_templates():
    archive= _tarball({"templates/index.html": b"<p>x</p>", "templates/base.jinja2": b"<p>{{ x }}</p>", "static/site.css": b"a{}"})
    assert [f.path for f in read_archive(archive)] == ["templates/index.html", "templates/base.jinja2"]


def test_dfd_shows_forms_third_party_resources_and_local_scripts():
    page= parse_file("web/index.html", "html", (
        '<form action="https://pay.acme.io/charge" method="post"><input name="c"></form>'
        '<script src="https://cdn.x.com/a.js"></script><script src="app.js"></script>'
    ).encode())
    script= parse_file("web/app.js", "javascript", b"fetch('https://api.y.com/z')")
    graph= build_dfd([page, script])
    edges= {(e["source"], e["target"], e["label"]) for e in graph["edges"]}
    assert ("entity:client", "web", "submits form") in edges
    assert ("web", "external:pay.acme.io", "form posts to") in edges
    assert ("web", "external:cdn.x.com", "loads script") in edges
    assert ("web", "external:api.y.com", "calls") in edges
    web= next(n for n in graph["nodes"] if n["id"] == "web")
    assert web["data"]["html"] == {"forms": 1, "inputs": 1, "inline_scripts": 0, "inline_handlers": 0, "external_resources": 1}


def test_dfd_links_an_html_page_to_the_script_it_loads():
    page= parse_file("site/index.html", "html", b'<script src="./js/app.js?v=3"></script>')
    script= parse_file("site/js/app.js", "javascript", b"x = 1")
    graph= build_dfd([page, script], "file")
    assert [(e["source"], e["target"], e["label"]) for e in graph["edges"]] == [("site/index.html", "site/js/app.js", "loads script")]


def test_urls_in_docstrings_are_ignored_but_the_same_url_in_code_is_kept():
    source= b'''def f():
    """See https://api.docs-only.io/v1 for details."""
    return get("https://api.real.io/v1")
'''
    parsed= parse_file("f.py", "python", source)
    assert parsed.urls == ["https://api.real.io/v1"]


@pytest.mark.parametrize("url", [
    "http://%s/x", "https://{host}/api", "http://${HOST}/api", "http://localhost:8000", "http://127.0.0.1/x",
    "https://example.com", "https://api.example.org/v1", "https://foobar.com", "https://svc.internal.test/x",
    "http://foo.localhost.localdomain/x",
])
def test_placeholder_hosts_never_become_external_entities(url):
    graph= build_dfd([parsed("a.py", f"x = '{url}'\n")])
    assert [n for n in graph["nodes"] if n["type"] == "external"] == []


def test_documentation_links_in_strings_are_dropped_but_real_apis_are_kept():
    source= (
        "links = {'py': 'https://docs.python.org/3/', 'src': 'https://github.com/o/r/issues', 'rtd': 'https://flask.readthedocs.io/'}\n"
        "api = 'https://api.github.com/repos'\n"
        "svc = 'http://backend:8000/api'\n"
    )
    hosts= {n["id"] for n in build_dfd([parsed("a.py", source)])["nodes"] if n["type"] == "external"}
    assert hosts == {"external:api.github.com", "external:backend"}


def test_a_page_that_really_loads_a_resource_from_a_reference_host_keeps_it():
    page= parse_file("p.html", "html", b'<iframe src="https://en.wikipedia.org/wiki/X"></iframe>')
    graph= build_dfd([page])
    assert any(e["target"] == "external:en.wikipedia.org" and e["label"] == "embeds iframe" for e in graph["edges"])


def routes_of(path, language, source):
    return [(r["method"], r["path"], r["handler"]) for r in parse_file(path, language, source.encode()).routes]


ROUTE_SAMPLES= {
    "python-fastapi": ("app/api.py", "python", 'from fastapi import APIRouter\nrouter = APIRouter()\n@router.get("/users/{id}", response_model=U)\nasync def read(id): pass\n@router.post("/users")\ndef make(): pass\n',
                       [("GET", "/users/{id}", "read"), ("POST", "/users", "make")]),
    "python-flask": ("app/views.py", "python", 'from flask import Blueprint\nbp = Blueprint("b", __name__)\n@bp.route("/login", methods=["GET", "POST"])\ndef login(): pass\n@bp.route("/")\ndef home(): pass\n',
                     [("GET", "/", "home"), ("GET", "/login", "login"), ("POST", "/login", "login")]),
    "python-django": ("shop/urls.py", "python", 'from django.urls import path, re_path, include\nurlpatterns = [path("items/<int:pk>/", views.item), re_path(r"^tags/(?P<slug>[-\\w]+)/?$", views.tag), path("api/", include("x.urls"))]\n',
                      [("ANY", "/items/<int:pk>", "views.item"), ("ANY", "/tags/{slug}", "views.tag")]),
    "express": ("src/routes.js", "javascript", 'const express = require("express")\nconst router = express.Router()\nrouter.get("/a", auth, list)\nrouter.post("/a", (req, res) => {})\naxios.get("/not-a-route", {})\nrequest(app).get("/t").expect(200)\n',
                [("GET", "/a", "list"), ("POST", "/a", "")]),
    "express-route-object": ("src/s.js", "javascript", 'const fastify = require("fastify")\nserver.route({ method: "GET", url: "/c", handler: h })\napp.route("/d").get(h).post(h)\n',
                             [("GET", "/c", ""), ("GET", "/d", "h"), ("POST", "/d", "h")]),
    "nestjs": ("src/users.controller.ts", "typescript", 'import { Controller, Get, Post } from "@nestjs/common"\n@Controller("users")\nexport class UsersController {\n  @Get(":id")\n  find() {}\n  @Post()\n  make() {}\n}\n',
               [("GET", "/users/:id", "find"), ("POST", "/users", "make")]),
    "spring": ("src/UserController.java", "java", 'import org.springframework.web.bind.annotation.*;\n@RestController\n@RequestMapping("/api")\nclass UserController {\n @GetMapping("/u/{id}")\n User f() { return null; }\n @RequestMapping(value = "/z", method = RequestMethod.POST)\n void g() {}\n}\n',
               [("GET", "/api/u/{id}", "f"), ("POST", "/api/z", "g")]),
    "jax-rs": ("src/R.java", "java", 'import javax.ws.rs.*;\n@Path("/res")\nclass R {\n @GET @Path("/p") String a() { return ""; }\n @POST void b() {}\n}\n',
               [("GET", "/res/p", "a"), ("POST", "/res", "b")]),
    "retrofit-is-a-client": ("src/Api.java", "java", 'interface Api {\n @GET("users/{id}") Call<U> user();\n}\n', []),
    "aspnet": ("src/UsersController.cs", "csharp", 'class UsersController {\n [HttpGet("{id}")]\n public int Get() { return 1; }\n [HttpPost]\n public void Post() {}\n}\n',
               [("GET", "/{id}", "Get"), ("POST", "/", "Post")]),
    "aspnet-controller-token": ("src/UsersController.cs", "csharp", '[Route("api/[controller]")]\nclass UsersController {\n [HttpGet("{id}")]\n public int Get() { return 1; }\n}\n',
                                [("GET", "/api/users/{id}", "Get")]),
    "aspnet-minimal": ("src/Program.cs", "csharp", 'class P { void M() { app.MapGet("/m", () => 1); } }\n', [("GET", "/m", "")]),
    "go-gin": ("main.go", "go", 'package main\nimport "github.com/gin-gonic/gin"\nfunc main() { r.GET("/u/:id", h); r.POST("/u", h2) }\n',
               [("GET", "/u/:id", "h"), ("POST", "/u", "h2")]),
    "go-net-http": ("main.go", "go", 'package main\nimport "net/http"\nfunc main() { http.HandleFunc("/x", f); mux.HandleFunc("POST /y", g) }\n',
                    [("ANY", "/x", "f"), ("POST", "/y", "g")]),
    "rust-actix": ("src/main.rs", "rust", 'use actix_web::get;\n#[get("/r")]\nasync fn r() {}\n', [("GET", "/r", "r")]),
    "rust-axum": ("src/main.rs", "rust", 'use axum::Router;\nfn main() { Router::new().route("/a", get(list).post(make)); }\n', [("GET", "/a", "list"), ("POST", "/a", "make")]),
    "laravel": ("routes/web.php", "php", '<?php\nRoute::get("/p", [C::class, "m"]);\nRoute::post("/q", "C@m");\n', [("GET", "/p", ""), ("POST", "/q", "C@m")]),
    "rails": ("config/routes.rb", "ruby", 'get "a", to: "c#d"\nresources :users\n', [("GET", "/a", ""), ("ANY", "/users", "")]),
    "sinatra": ("app.rb", "ruby", 'require "sinatra"\nget "/s" do\n 1\nend\n', [("GET", "/s", "")]),
}


@pytest.mark.parametrize("name", ROUTE_SAMPLES)
def test_routes_are_detected_for_each_framework(name):
    path, language, source, expected= ROUTE_SAMPLES[name]
    try:
        get_parser(language)
    except Exception:
        pytest.skip(f"parser de {language} indisponível")
    assert sorted(routes_of(path, language, source)) == sorted(expected)


def test_routes_declared_in_test_files_are_ignored():
    source= 'from flask import Flask\napp = Flask(__name__)\n@app.route("/only-in-tests")\ndef t(): pass\n'
    assert routes_of("tests/test_app.py", "python", source) == []
    assert routes_of("app/main.py", "python", source) == [("GET", "/only-in-tests", "t")]


def test_route_like_calls_need_a_web_framework_somewhere_in_the_repo():
    registers= parse_file("routes/index.js", "javascript", b"module.exports = (app) => { app.get('/x', list) }")
    assert [r.get("unconfirmed") for r in registers.routes] == [True]
    assert build_dfd([registers])["edges"] == []  # sem framework no repositório não há ponto de entrada

    server= parse_file("server.js", "javascript", b"const express = require('express')\nconst app = express()\n")
    graph= build_dfd([registers, server])
    edge= next(e for e in graph["edges"] if e["label"] == "HTTP requests")
    assert (edge["source"], edge["target"], edge["data"]["routes"]) == ("entity:client", "routes", ["GET /x"])


def test_dfd_lists_the_endpoints_on_the_process_and_on_the_client_edge():
    api= parse_file("app/api/users.py", "python", b'from fastapi import APIRouter\nr = APIRouter()\n@r.get("/users")\ndef a(): pass\n@r.post("/users")\ndef b(): pass\n')
    graph= build_dfd([api])
    process= next(n for n in graph["nodes"] if n["id"] == "app/api")
    assert process["data"]["route_count"] == 2
    assert [(r["method"], r["path"], r["handler"], r["file"]) for r in process["data"]["routes"]] == [
        ("GET", "/users", "a", "app/api/users.py"), ("POST", "/users", "b", "app/api/users.py")]
    edge= next(e for e in graph["edges"] if e["source"] == "entity:client")
    assert edge["data"]["routes"] == ["GET /users", "POST /users"]


REACT_PAGE= """const Page = () => (
  <div>
    <form action="/login" method="post" onSubmit={go}><input name="u" /><textarea /></form>
    <form onSubmit={save}><input name="q" /></form>
    <script src="https://cdn.x.com/a.js"></script>
    <iframe src={"https://ads.z.com/f"} title={t} />
    <img src={`https://img.q.com/${id}.png`} alt="" />
    <Script src="https://www.googletagmanager.com/gtag/js" />
    <link rel="stylesheet" href={cssUrl} />
    <Widget src="https://not-a-tag.example.io/w.js" onClick={fn} />
  </div>
)
"""


@pytest.mark.parametrize("language, path", [("tsx", "src/Page.tsx"), ("javascript", "src/Page.jsx")])
def test_jsx_is_read_for_forms_inputs_and_third_party_resources(language, path):
    parsed= parse_file(path, language, REACT_PAGE.encode())
    assert parsed.html.forms == [{"action": "/login", "method": "POST"}, {"action": "", "method": "JS"}]
    assert parsed.html.inputs == 3
    assert parsed.html.resources == [
        {"tag": "script", "url": "https://cdn.x.com/a.js"},
        {"tag": "iframe", "url": "https://ads.z.com/f"},
        {"tag": "img", "url": "https://img.q.com/${id}.png"},
        {"tag": "script", "url": "https://www.googletagmanager.com/gtag/js"},
    ]  # href={cssUrl} é dinâmico e <Widget> é um componente, por isso ficam de fora
    assert parsed.html.inline_handlers == 0  # onClick={fn} não é código embutido
    assert parsed.html.inline_scripts == 0


def test_dfd_treats_a_react_form_handled_in_javascript_differently_from_a_plain_form():
    graph= build_dfd([parse_file("src/Page.tsx", "tsx", REACT_PAGE.encode())])
    forms= next(e for e in graph["edges"] if e["label"] == "submits form")
    assert sorted(forms["data"]["via"]) == ["POST /login", "onSubmit handled in JavaScript"]
    labels= {(e["target"], e["label"]) for e in graph["edges"] if e["source"] == "src"}
    assert {("external:cdn.x.com", "loads script"), ("external:ads.z.com", "embeds iframe"),
            ("external:img.q.com", "loads media"), ("external:www.googletagmanager.com", "loads script")} <= labels
    # <Widget> é um componente: o URL é só uma string no código, não um recurso carregado pela página
    assert ("external:not-a-tag.example.io", "calls") in labels
    assert not any(t == "external:not-a-tag.example.io" and label.startswith(("loads", "embeds")) for t, label in labels)
    # e o URL do <script src> não aparece duplicado como "calls"
    assert ("external:cdn.x.com", "calls") not in labels
