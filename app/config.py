from pydantic_settings import BaseSettings, SettingsConfigDict

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
    trust_forwarded_for: bool= False        # atrás de um proxy: usar o X-Forwarded-For como endereço do cliente

    model_config= SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

settings= Settings()
