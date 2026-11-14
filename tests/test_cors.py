import pytest
from fastapi.testclient import TestClient

import main
from app.config import Settings


def client_for(monkeypatch, origins: str) -> TestClient:
    monkeypatch.setattr(main, "settings", Settings(cors_origins=origins, _env_file=None))
    return TestClient(main.create_app())


def test_origins_are_split_on_commas_and_cleaned():
    settings= Settings(cors_origins=" http://a.test/ , https://b.test ,, ", _env_file=None)
    assert settings.cors_origin_list == ["http://a.test", "https://b.test"]


def test_the_default_is_the_local_frontend():
    assert Settings(_env_file=None).cors_origin_list == ["http://localhost:3000"]


def test_a_listed_origin_may_read_the_response(monkeypatch):
    response= client_for(monkeypatch, "https://app.test").get("/health", headers={"Origin": "https://app.test"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.test"


def test_an_unlisted_origin_gets_no_permission(monkeypatch):
    response= client_for(monkeypatch, "https://app.test").get("/health", headers={"Origin": "https://evil.test"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_the_browser_preflight_is_answered_for_get_only(monkeypatch):
    client= client_for(monkeypatch, "https://app.test")
    ok= client.options("/dfd", headers={"Origin": "https://app.test", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "accept"})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "https://app.test"
    assert ok.headers["access-control-allow-methods"] == "GET"
    denied= client.options("/dfd", headers={"Origin": "https://app.test", "Access-Control-Request-Method": "DELETE"})
    assert denied.status_code == 400


def test_credentials_are_never_allowed(monkeypatch):
    response= client_for(monkeypatch, "https://app.test").get("/health", headers={"Origin": "https://app.test"})
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("path", ["/dfd", "/github/activity"])
def test_errors_keep_the_cors_headers_so_the_page_can_show_them(monkeypatch, path):
    response= client_for(monkeypatch, "https://app.test").get(path, params={"url": "not a url"}, headers={"Origin": "https://app.test"})
    assert response.status_code == 400
    assert response.headers["access-control-allow-origin"] == "https://app.test"
