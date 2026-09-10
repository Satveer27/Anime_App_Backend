import os
from pydantic_settings import BaseSettings, SettingsConfigDict


environment = os.getenv("APP_ENV", "dev")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=f".env.{environment}", extra="ignore")
    db_url: str
    redis_url: str
    redis_broker_url: str
    redis_backend_url: str
    log_sql: bool = False
    environment: str = environment
    jwt_secret: str 
    refresh_token_expire_days: int
    access_token_expire_minutes: int
    jwt_algorithm: str
    http_secure: bool = False
    resend_api_key: str 
    frontend_url: str

settings = Settings()