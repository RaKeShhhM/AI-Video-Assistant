"""Load and validate service configuration before model/storage imports.

Injected environment variables take precedence over the service-local .env.
Consumers may retain their module constants: startup populates and validates the
complete environment before importing them. No model is loaded here.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
from utils.internal_security import InternalSettings, load_internal_settings

SERVICE_DIR = Path(__file__).resolve().parent


def bounded_integer(name, default, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        raise RuntimeError(f"Invalid {name}: expected an integer from 1 to {maximum}.") from None
    if not 1 <= value <= maximum:
        raise RuntimeError(f"Invalid {name}: expected an integer from 1 to {maximum}.")
    return value


def text_setting(name, default=None, *, optional=False):
    value = os.getenv(name, default)
    if optional and not value:
        return None
    if (not value or len(value) > 512 or value != value.strip()
            or any(ord(char) < 33 for char in value)
            or any(marker in value.lower() for marker in ("your_", "change_this", "replace_me", "changeme"))):
        raise RuntimeError(f"{name} must be a non-placeholder value without whitespace.")
    return value


@dataclass(frozen=True)
class Settings:
    internal: InternalSettings
    mistral_api_key: str = field(repr=False)
    sarvam_api_key: str | None = field(repr=False)
    whisper_model: str
    sarvam_model: str
    upload_dir: Path
    download_dir: Path
    vector_db_dir: Path
    max_upload_mb: int
    max_media_seconds: int
    max_ai_jobs: int
    max_ai_asks: int


def load_settings():
    configured_file = os.getenv("AI_ENV_FILE")
    env_file = Path(configured_file) if configured_file else SERVICE_DIR / ".env"
    if configured_file and (not env_file.is_absolute() or not env_file.is_file()):
        raise RuntimeError("AI_ENV_FILE must name an existing absolute file path.")
    load_dotenv(env_file, override=False)
    internal = load_internal_settings()
    mistral = text_setting("MISTRAL_API_KEY")
    sarvam = text_setting("SARVAM_API_KEY", optional=True)
    whisper = text_setting("WHISPER_MODEL", "small")
    if whisper not in {"tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large", "large-v1", "large-v2", "large-v3", "large-v3-turbo", "turbo"}:
        raise RuntimeError("WHISPER_MODEL must be a supported Whisper model name.")
    sarvam_model = text_setting("SARVAM_STT_MODEL", "saaras:v2.5")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", sarvam_model):
        raise RuntimeError("Invalid SARVAM_STT_MODEL.")
    paths = []
    for name, default in [("UPLOAD_DIR", "storage/uploads"), ("DOWNLOAD_DIR", "storage/downloads"), ("VECTOR_DB_DIR", "storage/vector_db")]:
        value = os.getenv(name, default)
        if not value or value != value.strip() or any(ord(char) < 32 for char in value):
            raise RuntimeError(f"Invalid {name}.")
        path = Path(value).expanduser()
        path = (SERVICE_DIR / path).resolve() if not path.is_absolute() else path.resolve()
        if path.exists() and not path.is_dir():
            raise RuntimeError(f"{name} must name a directory.")
        paths.append(path)
    for index, path in enumerate(paths):
        if any(path == other or path in other.parents or other in path.parents for other in paths[index + 1:]):
            raise RuntimeError("UPLOAD_DIR, DOWNLOAD_DIR and VECTOR_DB_DIR must be separate non-nested directories.")
    limits = [bounded_integer(*args) for args in [("MAX_UPLOAD_MB", 100, 500), ("MAX_MEDIA_SECONDS", 1800, 7200), ("MAX_AI_JOBS", 1, 4), ("MAX_AI_ASKS", 2, 8)]]
    # Publish normalized paths only after all validation succeeds. No directories
    # are created here; invalid configuration stops before filesystem side effects.
    for name, path in zip(["UPLOAD_DIR", "DOWNLOAD_DIR", "VECTOR_DB_DIR"], paths):
        os.environ[name] = str(path)
    return Settings(internal, mistral, sarvam, whisper, sarvam_model, *paths, *limits)
