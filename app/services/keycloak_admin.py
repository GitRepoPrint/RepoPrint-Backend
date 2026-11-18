import time
from urllib.parse import quote

import httpx

from app.config import settings

ADMIN_CLIENT_ID= "repoprint-admin"
TOKEN_MARGIN_SECONDS= 10

_token: tuple[str, float] | None= None


class KeycloakUnavailable(Exception):
    pass


def reset_token() -> None:
    global _token
    _token= None


async def admin_token(client: httpx.AsyncClient) -> str:
    global _token
    if _token and _token[1] - TOKEN_MARGIN_SECONDS > time.monotonic():
        return _token[0]
    secret= settings.keycloak_admin_client_secret
    if secret is None:
        raise KeycloakUnavailable("KEYCLOAK_ADMIN_CLIENT_SECRET não está definido")
    response= await client.post(
        f"{settings.realm_url}/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": ADMIN_CLIENT_ID,
            "client_secret": secret.get_secret_value(),
        },
    )
    if response.status_code != 200:
        raise KeycloakUnavailable(f"token do service account recusado ({response.status_code})")
    data= response.json()
    _token= (data["access_token"], time.monotonic() + data["expires_in"])
    return _token[0]


async def user_request(method: str, sub: str) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            url= f"{settings.admin_url}/users/{quote(sub, safe='')}"
            response= await client.request(method, url, headers={"Authorization": f"Bearer {await admin_token(client)}"})
            if response.status_code == 401:
                reset_token()
                response= await client.request(method, url, headers={"Authorization": f"Bearer {await admin_token(client)}"})
    except httpx.HTTPError as error:
        raise KeycloakUnavailable(str(error)) from error
    return response


async def delete_user(sub: str) -> None:
    response= await user_request("DELETE", sub)
    if response.status_code not in (204, 404):
        raise KeycloakUnavailable(f"Keycloak respondeu {response.status_code}")


async def user_exists(sub: str) -> bool:
    response= await user_request("GET", sub)
    if response.status_code not in (200, 404):
        raise KeycloakUnavailable(f"Keycloak respondeu {response.status_code}")
    return response.status_code == 200
