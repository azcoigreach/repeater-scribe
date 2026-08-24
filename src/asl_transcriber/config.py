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
    whisper_model: str = Field(default="large-v3")
    whisper_device: str = Field(default="cuda")
    whisper_compute_type: str = Field(default="float16")
    whisper_model_dir: str = Field(default="/data/models/whisper")
    whisper_language: str | None = Field(default="en")
    whisper_beam_size: int = Field(default=5, ge=1)
    whisper_vad_filter: bool = Field(default=True)
    whisper_initial_prompt: str | None = Field(
        default="Amateur radio repeater traffic. Transcribe callsigns exactly."
    )
    whisper_hotwords: str = Field(default="")
    known_callsigns: str = Field(default="KM7GHS,NY7S,W7JHQ")
    callsign_hotword_limit: int = Field(default=0, ge=0, le=50)
    callsign_max_candidates: int = Field(default=250, ge=1, le=5_000)
    callsign_context_cache_seconds: float = Field(default=30.0, ge=1)
    worker_concurrency: int = Field(default=1)
    archive_poll_seconds: float = Field(default=5.0)
    auto_process: bool = Field(default=False)
    live_transcription: bool = Field(default=False)
    live_poll_seconds: float = Field(default=1.5, gt=0)
    live_window_seconds: float = Field(default=12.0, gt=2)
    live_beam_size: int = Field(default=1, ge=1)
    live_min_file_bytes: int = Field(default=4096, ge=44)
    ffmpeg_binary: str = Field(default="ffmpeg")
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
    ami_reconcile_seconds: float = Field(default=5.0)
    ami_event_debounce_seconds: float = Field(default=0.15)
    ami_sse_heartbeat_seconds: float = Field(default=15.0)
    ami_confirmation_timeout_seconds: float = Field(default=10.0)
    ami_connection_grace_seconds: float = Field(default=15.0)
    ami_control_enabled: bool = Field(default=False)
    api_key: str = Field(default="")
    favorite_stats_enabled: bool = Field(default=True)
    favorite_stats_base_url: str = Field(
        default="http://stats.allstarlink.org/api/stats"
    )
    favorite_stats_request_interval_seconds: float = Field(default=3.0, ge=2.1)
    favorite_stats_timeout_seconds: float = Field(default=20.0, gt=0)
    favorite_stats_refresh_seconds: int = Field(default=15, ge=3)
    favorite_stats_stale_seconds: int = Field(default=300, gt=0)
    favorite_stats_recent_activity_seconds: int = Field(default=120, gt=0)
    topology_max_nodes: int = Field(default=200, ge=2, le=2_000)
    topology_max_depth: int = Field(default=12, ge=1, le=50)
    topology_refresh_seconds: int = Field(default=900, ge=60)
    topology_node_cache_seconds: int = Field(default=300, ge=30)
    topology_sse_heartbeat_seconds: float = Field(default=15.0, gt=0)

    @property
    def archive_path_list(self) -> list[str]:
        return [item.strip() for item in self.archive_paths.split(",") if item.strip()]

    @property
    def known_callsign_list(self) -> list[str]:
        return [item.strip() for item in self.known_callsigns.split(",") if item.strip()]


settings = Settings()
