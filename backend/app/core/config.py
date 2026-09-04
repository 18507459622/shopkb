"""应用配置：pydantic-settings 单一真源，环境变量覆盖 .env。

字段用前缀命名（app_/database_/llm_/...）在逻辑上分组，
避免嵌套模型的 env 变量名（双下划线）带来的心智负担与拼写错误。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_SECRETS = ("change-me", "changeme", "your-", "replace", "secret-here")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),  # 支持从 backend/ 或仓库根目录运行
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- App ----
    app_env: str = "dev"
    app_debug: bool = False
    app_name: str = "shopkb"
    app_cors_origins: list[str] = Field(default_factory=list)

    # ---- Database ----
    database_url: str = "sqlite+aiosqlite:///./data/shopkb.db"
    database_echo: bool = False

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- Milvus ----
    # 注意：不能用 MILVUS_URI 作为 env 变量名（pymilvus 会从环境全局读取并冲突）
    vectorstore_uri: str = "./data/milvus_lite.db"  # 本地 Lite；生产 http://host:19530
    milvus_collection: str = "product_kb"
    milvus_dim: int = 1024

    # ---- LLM (DeepSeek) ----
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_temperature: float = 0.1

    # ---- Embedding (DashScope) ----
    embedding_model: str = "text-embedding-v3"
    embedding_api_key: str = ""
    embedding_dim: int = 1024
    embedding_batch_size: int = 10

    # ---- Reranker ----
    reranker_enabled: bool = False
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # ---- Auth ----
    jwt_secret: str = ""
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # ---- Storage ----
    upload_dir: str = "./data/uploads"

    # ---- Observability ----
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    sentry_dsn: str = ""

    @model_validator(mode="after")
    def _fail_fast_in_prod(self) -> Settings:
        """生产环境拒绝不安全配置：debug、弱密钥、空 API Key 启动即失败。"""
        if self.app_env != "prod":
            return self
        problems: list[str] = []
        if self.app_debug:
            problems.append("app_debug 在 prod 下必须为 false")
        if not self.jwt_secret or any(s in self.jwt_secret.lower() for s in _PLACEHOLDER_SECRETS):
            problems.append("jwt_secret 为弱密钥/占位符")
        if not self.llm_api_key:
            problems.append("llm_api_key 缺失")
        if not self.embedding_api_key:
            problems.append("embedding_api_key 缺失")
        if problems:
            raise ValueError("生产配置校验失败: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
