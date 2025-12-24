import logging
import time
from pathlib import Path
from typing import Optional

from celery import Celery
from telegram import Bot
from telegram.constants import ParseMode

from app import config, processing

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

celery_app = Celery(
    "recognbot",
    broker=config.CELERY_BROKER_URL,
    backend=config.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    broker_transport_options={"visibility_timeout": 60 * 60},
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
)


def _send_failure(bot: Bot, chat_id: int, reason: str) -> None:
    try:
        bot.send_message(chat_id=chat_id, text=f"Не удалось обработать видео: {reason}")
    except Exception:
        logger.exception("Failed to send failure message to user")


@celery_app.task(bind=True, name="process_video")
def process_video(self, chat_id: int, file_id: str, file_name: Optional[str] = None) -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not configured")
        return

    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    work_dir = config.TEMP_DIR / f"{chat_id}_{int(time.time())}"
    video_path: Optional[Path] = None
    audio_path: Optional[Path] = None
    transcription_txt: Optional[Path] = None
    transcription_srt: Optional[Path] = None

    try:
        video_path = processing.download_video(bot, file_id, work_dir, file_name)
        audio_path = work_dir / "audio.wav"
        processing.extract_audio(video_path, audio_path)

        chunks = processing.split_audio(
            audio_path=audio_path,
            chunk_dir=work_dir / "chunks",
            chunk_duration_seconds=config.CHUNK_DURATION_SECONDS,
        )
        segments = processing.transcribe_chunks(chunks, config.WHISPER_MODEL)

        transcription_txt = work_dir / "transcription.txt"
        processing.write_transcription_txt(segments, transcription_txt)

        if config.ENABLE_SRT:
            transcription_srt = work_dir / "transcription.srt"
            processing.write_srt(segments, transcription_srt)

        bot.send_document(
            chat_id=chat_id,
            document=transcription_txt.open("rb"),
            filename=transcription_txt.name,
            caption="Результат распознавания",
            parse_mode=ParseMode.HTML,
        )

        if transcription_srt and transcription_srt.exists():
            bot.send_document(
                chat_id=chat_id,
                document=transcription_srt.open("rb"),
                filename=transcription_srt.name,
                caption="SRT файл с субтитрами",
                parse_mode=ParseMode.HTML,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Video processing failed")
        retries = self.request.retries
        if retries >= 2:
            _send_failure(bot, chat_id, str(exc))
            raise
        raise self.retry(exc=exc, countdown=30, max_retries=2)
    finally:
        processing.clean_workdir(work_dir)
