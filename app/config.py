from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://executor:password@postgres:5432/executor"
    db_password: str = "changeme"

    # Redis / Celery
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # S3
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_output_prefix: str = "ffmpeg-results"
    s3_region: str = "auto"

    # FFmpeg
    ffmpeg_max_run_seconds: int = 600
    ffmpeg_work_dir: str = "/work"

    # API
    api_key: str = ""  # Optional internal key (not CF token — that's handled by CF Access)
    debug: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
