"""Core configuration module."""
from pydantic_settings import BaseSettings
from typing import Optional
from pathlib import Path


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "mineduc_intelligence"
    postgres_user: str = "mineduc_admin"
    postgres_password: str
    
    # FastAPI
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    debug: bool = False
    
    # Security
    fernet_key: str
    
    # Data paths
    data_raw_path: str = "data_raw"
    
    # Playwright
    playwright_headless: bool = True
    
    # Logging
    log_level: str = "INFO"
    
    @property
    def database_url(self) -> str:
        """Build PostgreSQL connection string."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:"
            f"{self.postgres_password}@{self.postgres_host}:"
            f"{self.postgres_port}/{self.postgres_db}"
        )
    
    class Config:
        """Pydantic config."""
        env_file = ".env"
        case_sensitive = False


settings = Settings()
