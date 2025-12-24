# recognBOT

Telegram-бот для распознавания русской речи из видеофайлов. Проект разворачивается в Docker и использует Celery + Redis для фоновой обработки.

## Возможности
- Приём видеофайлов (mp4, mov, mkv, avi) через Telegram.
- Асинхронная постановка задач в очередь Redis и обработка воркером Celery.
- Извлечение аудио через ffmpeg, конвертация в WAV 16 kHz mono и нарезка на чанки 5–10 минут.
- Распознавание русской речи с помощью Whisper.
- Формирование файлов `transcription.txt` (с таймкодами) и `transcription.srt` (опционально).
- Оповещения о ходе обработки и об ошибках.

## Требования
- Docker и Docker Compose.
- Telegram Bot Token.

## Конфигурация
Заполните файл окружения, основываясь на примере:

```bash
cp .env.example .env
```

Переменные:
- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота.
- `REDIS_URL` — адрес Redis (по умолчанию `redis://redis:6379/0`).
- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` — параметры брокера/бэкенда Celery.
- `WHISPER_MODEL` — имя модели Whisper (например, `tiny`, `base`, `small`).
- `CHUNK_DURATION_SECONDS` — длительность аудио-чанка в секундах (5–10 минут, по умолчанию 600).
- `ENABLE_SRT` — `true/false`, сохранять ли SRT.

## Запуск
Соберите и запустите сервисы:

```bash
docker compose up --build -d
```

Остановить сервисы:

```bash
docker compose down
```

Посмотреть логи:

```bash
docker compose logs -f
```

## Структура
- `app/bot.py` — Telegram-бот на long polling.
- `app/tasks.py` — Celery-воркер и задача обработки видео.
- `app/processing.py` — вспомогательная логика для ffmpeg, нарезки аудио и распознавания.
- `docker-compose.yml` — сервисы bot, worker и redis.
- `Dockerfile` — сборка образа с Python, ffmpeg и зависимостями.
- `requirements.txt` — Python-зависимости.

## Примечания по обработке
- Видео скачивается в контейнер воркера и очищается после завершения задачи.
- Ошибки скачивания, ffmpeg или распознавания корректно обрабатываются и сообщаются пользователю.
