import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import settings
from app.db.session import get_session
from app.models import User
from app.services import auth
from main import app

from conftest import FakeGitHub

pytestmark= pytest.mark.anyio

KID= "test-key"


def make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY= make_key()


def jwks_for(key, kid=KID):
    public= jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**public, "kid": kid, "use": "sig", "alg": "RS256"}]}


def make_token(*, key=KEY, kid=KID, **overrides):
    claims= {
        "sub": "sub-1",
        "iss": settings.keycloak_issuer,
        "aud": settings.keycloak_audience,
        "exp": int(time.time()) + 300,
        "email": "ana@example.com",
        "preferred_username": "ana",
    }
    claims.update(overrides)
    claims= {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def keycloak(monkeypatch):
    calls= {"count": 0}

    async def fake_jwks():
        calls["count"]+= 1
        return jwks_for(KEY)

    monkeypatch.setattr(auth, "fetch_jwks", fake_jwks)
    auth._keys.clear()
    monkeypatch.setattr(auth, "_fetched_at", None)
    yield calls
    auth._keys.clear()


def call_activity(headers=None):
    return TestClient(app).get("/github/activity", params={"url": "https://github.com/o/r"}, headers=headers)


def test_request_without_a_token_is_rejected():
    response= call_activity()

    assert response.status_code== 401
    assert response.headers["www-authenticate"]== "Bearer"


def test_valid_token_reaches_the_endpoint(install):
    install(FakeGitHub())

    assert call_activity(bearer(make_token())).status_code== 200


@pytest.mark.parametrize(
    "token",
    [
        make_token(exp=int(time.time()) - 10),
        make_token(aud="outra-api"),
        make_token(iss="http://evil.example/realms/repoprint"),
        make_token(sub=None),
        make_token(key=make_key()),
        make_token(kid="desconhecida"),
        "isto-nao-e-um-jwt",
    ],
    ids=["expired", "wrong-audience", "wrong-issuer", "no-sub", "wrong-signature", "unknown-kid", "garbage"],
)
def test_invalid_tokens_are_rejected(token):
    assert call_activity(bearer(token)).status_code== 401


def test_token_signed_with_hs256_is_rejected():
    forged= jwt.encode(
        {"sub": "x", "iss": settings.keycloak_issuer, "aud": settings.keycloak_audience, "exp": int(time.time()) + 300},
        "segredo-com-mais-de-32-bytes-para-o-hmac",
        algorithm="HS256",
        headers={"kid": KID},
    )

    assert call_activity(bearer(forged)).status_code== 401


def test_keys_are_fetched_once_and_reused(keycloak):
    client= TestClient(app)
    token= make_token()

    for _ in range(3):
        client.get("/github/activity", params={"url": "https://github.com/o/r"}, headers=bearer(token))

    assert keycloak["count"]== 1


def test_unknown_key_does_not_refetch_every_request(keycloak):
    client= TestClient(app)
    token= make_token(kid="desconhecida")

    for _ in range(3):
        client.get("/github/activity", params={"url": "https://github.com/o/r"}, headers=bearer(token))

    assert keycloak["count"]== 1


def test_keycloak_down_gives_503(monkeypatch):
    async def down():
        raise httpx.ConnectError("sem ligação")

    monkeypatch.setattr(auth, "fetch_jwks", down)

    assert call_activity(bearer(make_token())).status_code== 503


@pytest.fixture
async def client(session):
    async def override():
        yield session

    app.dependency_overrides[get_session]= override
    transport= httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def count_users(session):
    return await session.scalar(select(func.count()).select_from(User))


async def test_me_creates_the_user_on_first_login(client, session):
    response= await client.get("/me", headers=bearer(make_token()))

    body= response.json()
    assert response.status_code== 200
    assert body["email"]== "ana@example.com"
    assert body["name"]== "ana"
    assert body["role"] is None
    assert await count_users(session)== 1


async def test_me_returns_the_same_user_afterwards(client, session):
    first= await client.get("/me", headers=bearer(make_token()))
    second= await client.get("/me", headers=bearer(make_token()))

    assert first.json()["id"]== second.json()["id"]
    assert await count_users(session)== 1


async def test_me_prefers_the_full_name(client):
    response= await client.get("/me", headers=bearer(make_token(name="Ana Silva")))

    assert response.json()["name"]== "Ana Silva"


async def test_me_without_email_is_forbidden(client, session):
    response= await client.get("/me", headers=bearer(make_token(email=None)))

    assert response.status_code== 403
    assert await count_users(session)== 0


async def test_me_refuses_an_email_that_belongs_to_another_account(client, session):
    await client.get("/me", headers=bearer(make_token(sub="sub-1")))

    response= await client.get("/me", headers=bearer(make_token(sub="sub-2")))

    assert response.status_code== 409
    assert await count_users(session)== 1


async def test_me_without_a_token_is_rejected(client):
    assert (await client.get("/me")).status_code== 401
