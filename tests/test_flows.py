import pytest
from tree_sitter_language_pack import get_parser

from app.services.code_parser import parse_file
from app.services.dfd import build_dfd
from app.services.flows import operation


def need(*languages):
    for language in languages:
        try:
            get_parser(language)
        except Exception:
            pytest.skip(f"parser de {language} indisponível (precisa de rede na primeira vez)")


def graph(files, group_by="directory"):
    return build_dfd([parse_file(path, language, source.encode()) for path, language, source in files], group_by)


def flows_of(g):
    return {f["route"]: {r["id"]: r for r in f["resources"]} for f in g["flows"]}


@pytest.mark.parametrize("kind, method, sql, expected", [
    ("datastore", "findAll", None, "read"),
    ("datastore", "find_by_id", None, "read"),
    ("datastore", "save", None, "write"),
    ("datastore", "insertMany", None, "write"),
    ("datastore", "get_or_create", None, "write"),
    ("datastore", "filter", None, "read"),
    ("datastore", "execute", None, "access"),
    ("datastore", "execute", "INSERT INTO t VALUES (1)", "write"),
    ("datastore", "query", "insert into t values (1)", "write"),
    ("datastore", "query", "  SELECT * FROM t", "read"),
    ("datastore", "connect", None, None),
    ("datastore", "create_engine", None, None),
    ("datastore", "Query", "select 1", "read"),
    ("datastore", "CountAsync", None, "read"),
    ("external", "get", None, "read"),
    ("external", "post", None, "write"),
    ("queue", "publish", None, "write"),
    ("queue", "consume", None, "read"),
    ("queue", "delay", None, "write"),
])
def test_operation_comes_from_the_method_name_or_the_sql(kind, method, sql, expected):
    assert operation(kind, method, sql) == expected


LAYERS= [
    ("app/api/users.py", "python", 'from fastapi import APIRouter\nfrom app.services import users as svc\nrouter = APIRouter()\n@router.get("/users")\ndef list_users():\n    return svc.load_all()\n@router.post("/users")\ndef make(u):\n    return svc.create(u)\n@router.get("/ping")\ndef ping():\n    return "ok"\n'),
    ("app/services/users.py", "python", 'from app.db import repo\nfrom app.clients import notify\ndef load_all():\n    return repo.fetch_users()\ndef create(u):\n    notify.welcome(u)\n    return repo.insert_user(u)\n'),
    ("app/clients/notify.py", "python", 'import requests\ndef welcome(u):\n    return requests.post("https://api.mailer.io/send", json=u)\n'),
    ("app/db/repo.py", "python", 'import psycopg2\nconn = psycopg2.connect()\ndef fetch_users():\n    cur = conn.cursor()\n    cur.execute("select 1")\n    return cur.fetchall()\ndef insert_user(u):\n    conn.cursor().execute("insert into users values (1)")\n    conn.commit()\n'),
]


def test_a_route_reaches_the_database_through_service_and_repository_layers():
    flows= flows_of(graph(LAYERS))
    assert set(flows) == {"GET /users", "POST /users"}  # /ping não toca em nada
    read= flows["GET /users"]["datastore:PostgreSQL"]
    assert read["operations"] == ["read"]
    assert read["path"] == ["app/api", "app/services", "app/db", "datastore:PostgreSQL"]
    assert read["site"] == {"file": "app/db/repo.py", "line": 5, "call": "cur.execute"}
    assert flows["POST /users"]["datastore:PostgreSQL"]["operations"] == ["write"]


def test_a_route_that_calls_an_external_api_through_a_helper_reaches_it():
    post= flows_of(graph(LAYERS))["POST /users"]
    assert post["external:HTTP API"]["operations"] == ["write"]
    assert post["external:HTTP API"]["path"] == ["app/api", "app/services", "app/clients", "external:HTTP API"]
    assert "external:api.mailer.io" in post


def test_resource_edges_say_what_the_code_does_with_them():
    g= graph(LAYERS)
    labels= {(e["source"], e["target"]): e["label"] for e in g["edges"]}
    assert labels[("app/db", "datastore:PostgreSQL")] == "reads/writes"
    edge= next(e for e in g["edges"] if e["target"] == "datastore:PostgreSQL")
    assert edge["data"]["operations"] == ["read", "write"]


def test_a_process_that_only_reads_gets_a_reads_edge_and_one_that_only_writes_a_writes_edge():
    g= graph([
        ("a/read.py", "python", 'import psycopg2\nconn = psycopg2.connect()\ndef f():\n    return conn.cursor().fetchall()\n'),
        ("b/write.py", "python", 'import psycopg2\nconn = psycopg2.connect()\ndef f():\n    conn.commit()\n    conn.cursor().execute("delete from t")\n'),
        ("c/unknown.py", "python", 'import psycopg2\ndef f():\n    pass\n'),
    ])
    labels= {e["source"]: e["label"] for e in g["edges"] if e["target"] == "datastore:PostgreSQL"}
    assert labels == {"a": "reads", "b": "writes", "c": "reads/writes"}  # sem prova mantém a etiqueta genérica


def test_import_of_a_symbol_is_not_mistaken_for_a_submodule():
    # `from app.api.deps import SessionDep`: SessionDep é um símbolo do módulo deps, não um módulo
    g= graph([
        ("app/api/deps.py", "python", 'from typing import Annotated\nfrom sqlmodel import Session\nSessionDep = Annotated[Session, Depends(get_db)]\n'),
        ("app/api/items.py", "python", 'from fastapi import APIRouter\nfrom app.api.deps import SessionDep\nrouter = APIRouter()\n@router.post("/items")\ndef create(session: SessionDep, item):\n    session.add(item)\n    session.commit()\n@router.get("/items")\ndef read(session: SessionDep):\n    return session.get(Item, 1)\n'),
    ])
    flows= flows_of(g)
    assert flows["POST /items"]["datastore:SQL database"]["operations"] == ["write"]
    assert flows["GET /items"]["datastore:SQL database"]["operations"] == ["read"]


def test_express_anonymous_handlers_are_attributed_and_sql_decides_read_or_write():
    flows= flows_of(graph([("src/routes.js", "javascript", (
        'const express = require("express")\nconst { Pool } = require("pg")\nconst axios = require("axios")\n'
        'const pool = new Pool()\nconst router = express.Router()\n'
        'router.get("/items", async (req, res) => { const rows = await pool.query("select * from items"); res.json(rows) })\n'
        'router.post("/items", async (req, res) => { await pool.query("insert into items values (1)"); await axios.post("https://hooks.x.io/h", req.body) })\n'
        'router.get("/ping", (req, res) => res.send("ok"))\n')),
    ]))
    assert flows["GET /items"]["datastore:PostgreSQL"]["operations"] == ["read"]
    assert flows["POST /items"]["datastore:PostgreSQL"]["operations"] == ["write"]
    assert flows["POST /items"]["external:HTTP API"]["operations"] == ["write"]
    assert "GET /ping" not in flows


def test_express_controller_reaches_a_prisma_client_exported_from_another_module():
    flows= flows_of(graph([
        ("src/prisma.ts", "typescript", 'import { PrismaClient } from "@prisma/client"\nconst prisma = new PrismaClient()\nexport default prisma\n'),
        ("src/articles.ts", "typescript", 'import express from "express"\nimport prisma from "./prisma"\nconst router = express.Router()\nrouter.get("/articles", async (req, res) => { res.json(await prisma.article.findMany()) })\nrouter.post("/articles", async (req, res) => { res.json(await prisma.article.create({})) })\n'),
    ]))
    assert flows["GET /articles"]["datastore:SQL database"]["operations"] == ["read"]
    assert flows["POST /articles"]["datastore:SQL database"]["operations"] == ["write"]


def test_spring_controller_reaches_the_database_through_an_injected_service_and_repository_interface():
    need("java")
    flows= flows_of(graph([
        ("a/UserController.java", "java", 'import org.springframework.web.bind.annotation.*;\n@RestController\n@RequestMapping("/api")\nclass UserController {\n private final UserService service;\n @GetMapping("/users")\n public List<User> all() { return service.findAll(); }\n @PostMapping("/users")\n public User add(User u) { return service.save(u); }\n}\n'),
        ("a/UserService.java", "java", 'class UserService {\n private final UserRepository repo;\n List<User> findAll() { return repo.findAll(); }\n User save(User u) { return repo.save(u); }\n}\n'),
        ("a/UserRepository.java", "java", 'import org.springframework.data.jpa.repository.JpaRepository;\ninterface UserRepository extends JpaRepository<User, Long> { }\n'),
    ]))
    assert flows["GET /api/users"]["datastore:SQL database"]["operations"] == ["read"]
    assert flows["POST /api/users"]["datastore:SQL database"]["operations"] == ["write"]


def test_interface_calls_follow_the_class_that_implements_it():
    need("csharp")
    flows= flows_of(graph([
        ("Web/OrdersController.cs", "csharp", 'class OrdersController {\n private readonly IOrderService _service;\n [HttpGet("orders")]\n public int List() { return _service.Load(); }\n}\n'),
        ("Core/IOrderService.cs", "csharp", 'interface IOrderService { int Load(); }\n'),
        ("Infra/OrderService.cs", "csharp", 'using Microsoft.EntityFrameworkCore;\nclass OrderService : IOrderService {\n private readonly DbContext db;\n public int Load() { return db.Orders.Count(); }\n}\n'),
    ]))
    assert flows["GET /orders"]["datastore:SQL database"]["operations"] == ["read"]


def test_go_handlers_call_functions_from_other_files_of_the_same_package_without_importing_them():
    need("go")
    flows= flows_of(graph([
        ("articles/routers.go", "go", 'package articles\nimport "github.com/gin-gonic/gin"\nfunc Register(r *gin.RouterGroup) { r.GET("/", List); r.POST("/", Create) }\nfunc List(c *gin.Context) { FindAll() }\nfunc Create(c *gin.Context) { Save() }\n'),
        ("articles/models.go", "go", 'package articles\nimport "example.com/app/common"\nfunc FindAll() { db := common.GetDB(); db.Find(nil) }\nfunc Save() { db := common.GetDB(); db.Save(nil) }\n'),
        ("common/database.go", "go", 'package common\nimport "gorm.io/gorm"\nfunc GetDB() *gorm.DB { return nil }\n'),
    ]))
    assert flows["GET /"]["datastore:SQL database"]["operations"] == ["read"]
    assert flows["POST /"]["datastore:SQL database"]["operations"] == ["write"]


def test_class_based_views_reach_resources_through_their_methods():
    flows= flows_of(graph([
        ("shop/views.py", "python", 'from django.views import View\nimport psycopg2\nconn = psycopg2.connect()\nclass ItemView(View):\n    def get(self, request):\n        return conn.cursor().fetchall()\n    def post(self, request):\n        conn.commit()\n'),
        ("shop/urls.py", "python", 'from django.urls import path\nfrom shop import views\nurlpatterns = [path("items/", views.ItemView.as_view())]\n'),
    ]))
    assert flows["ANY /items"]["datastore:PostgreSQL"]["operations"] == ["read", "write"]
    assert next(f for f in graph([
        ("shop/views.py", "python", 'import psycopg2\nconn = psycopg2.connect()\nclass ItemView:\n    def get(self):\n        return conn.cursor().fetchall()\n    def post(self):\n        conn.commit()\n'),
        ("shop/urls.py", "python", 'from django.urls import path\nfrom shop import views\nurlpatterns = [path("items/", views.ItemView.as_view())]\n'),
    ])["flows"])["handler"] == "ItemView"


def test_mutually_recursive_functions_do_not_loop_forever():
    flows= flows_of(graph([
        ("app/api.py", "python", 'from fastapi import APIRouter\nimport psycopg2\nconn = psycopg2.connect()\nrouter = APIRouter()\ndef a(n):\n    return b(n)\ndef b(n):\n    conn.commit()\n    return a(n - 1)\n@router.get("/x")\ndef h():\n    return a(3)\n'),
    ]))
    assert flows["GET /x"]["datastore:PostgreSQL"]["operations"] == ["write"]


def test_constructing_a_client_is_not_an_access():
    g= graph([("app/api.py", "python", 'from fastapi import APIRouter\nfrom redis import Redis\nrouter = APIRouter()\n@router.get("/x")\ndef h():\n    r = Redis()\n    return r.get("k")\n')])
    assert flows_of(g)["GET /x"]["datastore:Redis"]["operations"] == ["read"]


def test_a_repository_named_object_uses_the_projects_only_database():
    need("csharp")
    flows= flows_of(graph([
        ("Web/Ep.cs", "csharp", 'class Ep {\n [HttpGet("a")]\n public int Get(IRepository<Item> itemRepository) { return itemRepository.CountAsync(); }\n [HttpPost("a")]\n public void Post(IRepository<Item> itemRepository) { itemRepository.AddAsync(1); }\n}\n'),
        ("Infra/Db.cs", "csharp", 'using Microsoft.EntityFrameworkCore;\nclass Db : DbContext { }\n'),
    ]))
    assert flows["GET /a"]["datastore:SQL database"]["operations"] == ["read"]
    assert flows["POST /a"]["datastore:SQL database"]["operations"] == ["write"]


def test_flow_paths_follow_the_files_when_grouping_by_file():
    flows= flows_of(graph(LAYERS, "file"))
    assert flows["GET /users"]["datastore:PostgreSQL"]["path"] == ["app/api/users.py", "app/services/users.py", "app/db/repo.py", "datastore:PostgreSQL"]


def test_building_the_graph_twice_gives_the_same_result():
    files= [parse_file(path, language, source.encode()) for path, language, source in LAYERS]
    assert build_dfd(files) == build_dfd(files)


def test_calls_edges_are_added_between_processes_that_never_import_each_other():
    need("go")
    g= graph([
        ("pkg/a.go", "go", 'package pkg\nimport "github.com/gin-gonic/gin"\nfunc R(r *gin.RouterGroup) { r.GET("/", H) }\nfunc H(c *gin.Context) { helper() }\n'),
        ("pkg/b.go", "go", 'package pkg\nimport "database/sql"\nfunc helper(db *sql.DB) { db.Query("select 1") }\n'),
    ], "file")
    pairs= {(e["source"], e["target"], e["label"]) for e in g["edges"]}
    assert ("pkg/a.go", "pkg/b.go", "calls") in pairs
    assert ("pkg/b.go", "datastore:SQL database", "reads") in pairs


def test_a_base_class_with_hundreds_of_subclasses_is_inheritance_not_dependency_injection():
    # Django, por exemplo, tem centenas de classes que estendem Field ou Model; expandi-las em cada chamada
    # tornava a análise de um repositório grande interminável
    import time
    from app.services.flows import MAX_IMPLEMENTERS

    files= [("lib/base.py", "python", "class Base:\n    def run(self):\n        pass\n")]
    files += [(f"lib/sub{i}.py", "python", f"from lib.base import Base\nclass Sub{i}(Base):\n    def run(self):\n        self.helper()\n    def helper(self):\n        pass\n") for i in range(MAX_IMPLEMENTERS * 4)]
    files.append(("lib/use.py", "python", "from lib.base import Base\ndef go(item: Base):\n" + "".join("    item.run()\n" for _ in range(300))))
    parsed= [parse_file(path, language, source.encode()) for path, language, source in files]
    start= time.time()
    build_dfd(parsed)
    assert time.time() - start < 5
