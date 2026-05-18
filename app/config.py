"""
ESI-Bench - Configuration for the application.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    MONGO_URL: str = "mongodb://localhost:27017"
    MONGO_DB: str = "benchmarks"
    BENCHMARK_DISK_PATH: str = "/tmp/benchmark"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
