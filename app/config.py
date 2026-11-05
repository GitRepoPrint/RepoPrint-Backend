from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    github_token: str | None= None
    activity_cache_ttl_seconds: int= 600

    model_config= SettingsConfigDict(env_file=".env", extra="ignore")

settings= Settings()