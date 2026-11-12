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

    model_config= SettingsConfigDict(env_file=".env", extra="ignore")

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
