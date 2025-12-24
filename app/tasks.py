import logging
import time
from pathlib import Path
from typing import Optional

from celery import Celery
from telegram import Bot
from telegram.constants import ParseMode

from app import config, processing
from redis import Redis

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

QUEUE_KEY = "recognbot:queue"
QUEUE_META_PREFIX = "recognbot:queue:meta:"
QUEUE_NOTIFY_INTERVAL = 30
TRANSCRIBE_NOTIFY_STEPS = 5


def _get_redis() -> Redis:
    return Redis.from_url(config.REDIS_URL, decode_responses=True)


def enqueue_job(job_id: str, chat_id: int, file_name: Optional[str]) -> int:
    client = _get_redis()
    position = client.rpush(QUEUE_KEY, job_id)
    client.hset(
        f"{QUEUE_META_PREFIX}{job_id}",
        mapping={
            "chat_id": chat_id,
            "file_name": file_name or "",
            "enqueued_at": int(time.time()),
        },
    )
    logger.info("Enqueued job %s at position %s", job_id, position)
    return position


def _remove_job(job_id: str) -> None:
    client = _get_redis()
    removed = client.lrem(QUEUE_KEY, 0, job_id)
    client.delete(f"{QUEUE_META_PREFIX}{job_id}")
    if removed:
        logger.info("Removed job %s from queue", job_id)


def _send_status(bot: Bot, chat_id: int, text: str) -> None:
    try:
        bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        logger.exception("Failed to send status message to user")


def _wait_for_turn(job_id: str, bot: Bot, chat_id: int) -> None:
    client = _get_redis()
    last_notified = 0.0
    last_position: Optional[int] = None

    while True:
        head = client.lindex(QUEUE_KEY, 0)
        if head == job_id:
            logger.info("Job %s is now at the head of the queue", job_id)
            return

        position = client.lpos(QUEUE_KEY, job_id)
        if position is None:
            # Reinsert the job at the end if it disappeared unexpectedly.
            logger.warning("Job %s not found in queue; reinserting", job_id)
            client.rpush(QUEUE_KEY, job_id)
            position = client.lpos(QUEUE_KEY, job_id)

        if position is not None:
            position_display = position + 1
            now = time.time()
            if position_display != last_position or now - last_notified >= QUEUE_NOTIFY_INTERVAL:
                _send_status(
                    bot,
                    chat_id,
                    f"Ожидание обработки. Ваша позиция в очереди: {position_display}.",
                )
                last_position = position_display
                last_notified = now
        time.sleep(5)


def _send_failure(bot: Bot, chat_id: int, reason: str) -> None:
    try:
        bot.send_message(chat_id=chat_id, text=f"Не удалось обработать видео: {reason}")
    except Exception:
        logger.exception("Failed to send failure message to user")


@celery_app.task(bind=True, name="process_video")
def process_video(
    self, job_id: str, chat_id: int, file_id: str, file_name: Optional[str] = None
) -> None:
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
        _wait_for_turn(job_id, bot, chat_id)

        total_steps = 5
        current_step = 1
        _send_status(
            bot,
            chat_id,
            f"Обработка началась. Шаг {current_step}/{total_steps}: скачиваем видео (0%).",
        )
        video_path = processing.download_video(bot, file_id, work_dir, file_name)
        audio_path = work_dir / "audio.wav"

        current_step += 1
        _send_status(
            bot,
            chat_id,
            f"Шаг {current_step}/{total_steps}: извлекаем аудио из видео (25%).",
        )
        processing.extract_audio(video_path, audio_path)

        current_step += 1
        chunks = processing.split_audio(
            audio_path=audio_path,
            chunk_dir=work_dir / "chunks",
            chunk_duration_seconds=config.CHUNK_DURATION_SECONDS,
        )

        _send_status(
            bot,
            chat_id,
            f"Шаг {current_step}/{total_steps}: аудио разделено на {len(chunks)} частей (40%).",
        )

        current_step += 1
        progress_state = {"last_percent": 40}

        def _on_transcribe_progress(done: int, total: int) -> None:
            if total <= 0:
                return
            percent = 45 + int(35 * done / total)
            percent = min(percent, 80)
            if percent - progress_state["last_percent"] >= 10 or done == total:
                _send_status(
                    bot,
                    chat_id,
                    f"Шаг {current_step}/{total_steps}: распознаём аудио "
                    f"({percent}%). Обработано частей: {done}/{total}.",
                )
                progress_state["last_percent"] = percent

        segments = processing.transcribe_chunks(
            chunks,
            config.WHISPER_MODEL,
            progress_callback=_on_transcribe_progress,
        )

        transcription_txt = work_dir / "transcription.txt"
        processing.write_transcription_txt(segments, transcription_txt)

        if config.ENABLE_SRT:
            transcription_srt = work_dir / "transcription.srt"
            processing.write_srt(segments, transcription_srt)

        _send_status(
            bot,
            chat_id,
            f"Шаг {current_step}/{total_steps}: формируем файлы с результатом (85%).",
        )

        current_step += 1
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
        _send_status(
            bot,
            chat_id,
            f"Готово! Отправляем результаты. Шаг {current_step}/{total_steps} (100%).",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Video processing failed")
        retries = self.request.retries
        if retries >= 2:
            _send_failure(bot, chat_id, str(exc))
            raise
        raise self.retry(exc=exc, countdown=30, max_retries=2)
    finally:
        _remove_job(job_id)
        processing.clean_workdir(work_dir)
