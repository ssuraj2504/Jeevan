from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Jeevan"
    app_env: str = "development"
    database_url: str = "sqlite:///./jeevan.db"
    booking_adapter: str = "simulated"
    voice_adapter: str = "simulated"
    public_base_url: str = "http://127.0.0.1:8000"
    max_call_attempts: int = 3
    jeevan_webhook_secret: str = "change-me"
    public_demo_mode: bool = False
    demo_session_secret: str | None = None
    demo_max_tasks_per_session: int = 12
    demo_max_tasks_total: int = 5000

    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    livekit_sip_trunk_id: str | None = None
    livekit_agent_name: str = "jeevan-provider-caller"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
