from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    github_token: str | None= None
    activity_cache_ttl_seconds: int= 600
    # Origens (separadas por vírgulas) que o navegador pode usar para chamar esta API; "*" aceita todas.
    cors_origins: str= "http://localhost:3000"

    model_config= SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

settings= Settings()
