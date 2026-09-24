"""Validate public source identifiers without accessing files or the network."""
import re
from urllib.parse import parse_qs, urlsplit

HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
INVALID_URL = "Provide an HTTPS YouTube video URL (watch, youtu.be, shorts, embed, or live)."


def normalize_youtube_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(INVALID_URL)
    value = value.strip()
    if not value.lower().startswith("https://") or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError(INVALID_URL)
    try:
        url = urlsplit(value)
        authority = url.netloc.lower()
        if authority.removesuffix(":443") not in HOSTS or url.hostname not in HOSTS:
            raise ValueError(INVALID_URL)
        if "%" in url.path or ".." in url.path:
            raise ValueError(INVALID_URL)
        query = parse_qs(url.query, keep_blank_values=True)
        if any(key.lower() == "list" for key in query) or url.path == "/playlist":
            raise ValueError("Playlists are not supported. Submit a single video without the list parameter.")
        video_id = None
        if url.hostname == "youtu.be":
            match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", url.path)
            video_id = match[1] if match else None
        elif url.path == "/watch":
            ids = query.get("v", [])
            video_id = ids[0] if len(ids) == 1 else None
        else:
            match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?", url.path)
            video_id = match[1] if match else None
        if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError(INVALID_URL)
    except (TypeError, AttributeError) as exc:
        raise ValueError(INVALID_URL) from exc
    return f"https://www.youtube.com/watch?v={video_id}"


def validate_video_source(youtube_url, file):
    has_url = youtube_url is not None
    if has_url == (file is not None):
        raise ValueError("Provide exactly one source: youtube_url or a file upload.")
    return ("youtube", normalize_youtube_url(youtube_url)) if has_url else ("upload", None)
