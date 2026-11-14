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

    model_config= SettingsConfigDict(env_file=".env", extra="ignore")

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
