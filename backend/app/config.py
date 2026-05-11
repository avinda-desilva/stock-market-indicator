from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    redis_password: str = ""
    secret_key: str = "dev-secret"
    debug: bool = True
    allowed_origins: str = "http://localhost:3000"

    # External APIs
    news_api_key: str = ""
    alpha_vantage_api_key: str = ""
    polygon_api_key: str = ""

    finnhub_api_key: str = ""

    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "stock-market-indicator/1.0"

    twitter_bearer_token: str = ""
    twitter_api_key: str = ""
    twitter_api_secret: str = ""
    twitter_access_token: str = ""
    twitter_access_token_secret: str = ""

    # Ollama LLM
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b-instruct-q4_K_M"
    ollama_max_concurrency: int = 1
    ollama_timeout: int = 20

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


settings = Settings()
