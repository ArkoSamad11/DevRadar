from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str
    ingest_interval_minutes: float = 15
    discovery_interval_hours: float = 24


settings = Settings()
