from pydub import AudioSegment
import os
from pathlib import Path

from utils.video_source import normalize_youtube_url
from utils.youtube_network import RestrictedYoutubeDL

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "storage/downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def download_youtube_audio(url :str) ->str:
    url = normalize_youtube_url(url)
    output_path = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")
    downloaded_paths = []
    ydl_opts = {
        # Direct HTTPS media keeps fetching inside the guarded transport;
        # remote manifests/external FFmpeg downloaders cannot bypass it.
        "format": "bestaudio[protocol=https]/best[protocol=https]",
        "proxy": "",
        "outtmpl": output_path,
        # Python embedding does not use the yt-dlp CLI configuration.
        # Deno is the default runtime; explicitly enable installed Node too.
        "js_runtimes": {"deno": {}, "node": {}},
        "noplaylist": True,
        "post_hooks": [downloaded_paths.append],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }
        ],
        "quiet": True,
    }
    with RestrictedYoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True, ie_key="Youtube")
    # Use the final path after FFmpeg conversion, regardless of input format.
    if not downloaded_paths or not os.path.isfile(downloaded_paths[-1]):
        raise RuntimeError("YouTube download did not produce an audio file.")
    return downloaded_paths[-1]

# download_youtube_audio("https://www.youtube.com/watch?v=_Q-e_nczWqM&t=223s")

def convert_to_wav(input_path: str) -> str:
    """Convert any audio/video file to WAV format using pydub."""
    output_path = os.path.splitext(input_path)[0] + "_converted.wav"
    audio = AudioSegment.from_file(input_path)
    audio = audio.set_channels(1).set_frame_rate(16000) #16khz
    audio.export(output_path, format="wav")
    return output_path



def chunk_audio(wav_path : str , chunk_minutes : int = 10) -> list:
    audio = AudioSegment.from_wav(wav_path)
    chunk_ms = chunk_minutes * 60 * 1000 

    chunks = []

    for i, start in enumerate(range(0,len(audio),chunk_ms)):
        chunk = audio[start : start + chunk_ms]
        chunk_path = f"{wav_path}_chunk_{i}.wav"
        chunk.export(chunk_path , format = "wav")

        chunks.append(chunk_path)
    
    return chunks

def process_input(source: str, *, source_type: str) -> list:
    if source_type == "youtube":
        print("Detected YouTube URL. Downloading audio...")
        wav_path = download_youtube_audio(source)
    elif source_type == "upload":
        upload_root = Path(os.getenv("UPLOAD_DIR", "storage/uploads")).resolve()
        upload = Path(source).resolve()
        if not upload.is_relative_to(upload_root) or not upload.is_file():
            raise ValueError("Upload must be a server-created file inside the upload directory.")
        print("Detected local file. Converting to WAV...")
        wav_path = convert_to_wav(str(upload))
    else:
        raise ValueError("Unsupported source type.")

    print("Chunking audio...")
    chunks = chunk_audio(wav_path)
    print(f"Audio ready — {len(chunks)} chunk(s) created.")
    return chunks
