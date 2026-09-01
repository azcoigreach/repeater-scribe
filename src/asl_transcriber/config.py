from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
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
    known_callsigns: str = Field(default="")
    callsign_hotword_limit: int = Field(default=0, ge=0, le=50)
    callsign_max_candidates: int = Field(default=250, ge=1, le=5_000)
    callsign_context_cache_seconds: float = Field(default=30.0, ge=1)
    qrz_username: str = Field(default="")
    qrz_password: str = Field(default="")
    qrz_password_file: str = Field(default="")
    qrz_base_url: str = Field(default="https://xmldata.qrz.com/xml/current/")
    qrz_timeout_seconds: float = Field(default=10.0, gt=0)
    qrz_cache_seconds: float = Field(default=86400.0, ge=60)
    qrz_last_heard_limit: int = Field(default=25, ge=1, le=100)
    worker_concurrency: int = Field(default=1)
    archive_poll_seconds: float = Field(default=5.0)
    auto_process: bool = Field(default=False)
    live_transcription: bool = Field(default=False)
    live_poll_seconds: float = Field(default=1.5, gt=0)
    live_window_seconds: float = Field(default=12.0, gt=2)
    live_beam_size: int = Field(default=1, ge=1)
    live_min_file_bytes: int = Field(default=4096, ge=44)
    live_max_files_per_cycle: int = Field(default=1, ge=1, le=8)
    ffmpeg_binary: str = Field(default="ffmpeg")
    file_stabilization_seconds: int = Field(default=5)
    min_duration_seconds: float = Field(default=0.5)
    max_duration_seconds: float = Field(default=1800.0)
    silence_threshold_db: float = Field(default=-40.0)
    deployment_mode: Literal["local", "internet"] = Field(default="local")
    auth_mode: Literal["off", "oidc"] = Field(default="off")
    cors_origins: str = Field(default="")
    public_base_url: str = Field(default="http://localhost:8088")
    allowed_hosts: str = Field(default="localhost,127.0.0.1,testserver")
    session_secret: str = Field(default="")
    session_secret_file: str = Field(default="")
    session_cookie_name: str = Field(default="__Host-aslt_session")
    session_idle_seconds: int = Field(default=3600, ge=300, le=86400)
    session_absolute_seconds: int = Field(default=28800, ge=900, le=604800)
    oidc_issuer_url: str = Field(default="")
    oidc_client_id: str = Field(default="")
    oidc_client_secret: str = Field(default="")
    oidc_client_secret_file: str = Field(default="")
    oidc_scopes: str = Field(default="openid profile email")
    oidc_role_claim: str = Field(default="groups")
    oidc_default_role: Literal["viewer", "operator", "admin"] = Field(default="viewer")
    oidc_allowed_subjects: str = Field(default="")
    oidc_allowed_groups: str = Field(default="")
    oidc_operator_subjects: str = Field(default="")
    oidc_admin_subjects: str = Field(default="")
    oidc_operator_groups: str = Field(default="repeater-scribe-operators")
    oidc_admin_groups: str = Field(default="repeater-scribe-admins")
    oidc_http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    request_body_max_bytes: int = Field(default=65536, ge=1024, le=10485760)
    request_rate_per_minute: int = Field(default=300, ge=10, le=10000)
    anonymous_rate_per_minute: int = Field(default=60, ge=5, le=1000)
    control_rate_per_minute: int = Field(default=30, ge=1, le=600)
    sse_connections_per_identity: int = Field(default=4, ge=1, le=32)
    retention_days: int = Field(default=0, ge=0, le=36500)
    audit_retention_days: int = Field(default=180, ge=1, le=3650)
    retry_backoff_seconds: int = Field(default=5)
    max_retries: int = Field(default=5)
    ami_enabled: bool = Field(default=False)
    ami_host: str = Field(default="allstarlink3")
    ami_port: int = Field(default=5038)
    ami_username: str = Field(default="admin")
    ami_secret: str = Field(default="")
    ami_secret_file: str = Field(default="")
    ami_node_id: str = Field(default="")
    ami_timeout_seconds: float = Field(default=5.0)
    ami_reconnect_max_seconds: float = Field(default=60.0)
    ami_reconcile_seconds: float = Field(default=5.0)
    ami_event_debounce_seconds: float = Field(default=0.15)
    ami_sse_heartbeat_seconds: float = Field(default=15.0)
    ami_confirmation_timeout_seconds: float = Field(default=10.0)
    ami_connection_grace_seconds: float = Field(default=15.0)
    ami_control_enabled: bool = Field(default=False)
    ami_raw_function_enabled: bool = Field(default=False)
    api_key: str = Field(default="")
    api_key_file: str = Field(default="")
    favorite_stats_enabled: bool = Field(default=True)
    favorite_stats_base_url: str = Field(default="https://stats.allstarlink.org/api/stats")
    favorite_stats_request_interval_seconds: float = Field(default=3.0, ge=2.1)
    favorite_stats_timeout_seconds: float = Field(default=20.0, gt=0)
    favorite_stats_refresh_seconds: int = Field(default=15, ge=3)
    favorite_stats_stale_seconds: int = Field(default=300, gt=0)
    favorite_stats_recent_activity_seconds: int = Field(default=120, gt=0)
    allstar_max_requests_per_minute: int = Field(default=30, ge=1, le=600)
    topology_max_nodes: int = Field(default=200, ge=2, le=2_000)
    topology_max_depth: int = Field(default=12, ge=1, le=50)
    topology_refresh_seconds: int = Field(default=900, ge=60)
    topology_node_cache_seconds: int = Field(default=300, ge=30)
    topology_sse_heartbeat_seconds: float = Field(default=15.0, gt=0)
    topology_viewer_ttl_seconds: float = Field(default=90.0, gt=0)

    @property
    def archive_path_list(self) -> list[str]:
        return [item.strip() for item in self.archive_paths.split(",") if item.strip()]

    @property
    def known_callsign_list(self) -> list[str]:
        return [item.strip() for item in self.known_callsigns.split(",") if item.strip()]

    @staticmethod
    def _csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _secret(value: str, file_name: str) -> str:
        if file_name:
            try:
                return Path(file_name).read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return value

    @property
    def resolved_session_secret(self) -> str:
        return self._secret(self.session_secret, self.session_secret_file)

    @property
    def resolved_oidc_client_secret(self) -> str:
        return self._secret(self.oidc_client_secret, self.oidc_client_secret_file)

    @property
    def resolved_ami_secret(self) -> str:
        return self._secret(self.ami_secret, self.ami_secret_file)

    @property
    def resolved_qrz_password(self) -> str:
        return self._secret(self.qrz_password, self.qrz_password_file)

    @property
    def resolved_api_key(self) -> str:
        return self._secret(self.api_key, self.api_key_file)

    @property
    def allowed_host_list(self) -> list[str]:
        return self._csv(self.allowed_hosts)

    @property
    def cors_origin_list(self) -> list[str]:
        return self._csv(self.cors_origins)

    @property
    def public_origin(self) -> str:
        parsed = urlparse(self.public_base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def oidc_allowed_subject_list(self) -> list[str]:
        return self._csv(self.oidc_allowed_subjects)

    @property
    def oidc_operator_subject_list(self) -> list[str]:
        return self._csv(self.oidc_operator_subjects)

    @property
    def oidc_allowed_group_list(self) -> list[str]:
        return self._csv(self.oidc_allowed_groups)

    @property
    def oidc_admin_subject_list(self) -> list[str]:
        return self._csv(self.oidc_admin_subjects)

    @property
    def oidc_operator_group_list(self) -> list[str]:
        return self._csv(self.oidc_operator_groups)

    @property
    def oidc_admin_group_list(self) -> list[str]:
        return self._csv(self.oidc_admin_groups)

    @model_validator(mode="after")
    def validate_internet_mode(self) -> Settings:
        if self.deployment_mode != "internet":
            return self
        errors: list[str] = []
        if self.auth_mode != "oidc":
            errors.append("ASLT_AUTH_MODE must be oidc")
        if len(self.resolved_session_secret) < 32:
            errors.append("ASLT_SESSION_SECRET must contain at least 32 characters")
        public = urlparse(self.public_base_url)
        if public.scheme != "https" or not public.netloc:
            errors.append("ASLT_PUBLIC_BASE_URL must be an absolute https URL")
        elif public.path not in {"", "/"} or public.query or public.fragment:
            errors.append("ASLT_PUBLIC_BASE_URL must not contain a path, query, or fragment")
        issuer = urlparse(self.oidc_issuer_url)
        if issuer.scheme != "https" or not issuer.netloc:
            errors.append("ASLT_OIDC_ISSUER_URL must be an absolute https URL")
        if not self.oidc_client_id or not self.resolved_oidc_client_secret:
            errors.append("OIDC client ID and client secret are required")
        if "openid" not in self.oidc_scopes.split():
            errors.append("ASLT_OIDC_SCOPES must include openid")
        if not self.allowed_host_list or "*" in self.allowed_host_list:
            errors.append("ASLT_ALLOWED_HOSTS must be an explicit hostname allowlist")
        elif public.hostname not in self.allowed_host_list:
            errors.append("ASLT_ALLOWED_HOSTS must include the public hostname")
        if not self.session_cookie_name.startswith("__Host-"):
            errors.append("ASLT_SESSION_COOKIE_NAME must use the __Host- prefix")
        if self.session_idle_seconds > self.session_absolute_seconds:
            errors.append("session idle timeout cannot exceed its absolute timeout")
        if self.qrz_username and not self.resolved_qrz_password:
            errors.append("QRZ username requires a QRZ password")
        if self.qrz_username and urlparse(self.qrz_base_url).scheme != "https":
            errors.append("QRZ must use HTTPS in internet mode")
        if self.favorite_stats_enabled and urlparse(self.favorite_stats_base_url).scheme != "https":
            errors.append("AllStar statistics must use HTTPS in internet mode")
        if self.ami_control_enabled and not self.resolved_ami_secret:
            errors.append("AMI control requires an AMI secret")
        if errors:
            raise ValueError("Invalid internet deployment: " + "; ".join(errors))
        return self


settings = Settings()
