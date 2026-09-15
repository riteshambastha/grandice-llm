import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    ollama_base_url: str = "http://127.0.0.1:11434"
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 8080
    admin_token: str = ""
    default_rpm_limit: int = 120
    request_timeout: float = 600.0
    max_concurrent_requests: int = 2
    cors_origins: str = "*"
    inject_stream_usage: bool = True
    privacy_media_max_bytes: int = 50 * 1024 * 1024
    privacy_whisper_model_path: Path = (
        Path.home()
        / ".grandice"
        / "models"
        / "faster-whisper-large-v3-turbo"
    )

    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""
    contact_recipient: str = "ritesh.linux@gmail.com"
    contact_form_endpoint: str = "https://formsubmit.co/ajax/ritesh.linux@gmail.com"

    database_path: Path = ROOT / "data" / "gateway.db"
    backup_enabled: bool = True
    backup_interval_hours: int = 24
    backup_local_retention_days: int = 30
    backup_weekly_retention_weeks: int = 12
    backup_encryption_key: str = ""
    backup_dir: Path = ROOT / "backups"
    backup_status_path: Path = ROOT / "data" / "backup-status.json"
    r2_backup_enabled: bool = False
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "grandice-backups"
    r2_prefix: str = "database"
    models_config_path: Path = ROOT / "config" / "models.json"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _models_config() -> dict:
    path = get_settings().models_config_path
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_aliases() -> dict[str, str]:
    """Friendly names applications can use instead of raw Ollama model tags.

    Indirection here means swapping the model behind "chat" is a config edit
    rather than a redeploy of every calling application.
    """
    return _models_config().get("aliases", {})


def default_reasoning_effort(model: str) -> str | None:
    """Effort level to apply when the caller did not specify one.

    Returns None for models that have no reasoning mode, leaving their request
    bodies untouched.
    """
    reasoning = _models_config().get("reasoning", {})
    effort = reasoning.get("default_effort")
    if not effort:
        return None
    families = reasoning.get("applies_to", [])
    name = model.lower()
    return effort if any(name.startswith(f) for f in families) else None


def resolve_model(name: str | None) -> str | None:
    if name is None:
        return None
    return load_aliases().get(name, name)
