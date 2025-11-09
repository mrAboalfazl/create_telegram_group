from pydantic import Field
from pydantic_settings import BaseSettings
from typing import Optional, List
import os

class Settings(BaseSettings):
    bot_token: str = Field(alias="BOT_TOKEN")
    database_url: str = Field(alias="DATABASE_URL")
    fernet_key: str = Field(alias="FERNET_KEY")

    target_per_24h: int = Field(48, alias="TARGET_PER_24H")
    min_delay_s: int = Field(600, alias="MIN_DELAY_SECONDS")
    max_delay_s: int = Field(3600, alias="MAX_DELAY_SECONDS")
    max_attempts_per_group: int = Field(3, alias="MAX_ATTEMPTS_PER_GROUP")
    concurrent_workers_per_account: int = Field(1, alias="CONCURRENT_WORKERS_PER_ACCOUNT")
    log_retention_days: int = Field(30, alias="LOG_RETENTION_DAYS")
    floodwait_threshold_s_24h: int = Field(3600, alias="FLOODWAIT_THRESHOLD_SECONDS_PER_24H")

    group_title_prefix: str = Field("", alias="GROUP_TITLE_PREFIX")
    admin_user_ids: Optional[str] = Field("", alias="ADMIN_USER_IDS")

    class Config:
        env_file = os.getenv("ENV_FILE", ".env")
        case_sensitive = True

settings = Settings()
