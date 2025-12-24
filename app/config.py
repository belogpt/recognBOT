import os
from pathlib import Path


def _clamp_chunk_duration(value: int) -> int:
    """Clamp chunk duration to the 5–10 minute range."""
    return max(300, min(600, value))


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")

CHUNK_DURATION_SECONDS = _clamp_chunk_duration(
    int(os.getenv("CHUNK_DURATION_SECONDS", "600"))
)
ENABLE_SRT = os.getenv("ENABLE_SRT", "true").lower() in {"1", "true", "yes", "on"}

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}

TEMP_DIR = Path(os.getenv("TEMP_DIR", "/app/data"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)
