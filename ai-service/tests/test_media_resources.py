import asyncio
import io
import tempfile
import subprocess
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException

from utils.media_limits import MediaRejected, inspect_media, copy_upload
from utils.audio_processor import convert_to_wav, chunk_audio, youtube_filter, process_input
from utils.request_limits import ProcessLimitsMiddleware


def wav_bytes(seconds=1, rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * int(seconds * rate))
    return output.getvalue()


class MediaTests(unittest.TestCase):
    def test_real_probe_conversion_and_chunking(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.wav"
            source.write_bytes(wav_bytes())
            self.assertAlmostEqual(inspect_media(source), 1, places=2)
            converted = convert_to_wav(str(source))
            chunks = chunk_audio(converted)
            self.assertEqual(len(chunks), 1)
            with wave.open(chunks[0], "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getframerate(), audio.getsampwidth()), (1, 16000, 2))

    def test_probe_rejects_fake_wav_and_overlong_media(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.wav"
            source.write_text("not an audio file")
            with self.assertRaises(MediaRejected):
                inspect_media(source)
            source.write_bytes(wav_bytes(2))
            with self.assertRaises(MediaRejected) as error:
                inspect_media(source, max_seconds=1)
            self.assertEqual(error.exception.status, 413)

    def test_common_compressed_formats_and_video_without_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.wav"
            source.write_bytes(wav_bytes(0.2))
            for extension, codec in [("m4a", "aac"), ("webm", "libopus")]:
                media = root / f"input.{extension}"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-c:a", codec, str(media)], check=True, timeout=15)
                self.assertGreater(inspect_media(media), 0)
                self.assertTrue(Path(convert_to_wav(str(media))).exists())
            video = root / "silent.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=size=16x16:duration=0.1", "-an", "-c:v", "mpeg4", str(video)], check=True, timeout=15)
            with self.assertRaisesRegex(MediaRejected, "audio track"):
                inspect_media(video)

    def test_empty_and_playlist_uploads_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.wav"
            source.touch()
            with self.assertRaises(MediaRejected):
                inspect_media(source)
            source.write_text("#EXTM3U\n#EXTINF:10\nhttps://127.0.0.1/private\n")
            with self.assertRaises(MediaRejected):
                inspect_media(source)

    def test_copy_limit_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory, patch("utils.media_limits.MAX_BYTES", 4):
            target = Path(directory) / "copy.wav"
            with self.assertRaises(MediaRejected):
                copy_upload(io.BytesIO(b"12345"), target)
            self.assertFalse(target.exists())

    def test_failed_preparation_removes_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uploads = root / "uploads"
            downloads = root / "downloads"
            uploads.mkdir()
            downloads.mkdir()
            source = uploads / "bad.wav"
            source.write_text("bad")
            with patch.dict("os.environ", {"UPLOAD_DIR": str(uploads)}), patch("utils.audio_processor.DOWNLOAD_DIR", str(downloads)):
                with self.assertRaises(MediaRejected):
                    process_input(str(source), source_type="upload")
            self.assertEqual(list(downloads.iterdir()), [])

    def test_youtube_preflight_rejects_unbounded_work(self):
        for metadata in [{"is_live": True}, {"duration": 1801}, {"duration": 0}, {}, {"duration": 10, "filesize": 200 * 1024 * 1024}]:
            self.assertIsNotNone(youtube_filter(metadata))
        self.assertIsNone(youtube_filter({"duration": 10}))


class RequestLimitsTests(unittest.IsolatedAsyncioTestCase):
    def scope(self):
        return {"type": "http", "asgi": {"version": "3.0"}, "method": "POST", "path": "/process",
                "headers": [], "query_string": b"", "server": ("test", 80), "client": ("test", 1), "scheme": "http"}

    async def test_capacity_is_held_after_response_until_background_work_finishes(self):
        entered = asyncio.Event()
        finish = asyncio.Event()
        statuses = []

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def app(scope, receive, send):
            await JSONResponse({"ok": True})(scope, receive, send)
            entered.set()
            await finish.wait()

        middleware = ProcessLimitsMiddleware(app, max_jobs=1)
        first = asyncio.create_task(middleware(self.scope(), receive, send))
        await entered.wait()
        await middleware(self.scope(), receive, send)
        self.assertEqual(statuses, [200, 429])
        finish.set()
        await first
        await middleware(self.scope(), receive, send)
        self.assertEqual(statuses, [200, 429, 200])

    async def test_chunked_body_cap_closes_partially_spooled_upload(self):
        await self.check_partial_upload_cleanup(disconnect=False)

    async def test_disconnect_closes_partially_spooled_upload(self):
        await self.check_partial_upload_cleanup(disconnect=True)

    async def check_partial_upload_cleanup(self, disconnect):
        scope = self.scope()
        scope["headers"] = [(b"content-type", b"multipart/form-data; boundary=s3")]
        chunks = [b'--s3\r\nContent-Disposition: form-data; name="file"; filename="x.wav"\r\nContent-Type: audio/wav\r\n\r\n123', b"x" * 2048]
        files = []
        statuses = []
        real_spool = tempfile.SpooledTemporaryFile

        def track_spool(*args, **kwargs):
            result = real_spool(*args, **kwargs)
            files.append(result)
            return result

        async def receive():
            if disconnect and len(chunks) == 1:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        async def app(scope, receive, send):
            try:
                async with Request(scope, receive).form(max_files=1, max_fields=4):
                    self.fail("Oversized input reached the endpoint")
            except HTTPException as error:
                await JSONResponse({"detail": str(error)}, status_code=error.status_code)(scope, receive, send)

        # Starlette translates parser exceptions to HTTP errors for app scopes.
        scope["app"] = object()
        with patch("starlette.formparsers.SpooledTemporaryFile", side_effect=track_spool):
            await ProcessLimitsMiddleware(app, max_bytes=1024)(scope, receive, send)
        self.assertEqual(statuses, [400 if disconnect else 413])
        self.assertTrue(files)
        self.assertTrue(all(file.closed for file in files))
