from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Database ───────────────────────────────────────────────
    db_host: str = "localhost"
    db_port: int = 5433
    db_name: str = "rkp_core"
    db_user: str = "rkp_user"
    db_password: str = "rkp_password"

    # ── Ollama ─────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:latest"
    ollama_embed_model: str = "nomic-embed-text"    # 768-dim, used for pgvector search
    ollama_timeout: int = 300  # seconds; model cold-start can be slow

    # ── Anthropic (optional, not required) ─────────────────────
    anthropic_api_key: str = ""

    # ── GitHub ─────────────────────────────────────────────────
    github_token: str = ""

    # ── Service ────────────────────────────────────────────────
    app_port: int = 8000
    app_host: str = "0.0.0.0"
    log_level: str = "info"

    def get_dsn(self) -> str:
        """Return a postgres DSN string suitable for asyncpg."""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
