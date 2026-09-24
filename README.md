# Reel — AI Video Assistant (MERN + Python AI service)

Turns a YouTube link or an uploaded video/audio file into a transcript,
summary, action items, key decisions, open questions, and a chat interface
grounded in the video (RAG). Originally a Python CLI script — now a full
web app with accounts and live processing status.

## Architecture

```
┌────────────┐      HTTP + Socket.io      ┌──────────────┐      internal HTTP      ┌────────────────┐
│   React    │ ─────────────────────────▶ │   Express    │ ───────────────────────▶│   FastAPI       │
│  (client)  │ ◀───── job:update ──────── │ + MongoDB    │ ◀──── progress callback ─│  (ai-service)   │
└────────────┘                            └──────────────┘                          └────────────────┘
  Dashboard,                                Auth (JWT),                               Whisper / Sarvam,
  live VU-meter                             Job model,                                Mistral (LangChain),
  progress, chat UI                         Socket.io rooms                           ChromaDB (per-job)
```

**Why not pure JavaScript?** The AI pipeline (Whisper, LangChain, ChromaDB,
yt-dlp) is mature and battle-tested in Python — rewriting it in JS would mean
losing quality for no real benefit. So the app is "full stack MERN" from the
product's point of view — React, Express, and MongoDB own the UI, users, and
data — while a small Python microservice does the actual AI work and is
treated as an internal implementation detail, the same way you'd treat any
external inference service.

### Request flow for a new video

1. Client uploads a file or pastes a YouTube URL → `POST /api/videos`.
2. Express creates a `Job` document (`status: queued`) and responds
   immediately with the job id.
3. Express forwards the source to the FastAPI service's `/process` endpoint,
   which accepts it and runs the pipeline in a background task.
4. After each pipeline stage (download → transcribe → summarize → extract
   insights → build RAG index), FastAPI POSTs progress back to Express's
   internal callback route (`/api/internal/jobs/progress`, protected by a
   shared secret).
5. Express updates the `Job` document and emits a `job:update` Socket.io
   event to that user's private room.
6. The client updates the dashboard and job detail page live — no polling.
7. Once `status: completed`, the client can chat with the video via
   `POST /api/videos/:id/ask`, which Express proxies to FastAPI's `/ask`
   endpoint (loads that job's persisted Chroma collection).

## Project structure

```
mern-app/
├── ai-service/     # FastAPI wrapper around the original core/ + utils/ pipeline
├── server/         # Express + MongoDB + Socket.io API gateway
├── client/         # React (Vite) frontend
├── docker-compose.yml
└── render.yaml
```

## Local development

### Prerequisites
- Node.js 20+
- Python 3.11+
- FFmpeg installed and on your PATH
- MongoDB running locally, or a MongoDB Atlas connection string
- API keys: `MISTRAL_API_KEY` (required), `SARVAM_API_KEY` (optional, only
  needed for the Hinglish→English transcription path)

### 1. AI service
```bash
cd ai-service
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill in MISTRAL_API_KEY, AI_SERVICE_SECRET, etc.
uvicorn main:app --reload --port 8000
```

# to run
uv venv
source .venv/Scripts/activate
uv pip install -r requirements.txt


### 2. Express server
```bash
cd server
npm install
cp .env.example .env   # fill in MONGO_URI, JWT_SECRET, INTERNAL_AI_SECRET (must match ai-service), etc.
npm run dev
```

### 3. React client
```bash
cd client
npm install
cp .env.example .env   # VITE_API_URL=http://localhost:5000
npm run dev
```

Visit `http://localhost:5173`, register an account, and submit a video.

### Or: everything at once with Docker Compose
```bash
cp ai-service/.env.example ai-service/.env   # fill in secrets first
cp server/.env.example server/.env
docker compose up --build
```

## Deploying (Render)

This repo includes `render.yaml` as a Blueprint for three services. Render
Blueprints don't auto-resolve cross-service URLs cleanly for docker + static
services mixed together, so the flow is:

1. Push this repo to GitHub.
2. In Render, choose **New → Blueprint** and point it at the repo — it will
   read `render.yaml` and create three services:
   - `ai-video-assistant-ai-service` (Docker, Python/FastAPI)
   - `ai-video-assistant-server` (Docker, Node/Express)
   - `ai-video-assistant-client` (Static site, React build)
3. Set the `sync: false` env vars for each service in the Render dashboard:
   - **ai-service**: `MISTRAL_API_KEY`, `SARVAM_API_KEY` (optional),
     `AI_SERVICE_SECRET` (make up a long random string)
   - **server**: `MONGO_URI` (from MongoDB Atlas), `JWT_SECRET` (long random
     string), `INTERNAL_AI_SECRET` (**same value** as `AI_SERVICE_SECRET`
     above), then after the first deploy, `SELF_URL` and `AI_SERVICE_URL`
     using the `.onrender.com` URLs Render assigns, and `CLIENT_ORIGIN` with
     the client's URL
   - **client**: `VITE_API_URL` set to the server's `.onrender.com` URL
4. Redeploy `server` and `client` after filling in the URL-dependent env
   vars (Render doesn't hot-reload env var changes into a running static
   build, so trigger a manual redeploy for the client).

**MongoDB:** Render doesn't offer managed MongoDB — use
[MongoDB Atlas](https://www.mongodb.com/atlas) free tier and paste the
connection string into `MONGO_URI`.

**Resource note:** local Whisper transcription is CPU/RAM-hungry. Render's
free tier will be too slow or get OOM-killed on anything but very short
clips — use at least a `Standard` instance for `ai-service`, or swap
`WHISPER_MODEL=small` for `tiny` for lighter (lower-accuracy) transcription,
or point `transcriber.py` at a hosted Whisper API instead of running it
locally.

## What changed vs. the original CLI project

- `core/vector_store.py` and `core/rag_engine.py`: the Chroma vector store
  is now scoped **per job** (`storage/vector_db/<job_id>`) instead of one
  shared global collection, so concurrent users' videos don't overwrite each
  other's embeddings.
- `utils/audio_processor.py`: download directory is now configurable via
  `DOWNLOAD_DIR` instead of hardcoded.
- Everything else in `core/` and `utils/` is untouched — same Whisper/Sarvam
  transcription, same Mistral-powered summarization and extraction, same RAG
  chain logic, just called from `pipeline.py` instead of `main.py`'s CLI
  loop.
- The original `main.py` CLI still works standalone if you ever want to run
  the pipeline without the web app — nothing about `core/`'s public
  functions was removed, only extended with job-scoping parameters.

## Possible next steps

- Rate limiting / usage quotas per user
- Password reset flow, email verification
- Streamed/token-by-token chat answers over Socket.io instead of
  request/response
- Swap local Whisper for a hosted STT API in production for speed and lower
  memory footprint
- Add pagination to the job list once history grows







It's a **Python FastAPI** service. Here's how to run it:

---

## Steps to Run the AI Service

### 1. Activate the virtual environment
A `.venv` already exists, so activate it:
```powershell
cd ai-service
.venv\Scripts\activate
```

### 2. Set up your `.env`
Copy the example and fill in your keys:
```powershell
copy .env.example .env
```
Then edit [`.env`](file:///c:/Users/ASUS/OneDrive/Pictures/Desktop/GenAi/AIVideoAssistant-MERN/AiVideoAssistant/ai-service/.env) and set at minimum:
- `MISTRAL_API_KEY` — required for AI processing
- `AI_SERVICE_SECRET` — must match `INTERNAL_AI_SECRET` in your backend `server/.env`

### 3. Install dependencies (if not already done)
```powershell
pip install -r requirements.txt
```

### 4. Run the FastAPI server
```powershell
python -m uvicorn main:app --reload --port 8000
```

Or if the `PORT` env var is set:
```powershell
uvicorn main:app --reload --port $env:PORT
```

---

### Verify it's running
Visit **http://localhost:8000/health** — you should get:
```json
{"status": "ok"}
```

---

> **Note:** The service exposes three routes:
> - `GET /health` — health check
> - `POST /process` — accepts video/YouTube URL, kicks off background pipeline
> - `POST /ask` — RAG-based Q&A over a processed job
