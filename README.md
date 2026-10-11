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
2. Express stores an uploaded file in MongoDB GridFS (YouTube URLs remain in
   MongoDB), creates a `Job` document with `status: queued`, and responds
   immediately with the job id. A temporary request path is never queued.
3. A separate Python worker polls MongoDB, atomically claims the job with a
   lease, and runs the pipeline. FastAPI no longer owns processing through
   in-process `BackgroundTasks`.
4. After each pipeline stage (download → transcribe → summarize → extract
   insights → build RAG index), the worker records a checkpoint in MongoDB and
   persists results plus pending notifications in MongoDB and relays versioned callbacks to Express
   (`/api/internal/jobs/progress`, protected by a shared secret).
5. Express updates the `Job` document and emits a `job:update` Socket.io
   event to that user's private room.
6. The client updates compact, versioned dashboard/detail state through Socket.io
   and refetches the authoritative result document when a job completes.
7. History uses cursor pagination (`GET /api/videos?limit=20&cursor=...`) and
   card projections; large transcript/result fields are fetched only for the
   selected job.
8. Once `status: completed`, chat messages are stored in the separate
   `ChatMessage` collection. `GET /api/videos/:id/chat` returns bounded pages,
   while `POST /api/videos/:id/ask` returns only the new message rather than
   the full conversation.
   Existing embedded chat entries are migrated lazily into `ChatMessage` on
   the first chat read or write; the migration is idempotent and does not
   expose another user's messages.

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
cp .env.example .env   # fill in MISTRAL_API_KEY, AI_SERVICE_SECRET, EXPRESS_INTERNAL_URL
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
   - `ai-video-assistant-ai-service` (private Docker service, Python/FastAPI)
   - `ai-video-assistant-server` (Docker, Node/Express)
   - `ai-video-assistant-client` (Static site, React build)
3. Set the `sync: false` env vars for each service in the Render dashboard:
   - **ai-service**: `MISTRAL_API_KEY`, `SARVAM_API_KEY` (optional),
     `AI_SERVICE_SECRET` (random 32-256 character secret), and
     `EXPRESS_INTERNAL_URL` (Express's reachable origin, preferably its private hostname and port)
   - **server**: `MONGO_URI` (from MongoDB Atlas), `JWT_SECRET` (long random
     string), `INTERNAL_AI_SECRET` (**same value** as `AI_SERVICE_SECRET`
     above), `AI_SERVICE_URL` (`http://<AI private hostname>:8000`), and
     `CLIENT_ORIGIN` with the client's URL. The AI service has no public
     `.onrender.com` URL. `SELF_URL` is no longer used for callbacks.
   - **client**: `VITE_API_URL` set to the server's `.onrender.com` URL
4. Redeploy the affected services after filling in the URL-dependent env
   vars (Render doesn't hot-reload env var changes into a running static
  build, so trigger a manual redeploy for the client).

Both backends reject missing or placeholder internal secrets at startup. Use
the same randomly generated value in `AI_SERVICE_SECRET` and
`INTERNAL_AI_SECRET`; for example, generate one locally with
`python -c "import secrets; print(secrets.token_urlsafe(48))"` and place it in
the two private environments. Never commit the value. Internal calls use
`X-Internal-Secret`; callback URLs and credentials are not accepted in request
bodies. Restart both backends when changing the shared secret.

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
> - `POST /process` — legacy AI-service intake; validates the request but returns
>   410 because processing is accepted durably by Express and the MongoDB worker queue
> - `POST /ask` — RAG-based Q&A over a processed job

## Persistent vector indexes (S7)

Render's AI private service now declares a 1 GiB disk mounted at `/var/data`
with `VECTOR_DB_DIR=/var/data/vector_db`. Compose uses a named `vector_data`
volume at the same path. Downloads/uploads remain temporary. Local Python runs
keep the `.env` default `storage/vector_db` unless explicitly overridden.

This is a single-instance deployment. Render disks survive redeploys and
restarts, but cannot be shared between service instances and prevent
zero-downtime deployments. Disk provisioning has a cost; the repository change
does not provision it. See [Render persistent disks](https://render.com/docs/disks)
and [Blueprint disk configuration](https://render.com/docs/blueprint-spec).

Existing indexes are **not automatically migrated** when the storage path changes.
Before switching an existing deployment, pause processing and make a consistent
backup of the complete old `VECTOR_DB_DIR` with the AI process stopped, then copy
its job-ID directories (including SQLite and index files) into the new directory.
Do not copy a live SQLite database or remove the original until retrieval from
the new location is verified. Alternatively, resubmit the videos to build new
indexes. For Compose, migrate old `ai-service/storage/vector_db` contents into
the named volume before relying on older completed jobs. `docker compose down`
retains named volumes; `docker compose down -v` deletes them.

If a job directory/database/collection is missing or the collection is empty,
asking returns HTTP 409 with code `INDEX_REBUILD_REQUIRED`, without calling the
answer provider or creating an empty collection. The existing chat error banner
asks the user to submit the video again. This does not implement an in-place
rebuild API. Other storage/provider errors retain the sanitized service error.

Deployment acceptance (must be performed on the actual deployed service):

1. Apply the disk/volume configuration and verify `VECTOR_DB_DIR` points inside
   its mount. Confirm adequate free disk space.
2. Process a permitted short video and ask a question; record the job ID.
3. Restart/redeploy only the AI service, retaining its disk/volume and MongoDB.
4. Ask against that same job and confirm retrieval succeeds.
5. Test the missing-index response using a disposable job on a test deployment.

The automated persistence test writes an index in one Python process, exits,
and retrieves from it in a fresh process using fixed local embeddings. This
verifies on-disk reopening, not a live Render deployment or paid-model quality.
Disk persistence does not replace the durable MongoDB job queue; backups and
multi-instance vector storage remain separate operational concerns. R2 covers
worker restart recovery, and R5 covers retention and deletion cleanup.

## Durable job execution (P1 / R2)

Processing uses MongoDB as the durable queue. Uploaded media is stored in the
`jobMedia` GridFS bucket with job ownership metadata before the queued job is
acknowledged. YouTube URLs are stored directly on the job. The Python worker
streams GridFS media into an attempt-specific temporary workspace, so a queue
record never depends on an Express temporary-file path.

The worker claims queued jobs atomically and records an attempt ID, worker ID,
lease expiry, attempt count and stage checkpoint. It heartbeats every 15
seconds by default; expired leases are requeued with bounded delays of 30
seconds, 2 minutes and 10 minutes, and jobs become explicitly failed after
three attempts. These values are configurable with `JOB_LEASE_SECONDS`,
`JOB_HEARTBEAT_SECONDS`, `JOB_MAX_ATTEMPTS` and `JOB_POLL_SECONDS`. Compose and
the current single-instance Render deployment run the FastAPI process and one
worker process against the same MongoDB and persistent vector disk.

This is at-least-once execution. A crash after a paid provider call but before
its checkpoint can repeat that call and create duplicate provider cost; the
system does not promise exactly-once external calls. R3 adds durable, versioned
snapshot notifications; R4 adds submission deduplication and attempt fencing (see below). MongoDB results
and pending notification state survive an Express restart.

The legacy FastAPI `/process` endpoint still validates requests but returns
`410 Processing is owned by the durable worker queue`; submissions must enter
through Express so ownership, usage reservations and GridFS persistence are
recorded together. R2 tests cover expired leases, bounded retry timing,
terminal failure, and a pipeline failure that cannot remain indefinitely
`processing`. The tests use mocked providers; actual Render restart/recovery
evidence has not yet been collected.

## Configuration startup (P1 / R1)

The AI service calls `settings.load_settings()` before importing pipeline, model,
or storage consumers. It reads `ai-service/.env` explicitly, independent of the
shell's current directory. `AI_ENV_FILE` may select an existing absolute `.env`
path. Deployment environment variables always take precedence; an explicitly
empty required environment variable fails validation rather than falling back.

Startup validates the internal secret/callback origin, required Mistral key,
optional Sarvam key, transcription model settings, processing limits, and storage
paths. Leave `SARVAM_API_KEY` empty when unused; example placeholder keys are
rejected. Sarvam model identifiers are syntax-checked; provider availability is
not tested at startup. Relative storage paths resolve against `ai-service/`.
Upload, download and vector directories must be separate and non-nested so
scratch cleanup cannot overlap durable indexes. Existing absolute deployment
paths remain unchanged. No directories or models are created during validation.

Configuration is a startup snapshot: restart the service after changing it.
Consumer modules still use environment-backed constants, populated and validated
by the startup loader before their imports. Future worker entry points must call
this same loader before importing consumers. Direct library imports in isolated
tests do not trigger production startup validation.

`python-config-tests` in GitHub Actions runs fresh-process configuration tests
without downloading models or calling providers. This is an additional targeted
CI job; it does not replace the full local Python regression suite. R1 does not
introduce a queue, durable workers, callback retries, or restart recovery (R2-R4).

## Recoverable callbacks (P1 / R3)

The worker is authoritative for job state. Each stage/result update atomically
increments `progressVersion` and writes a pending `delivery` record in the same
MongoDB job document. There is no gap between saving results and recording that
Express needs notification. One latest snapshot is retained per job: later
progress replaces older pending progress, retaining the accumulated result
fields. This is a coalescing outbox, not an audit log of every stage event.

A separate relay thread continues delivering while the worker processes media.
The internal callback body now contains only `job_id`, `attempt_id` (32 hex
characters), and `sequence` (the monotonic per-job `progressVersion`). Transcript
and result payloads stay in MongoDB rather than crossing the callback body limit.
Express validates the reference and reads the matching stored snapshot. Old or
out-of-order notifications are acknowledged as ignored; they never rewrite job
status/results. Usage finalization is idempotent and finishes before the gateway
records `callbackAck`. Replaying an acknowledgement lost in transit is safe.

Delivery uses a 60-second claim lease, the existing 15-second HTTP timeout, and
at most eight attempts per snapshot. Network timeouts, connection failures,
408/425/429 and 5xx responses retry with exponential delays capped at 60 seconds
plus jitter. Integer Retry-After values on 429/503 are honored up to 300 seconds.
Other HTTP errors, redirects, malformed JSON acknowledgements, or exhausted
retries leave `delivery.state=blocked` with a safe error category. A valid JSON
acknowledgement with the wrong identity/version is retried within the same budget.
Results remain readable even if notification delivery is blocked. An interrupted
relay claim is eligible for redelivery after lease expiry in a fresh process.

Monitor `jobs` filtered by `delivery.state: "blocked"` and callback relay logs.
After fixing the cause (such as gateway authentication or availability), run
`python worker.py --retry-callbacks` from `ai-service` with the usual environment.
It requeues blocked notifications; it does not repeat transcription, indexing or
provider calls. Pending notifications resume automatically when the worker runs.
The command retries all blocked notifications in this service's database.

Deploy the Express and worker changes together: stop/drain the old worker,
deploy the gateway, then start the updated worker. The old callback body is
intentionally rejected. Existing terminal R2 jobs without an outbox are not
silently migrated; this change guarantees delivery for updates written by R3.
Do not run old/new worker versions concurrently during rollout.

The worker now marks success only after indexing and the terminal snapshot save
succeed. Lease-expiry recovery also persists a pending notification, so exhausted
jobs can finalize usage when the gateway recovers. All callback retries are
independent of AI processing retries.

This is durable at-least-once notification with idempotent reconciliation, not
exactly-once Socket.IO delivery. A gateway crash after recording an acknowledgement
but before emitting can lose the live event; clients can fetch the persisted job.
Reconnect recovery remains F2/R6. R4 below adds submission deduplication,
cancellation and fences for stale worker result/index writes.

Tests use a temporary real MongoDB, fresh relay processes and the real Express
callback route to check crash recovery, lost acknowledgements, bounded retries,
permanent failures, superseded notifications and one-time usage reconciliation.
CI's `python-worker-tests` job supplies MongoDB and installs the gateway to run
these integration checks without paid providers. Local tests find `mongod` on
PATH or accept `CALLBACK_TEST_MONGO_URI`; use only a dedicated test database host.


## Job lifecycle and idempotency (P1 / R4)

`POST /api/videos` and `POST /api/videos/:id/retry` require an
`Idempotency-Key` header (16-128 letters, digits, underscores or hyphens;
a UUID works). Reuse the key and the same input after a timeout. The upload
form retains its key while retrying the same input. A new logical submission
uses a new key. Keys are scoped to the authenticated user; changed input under
the same key returns `409 IDEMPOTENCY_CONFLICT`. Upload fingerprints include
the file bytes, filename and language, not the temporary path.

The server derives a stable job ID from the user/key and inserts a non-runnable
intent before reserving quota or writing GridFS media. Acceptance atomically
attaches the original media and enables worker claims. Replays return the
existing job (`200`, `replayed: true`); new acceptance returns `201`.
Concurrent acceptance returns `409 SUBMISSION_IN_PROGRESS`; retry the same
request. An interrupted intake lease can be reclaimed after ten minutes.
Unfinished intents remain at `queued/accepting` and need the original request
replayed; they never trigger provider work. Quota reservations are idempotent.
An uncertain database response does not refund quota, delete media, or mark
accepted work failed. Unreferenced media is handled by the R2 orphan reconciler.

| Current state | Allowed change |
| --- | --- |
| queued, accepted | processing (atomic claim), cancelled |
| processing | forward progress, completed, queued (bounded automatic retry), failed, cancelled |
| completed | terminal |
| failed or cancelled | terminal; manual retry creates a new job with `retryOf` |

`POST /api/videos/:id/cancel` is owner-scoped and idempotent. It atomically
invalidates the attempt, persists a cancellation notification and reconciles
usage. Completed jobs and unfinished intake cannot be cancelled. Retry creates
a separately budgeted successor; its idempotency key deduplicates repeated
clicks. The old job and its callback identity stay terminal. Uploaded retries
clone the GridFS source, so each successor can be deleted independently.
R5 adds artifact deletion and retention below.

Workers honor each job's attempt limit. Heartbeats, progress, completion and
failure writes require a live matching lease; an expired lease cannot be
resurrected. Progress validates stage/status/percentage and cannot move backwards
within an attempt. Cancellation/lease checks run before subsequent pipeline
stages, transcription chunks, Sarvam pieces and insight calls. A provider call
already in flight may finish, but cannot publish its results after losing
ownership. Automatic crash retries can still repeat a provider call: external
providers do not participate in the MongoDB commit.

Vectors use deterministic chunk IDs, remove obsolete chunks on rebuild, and
live in attempt-specific collections. Completion atomically publishes the
winning `indexAttempt` alongside results. Chat loads that exact generation;
stale attempts cannot overwrite it. Legacy completed jobs still read their
original collection. Old or cancelled index generations are removed by R5 cleanup. This still requires the existing single-instance persistent disk.

Deploy the updated gateway and FastAPI code before starting the updated worker,
and stop old workers during rollout. Restart both API and worker processes.
The frontend now supplies the required submission header and shows Cancel/Retry
controls. Custom API clients must supply the header too.

Regression coverage includes concurrent submissions, changed-input conflicts,
GridFS reuse, lost acceptance acknowledgements, expired intake recovery,
idempotent quota reservations, cancel/retry ownership, stale callbacks, atomic
worker claims, expired lease fencing, forward-only progress, stable index IDs,
and isolation between index attempts. Run `npm test` in `server` with
`S3_MONGOD` set to the local mongod executable, or set `CALLBACK_TEST_MONGO_URI`
to a dedicated test host. CI runs lifecycle integration against its MongoDB
service and persisted-vector checks without provider credentials.


## Deletion and retention (P1 / R5)

New jobs expire after 30 days by default (`JOB_RETENTION_DAYS`, 1-3650).
Existing jobs without an expiry remain until manually deleted. The Python
worker scans for expired jobs hourly. `DELETE /api/videos/:id` is owner-scoped,
idempotent and returns `202` while cleanup runs. It first marks the job
`deleting` and invalidates its worker attempt and submission token. Queue claims,
callbacks and result writes can no longer update the job. The worker waits for
the active attempt to stop or its lease to expire, then removes the whole job
vector directory, owned download/conversion workspace, temporary GridFS copy,
and original GridFS upload. A per-job OS lock orders vector writing and deletion.

Cleanup is retried with backoff after a crash or temporary file/database error.
After eight failed attempts, the job remains `deleting` with
`deletion.state: failed` and a short error type. The dashboard shows it and the
Delete action queues another attempt. Cleanup is safe to repeat if a previous
attempt removed some artifacts. Once complete, content fields are erased and a
minimal `deleted` tombstone remains so the same idempotency key cannot recreate
the job. The list and detail routes hide completed tombstones. Uploaded manual
retries clone their source into a separate GridFS object; older shared uploads
are retained until their last referencing job is deleted. The hourly orphan scan
also removes unreferenced GridFS files left by crashes or concurrent cleanup.

Media workspaces are removed in pipeline `finally` and on partial preparation
failure; deletion also scans the entire job workspace rather than only known
chunks. A worker killed mid-operation leaves temporary scratch until the job
is deleted; stale scratch is reconciled hourly after 24 hours when no live processing lease owns it. The worker and API must
share MongoDB and the worker must access the persistent vector disk. On Windows,
Chroma can hold a SQLite file open: deletion then stays visible and retryable
until the handle is released, such as after a service restart.

R5 integration tests use a disposable MongoDB and temporary scratch roots to
verify ownership, partial cleanup, retry, retention expiry and terminal content
scrubbing. Vector persistence tests verify deletion from a fresh process without
loading an embedding model or making provider calls.
