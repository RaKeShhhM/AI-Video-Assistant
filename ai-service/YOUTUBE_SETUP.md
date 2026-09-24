# YouTube download setup

The AI service needs FFmpeg, a supported JavaScript runtime, and yt-dlp's matching EJS challenge scripts. Node 22+ on PATH is supported and explicitly enabled by `utils/audio_processor.py`; Deno is also enabled. The Dockerfile includes Node.

From `ai-service`, with its virtual environment activated:

```sh
python -m pip install -r requirements.txt
node --version
ffmpeg -version
python -m uvicorn main:app --reload --port 8000
```

Stop the old AI server before restarting it. Submit a new job; previously failed jobs do not automatically retry.

The `default` extra installs a compatible `yt-dlp-ejs`. Installing plain `yt-dlp` alone does not provide that dependency. Updating the command-line configuration alone does not configure the application's embedded Python downloader. The version in requirements is pinned to match the S1 restricted networking adapter; rerun the source and network tests when deliberately upgrading it.

To inspect extraction without running transcription or calling the LLM:

```sh
python -m yt_dlp --ignore-config --js-runtimes node --no-playlist --simulate --verbose "YOUTUBE_URL"
```

Simulation verifies extraction but does not prove that the media download succeeds. A missing-runtime warning should disappear. A remaining 403 can depend on the video, YouTube's current challenge/token requirements, or the network/IP. Preserve the new diagnostic output and check the specific URL before changing extractor clients or authentication settings. Do not put personal browser cookies into the repository or a shared server.

`POST /process` returning 200 means the background job was accepted; it does not mean the video downloaded successfully.

Run the focused regression checks from `ai-service`:

```sh
python -m unittest discover -s tests -v
```

Official setup reference: [yt-dlp EJS guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS).
