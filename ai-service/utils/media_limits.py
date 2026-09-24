"""Bounded local probing/decoding. Never permit network protocols or playlists."""
import json
import math
import os
from pathlib import Path
import subprocess


def setting(name, default, maximum):
    value = int(os.getenv(name, default))
    if not 1 <= value <= maximum:
        raise RuntimeError(f"Invalid {name}")
    return value


MAX_BYTES = setting("MAX_UPLOAD_MB", 100, 500) * 1024 * 1024
MAX_SECONDS = setting("MAX_MEDIA_SECONDS", 1800, 7200)
MAX_AI_JOBS = setting("MAX_AI_JOBS", 1, 4)
MAX_AI_ASKS = setting("MAX_AI_ASKS", 2, 8)
EXTENSIONS = {".mp4", ".m4a", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".ogg", ".flac", ".aac"}
DEMUXERS = "mov,matroska,webm,mp3,wav,ogg,flac,aac"
INPUT_OPTIONS = ["-protocol_whitelist", "file,pipe", "-format_whitelist", DEMUXERS,
                 "-probesize", "5000000", "-analyzeduration", "5000000"]


class MediaRejected(ValueError):
    def __init__(self, message, status=415):
        super().__init__(message)
        self.status = status


def inspect_media(path, max_seconds=MAX_SECONDS):
    path = Path(path).resolve()
    if path.suffix.lower() not in EXTENSIONS:
        raise MediaRejected("Unsupported media extension.")
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise MediaRejected("Media is empty or exceeds the upload size limit.", 413)
    try:
        result = subprocess.run(["ffprobe", "-v", "error", *INPUT_OPTIONS,
            "-show_entries", "format=duration,format_name:stream=codec_type", "-of", "json", str(path)],
            capture_output=True, timeout=15, check=True)
        info = json.loads(result.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
    except FileNotFoundError as exc:
        raise RuntimeError("FFprobe is required on the AI service PATH.") from exc
    except (subprocess.SubprocessError, ValueError, TypeError) as exc:
        raise MediaRejected("Invalid or unsupported media, or media probing timed out.") from exc
    if not any(stream.get("codec_type") == "audio" for stream in info.get("streams", [])):
        raise MediaRejected("The media must contain an audio track.")
    if not math.isfinite(duration) or duration <= 0:
        raise MediaRejected("Media must have a known positive duration.")
    if duration > max_seconds:
        raise MediaRejected(f"Media exceeds the {max_seconds // 60}-minute processing limit.", 413)
    return duration


def copy_upload(file, destination):
    """Called in a thread, with bounded buffers and cleanup on copy failure."""
    total = 0
    try:
        with open(destination, "xb") as output:
            while chunk := file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise MediaRejected("File exceeds the upload size limit.", 413)
                output.write(chunk)
        return total
    except BaseException:
        Path(destination).unlink(missing_ok=True)
        raise
