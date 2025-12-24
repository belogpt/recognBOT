import logging
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import Application, CallbackContext, CommandHandler, MessageHandler, filters

from app import config
from app.tasks import process_video

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _is_supported(filename: Optional[str]) -> bool:
    if not filename:
        return False
    return Path(filename).suffix.lower() in config.SUPPORTED_EXTENSIONS


async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Отправьте видеофайл (mp4, mov, mkv, avi), и я пришлю текст с распознаванием речи."
    )


async def handle_video(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    if not message:
        return

    attachment = message.effective_attachment
    if isinstance(attachment, list) and attachment:
        attachment = attachment[-1]
    file_id = None
    file_name = None

    if attachment is None:
        await message.reply_text("Не удалось получить файл.")
        return

    if hasattr(attachment, "file_name"):
        file_name = getattr(attachment, "file_name", None)

    if hasattr(attachment, "file_id"):
        file_id = attachment.file_id

    if file_name and not _is_supported(file_name):
        await message.reply_text(
            "Формат файла не поддерживается. Отправьте видео в одном из форматов: mp4, mov, mkv, avi."
        )
        return

    if not file_id:
        await message.reply_text("Не удалось получить видео. Попробуйте ещё раз.")
        return

    await message.reply_text("Видео принято в обработку. Ожидайте результат.")
    process_video.delay(message.chat_id, file_id, file_name)


def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    video_filter = filters.VIDEO | (filters.Document.VIDEO & ~filters.Document.IMAGE)
    application.add_handler(MessageHandler(video_filter, handle_video))

    logger.info("Starting bot polling")
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
