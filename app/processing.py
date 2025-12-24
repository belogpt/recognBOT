import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

import whisper
from pydub import AudioSegment

from app import config

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    start: float
    end: float
    text: str


def download_video(bot, file_id: str, target_dir: Path, file_name_hint: Optional[str]) -> Path:
    """Download the video from Telegram and return the stored path."""
    target_dir.mkdir(parents=True, exist_ok=True)
    file = bot.get_file(file_id)
    extension = Path(file.file_path or "").suffix or (
        Path(file_name_hint).suffix if file_name_hint else ""
    )
    if extension.lower() not in config.SUPPORTED_EXTENSIONS:
        extension = ".mp4"
    filename = f"{uuid.uuid4()}{extension}"
    video_path = target_dir / filename
    logger.info("Downloading video to %s", video_path)
    file.download_to_drive(custom_path=str(video_path))
    return video_path


def extract_audio(video_path: Path, audio_path: Path) -> None:
    """Extract mono 16 kHz WAV audio from a video file."""
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ]
    logger.info("Extracting audio via ffmpeg")
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if result.returncode != 0:
        logger.error("ffmpeg failed: %s", result.stderr.decode("utf-8", "ignore"))
        raise RuntimeError("Failed to extract audio with ffmpeg")


def split_audio(audio_path: Path, chunk_dir: Path, chunk_duration_seconds: int) -> List[Path]:
    """Split long audio into sequential chunks."""
    chunk_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Splitting audio into %s-second chunks", chunk_duration_seconds)
    audio = AudioSegment.from_file(audio_path)
    chunk_length_ms = chunk_duration_seconds * 1000
    chunks: List[Path] = []
    for index, start_ms in enumerate(range(0, len(audio), chunk_length_ms)):
        end_ms = min(start_ms + chunk_length_ms, len(audio))
        chunk_audio = audio[start_ms:end_ms]
        chunk_path = chunk_dir / f"chunk_{index:04d}.wav"
        chunk_audio.export(chunk_path, format="wav", parameters=["-ac", "1", "-ar", "16000"])
        chunks.append(chunk_path)
    return chunks


def _format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_srt_timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, remainder = divmod(millis, 3600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def transcribe_chunks(
    chunk_paths: Sequence[Path],
    model_name: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[Segment]:
    """Run Whisper on each chunk and aggregate segments with corrected timestamps."""
    if not chunk_paths:
        return []

    logger.info("Loading Whisper model %s", model_name)
    model = whisper.load_model(model_name)
    segments: List[Segment] = []
    offset = 0.0

    total = len(chunk_paths)
    for index, chunk_path in enumerate(chunk_paths, start=1):
        logger.info("Transcribing %s", chunk_path.name)
        result = model.transcribe(str(chunk_path), language="ru")
        for seg in result.get("segments", []):
            segments.append(
                Segment(
                    start=seg["start"] + offset,
                    end=seg["end"] + offset,
                    text=seg["text"].strip(),
                )
            )
        audio = AudioSegment.from_file(chunk_path)
        offset += len(audio) / 1000.0
        if progress_callback:
            progress_callback(index, total)
    return segments


def write_transcription_txt(segments: Iterable[Segment], target: Path) -> None:
    with target.open("w", encoding="utf-8") as handle:
        for seg in segments:
            handle.write(
                f"[{_format_timestamp(seg.start)} - {_format_timestamp(seg.end)}] {seg.text}\n"
            )


def write_srt(segments: Iterable[Segment], target: Path) -> None:
    with target.open("w", encoding="utf-8") as handle:
        for index, seg in enumerate(segments, start=1):
            start = _format_srt_timestamp(seg.start)
            end = _format_srt_timestamp(seg.end)
            handle.write(f"{index}\n")
            handle.write(f"{start} --> {end}\n")
            handle.write(f"{seg.text}\n\n")


def clean_workdir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
