from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

class Settings(BaseSettings):
    github_token: str | None= None
    activity_cache_ttl_seconds: int= 600
    # Origens (separadas por vírgulas) que o navegador pode usar para chamar esta API; "*" aceita todas.
    cors_origins: str= "http://localhost:3000"

    # GET /dfd: ver app/services/guard.py
    dfd_max_concurrent: int= 2              # análises ao mesmo tempo
    dfd_queue_timeout_seconds: float= 10    # quanto se espera por uma vaga antes de responder 503
    dfd_rate_limit: int= 10                 # análises novas por cliente e por janela (0 desliga)
    dfd_rate_window_seconds: int= 60
    dfd_cache_ttl_seconds: int= 600         # 0 desliga a cache
    dfd_cache_max_entries: int= 32
    dfd_fetch_method: str= "auto"           # "auto": lista os ficheiros pela API e pede só o código ao raw; se falhar, descarrega o arquivo. "archive": só o arquivo
    dfd_fetch_concurrency: int= 48          # pedidos ao raw.githubusercontent.com ao mesmo tempo
    github_raw_url: str= "https://raw.githubusercontent.com"
    trust_forwarded_for: bool= False        # atrás de um proxy: usar o X-Forwarded-For como endereço do cliente
    db_host: str= "localhost"
    db_port: int= 5432
    db_name: str= "repoprint"
    db_user: str= "repoprint"
    db_password: SecretStr | None= None
    keycloak_issuer: str= "http://localhost:8080/realms/repoprint"
    keycloak_jwks_url: str | None= None
    keycloak_audience: str= "repoprint-api"
    keycloak_url: str | None= None
    keycloak_admin_client_secret: SecretStr | None= None
    github_client_id: str | None= None
    github_client_secret: SecretStr | None= None
    github_redirect_uri: str= "http://localhost:3000/api/github/callback"
    github_oauth_url: str= "https://github.com"
    github_api_url: str= "https://api.github.com"
    token_encryption_key: SecretStr | None= None
    github_app_id: str | None= None
    github_app_slug: str | None= None
    github_app_client_id: str | None= None
    github_app_client_secret: SecretStr | None= None
    github_app_private_key: SecretStr | None= None
    github_app_redirect_uri: str= "http://localhost:3000/api/github/app/callback"

    model_config= SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]
    @property
    def realm_url(self) -> str:
        return self.keycloak_url or self.keycloak_issuer

    @property
    def admin_url(self) -> str:
        return self.realm_url.replace("/realms/", "/admin/realms/")

    @property
    def jwks_url(self) -> str:
        return self.keycloak_jwks_url or f"{self.keycloak_issuer}/protocol/openid-connect/certs"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value() if self.db_password else None,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

settings= Settings()
