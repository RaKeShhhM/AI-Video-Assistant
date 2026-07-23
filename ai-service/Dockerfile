FROM python:3.11-slim

# ffmpeg is required by pydub/yt-dlp/whisper for audio extraction & conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p storage/uploads storage/downloads storage/vector_db

ENV WHISPER_MODEL=small
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
