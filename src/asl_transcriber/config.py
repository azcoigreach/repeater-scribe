from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASLT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="repeater-scribe")
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    database_url: str = Field(default="sqlite:///./data/asl_transcriber.db")
    data_dir: str = Field(default="./data")
    tmp_dir: str = Field(default="./tmp")
    source_timezone: str = Field(default="UTC")
    archive_paths: str = Field(default="./asl-monitor")
    correlation_tolerance_seconds: int = Field(default=30)
    whisper_model: str = Field(default="small.en")
    whisper_device: str = Field(default="cpu")
    whisper_compute_type: str = Field(default="int8")
    whisper_model_dir: str = Field(default="/data/models/whisper")
    whisper_language: str | None = Field(default=None)
    worker_concurrency: int = Field(default=1)
    archive_poll_seconds: float = Field(default=5.0)
    auto_process: bool = Field(default=False)
    file_stabilization_seconds: int = Field(default=5)
    min_duration_seconds: float = Field(default=0.5)
    max_duration_seconds: float = Field(default=1800.0)
    silence_threshold_db: float = Field(default=-40.0)
    audio_api_mode: str = Field(default="authenticated")
    auth_mode: str = Field(default="off")
    cors_origins: str = Field(default="http://localhost:8088")
    public_base_url: str = Field(default="http://localhost:8088")
    retention_days: int = Field(default=0)
    retry_backoff_seconds: int = Field(default=5)
    max_retries: int = Field(default=5)
    read_only_mode: bool = Field(default=False)
    ami_enabled: bool = Field(default=False)
    ami_host: str = Field(default="allstarlink3")
    ami_port: int = Field(default=5038)
    ami_username: str = Field(default="admin")
    ami_secret: str = Field(default="")
    ami_node_id: str = Field(default="668390")
    ami_timeout_seconds: float = Field(default=5.0)
    ami_reconnect_max_seconds: float = Field(default=60.0)
    ami_connection_grace_seconds: float = Field(default=15.0)
    ami_control_enabled: bool = Field(default=False)
    api_key: str = Field(default="")

    @property
    def archive_path_list(self) -> list[str]:
        return [item.strip() for item in self.archive_paths.split(",") if item.strip()]


settings = Settings()
