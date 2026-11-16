from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

class Settings(BaseSettings):
    github_token: str | None= None
    activity_cache_ttl_seconds: int= 600
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

    model_config= SettingsConfigDict(env_file=".env", extra="ignore")

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
