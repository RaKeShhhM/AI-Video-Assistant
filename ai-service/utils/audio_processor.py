import os
import math
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from utils.job_ids import validate_job_id
from utils.video_source import normalize_youtube_url
from utils.youtube_network import RestrictedYoutubeDL
from utils.media_limits import MAX_BYTES, MAX_SECONDS, INPUT_OPTIONS, MediaRejected, inspect_media

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "storage/downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def youtube_filter(info, *, incomplete=False, max_seconds=MAX_SECONDS):
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        return "Live streams are not supported."
    duration = info.get("duration")
    if duration is None:
        return None if incomplete else "Video must have a known duration."
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0 or duration > max_seconds:
        return f"Video exceeds the {max_seconds // 60}-minute processing limit."
    if (info.get("filesize") or info.get("filesize_approx") or 0) > MAX_BYTES:
        return "Video exceeds the download size limit."


def download_youtube_audio(url: str, *, output_dir, max_seconds=MAX_SECONDS) -> str:
    url = normalize_youtube_url(url)
    downloaded_paths = []

    def check_bytes(progress):
        if progress.get("downloaded_bytes", 0) > MAX_BYTES:
            raise MediaRejected("Video exceeds the download size limit.", 413)

    options = {
        "format": "bestaudio[protocol=https]/best[protocol=https]", "proxy": "",
        "outtmpl": str(Path(output_dir).resolve() / "source.%(ext)s"),
        "js_runtimes": {"deno": {}, "node": {}}, "noplaylist": True,
        "post_hooks": [downloaded_paths.append], "progress_hooks": [check_bytes],
        "max_filesize": MAX_BYTES, "socket_timeout": 15, "retries": 2,
        "buffersize": 65536, "noresizebuffer": True,
        "match_filter": lambda info, *, incomplete=False: youtube_filter(info, incomplete=incomplete, max_seconds=max_seconds),
        "quiet": True,
    }
    with RestrictedYoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True, ie_key="Youtube")
    if not downloaded_paths or not os.path.isfile(downloaded_paths[-1]):
        raise RuntimeError("YouTube download did not produce an audio file (check duration, size and availability).")
    final_path = Path(downloaded_paths[-1]).resolve()
    if final_path.parent != Path(output_dir).resolve():
        raise ValueError("Downloaded media escaped its workspace.")
    if os.path.getsize(final_path) > MAX_BYTES:
        raise MediaRejected("Video exceeds the download size limit.", 413)
    return str(final_path)


def convert_to_wav(input_path: str, *, output_path=None, max_seconds=MAX_SECONDS) -> str:
    inspect_media(input_path, max_seconds)
    output_path = output_path or os.path.splitext(input_path)[0] + "_converted.wav"
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *INPUT_OPTIONS,
            "-i", str(Path(input_path).resolve()), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", "-threads", "1", "-t", str(max_seconds + 1), str(output_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120)
        with wave.open(str(output_path), "rb") as audio:
            if audio.getnframes() / audio.getframerate() > max_seconds:
                raise MediaRejected("Decoded audio exceeds the duration limit.", 413)
    except BaseException:
        Path(output_path).unlink(missing_ok=True)
        raise
    return str(output_path)


def chunk_audio(wav_path: str, chunk_minutes: int = 10) -> list:
    chunks = []
    with wave.open(str(wav_path), "rb") as audio:
        while True:
            # At most one 10-minute mono/16kHz/16-bit chunk (~19.2 MB).
            frames = audio.readframes(chunk_minutes * 60 * audio.getframerate())
            if not frames:
                break
            path = f"{wav_path}_chunk_{len(chunks)}.wav"
            with wave.open(path, "wb") as output:
                output.setparams(audio.getparams())
                output.writeframes(frames)
            chunks.append(path)
    if not chunks:
        raise MediaRejected("Audio contains no samples.")
    return chunks


class MediaWorkspace:
    """Own one generated directory for one execution, independent of chunk output."""

    def __init__(self, job_id):
        validate_job_id(job_id)
        self.root = Path(DOWNLOAD_DIR).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=f"work-{job_id}-", dir=self.root))

    def cleanup(self):
        # Resolve and check the exact target before recursive removal, including
        # on Windows. Never discover ownership by following returned chunk paths.
        target = self.path.resolve()
        if target != self.path or target.parent != self.root:
            raise ValueError("Invalid media cleanup directory.")
        if target.exists():
            shutil.rmtree(target)


def cleanup_upload(source, source_type):
    if source_type == "upload":
        path = Path(source).resolve()
        root = Path(os.getenv("UPLOAD_DIR", "storage/uploads")).resolve()
        if path.parent == root:
            path.unlink(missing_ok=True)


def process_input(source: str, *, source_type: str, workspace=None, max_seconds=MAX_SECONDS) -> list:
    if source_type == "youtube":
        source = normalize_youtube_url(source)
    elif source_type == "upload":
        upload_root = Path(os.getenv("UPLOAD_DIR", "storage/uploads")).resolve()
        upload = Path(source).resolve()
        if not upload.is_relative_to(upload_root) or not upload.is_file():
            raise ValueError("Upload must be a server-created file inside the upload directory.")
        source = str(upload)
    else:
        raise ValueError("Unsupported source type.")
    if not isinstance(workspace, MediaWorkspace):
        raise ValueError("An owned media workspace is required.")
    work = workspace.path
    try:
        if source_type == "youtube":
            source = download_youtube_audio(source, output_dir=work, max_seconds=max_seconds)
        else:
            # The ingress upload is server-generated; its working copy belongs
            # exclusively to this execution, including concurrent retries.
            local_source = work / ("source" + Path(source).suffix.lower())
            shutil.copyfile(source, local_source)
            source = str(local_source)
        wav = convert_to_wav(source, output_path=work / "audio.wav", max_seconds=max_seconds)
        return chunk_audio(wav)
    except BaseException:
        workspace.cleanup()
        raise
