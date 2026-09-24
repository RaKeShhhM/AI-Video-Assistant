import os
import threading
from pathlib import Path
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from starlette.datastructures import UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from utils.internal_security import load_internal_settings, secret_matches
from utils.job_ids import validate_job_id

load_dotenv()
# Validate before importing models or creating storage directories.
INTERNAL_SETTINGS = load_internal_settings()

from core.rag_engine import ask_question, load_rag_chain
from pipeline import run_pipeline_job
from utils.video_source import validate_video_source
from utils.media_limits import MAX_SECONDS, MAX_AI_ASKS, EXTENSIONS, MediaRejected, copy_upload, inspect_media
from utils.request_limits import ProcessLimitsMiddleware

app = FastAPI(title="AI Video Assistant - AI Service")
app.add_middleware(ProcessLimitsMiddleware)
ask_slots = threading.BoundedSemaphore(MAX_AI_ASKS)


@app.middleware("http")
async def authenticate_internal_requests(request: Request, call_next):
    if request.url.path.rstrip("/") in {"/process", "/ask"}:
        secrets = request.headers.getlist("x-internal-secret")
        if len(secrets) != 1 or not secret_matches(secrets[0], INTERNAL_SETTINGS.secret):
            return JSONResponse(status_code=401, content={"detail": "Invalid internal secret"})
    return await call_next(request)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "storage/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/process")
async def process_video(
    background_tasks: BackgroundTasks,
    request: Request,
):
    async with request.form(max_files=1, max_fields=4, max_part_size=4096) as form:
        return await accept_process(background_tasks, form)


async def accept_process(background_tasks, form):
    # The form is parsed explicitly so field/file counts are constrained before
    # Starlette spools any files, rather than after FastAPI's default parser.
    job_id = form.get("job_id")
    language = form.get("language", "english")
    file = form.get("file")
    if file is not None and not isinstance(file, UploadFile):
        raise HTTPException(status_code=400, detail="file must be a multipart file upload.")
    try:
        max_media_seconds = int(form.get("max_media_seconds", MAX_SECONDS))
        if not 1 <= max_media_seconds <= MAX_SECONDS:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid media duration limit.") from None
    if language not in {"english", "hinglish"}:
        raise HTTPException(status_code=400, detail="Unsupported language.")
    if any(field in form for field in ("callback_url", "callback_secret", "service_secret")):
        raise HTTPException(status_code=400, detail="Credentials belong in X-Internal-Secret; callbacks are configured on the server.")
    try:
        validate_job_id(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if len(form.getlist("youtube_url")) > 1 or len(form.getlist("file")) > 1:
        raise HTTPException(status_code=400, detail="Submit only one URL or one file.")
    # Form binding treats empty strings as missing; keep explicit empty fields
    # so URL+file remains invalid even when the URL is blank.
    youtube_url = form.get("youtube_url") if "youtube_url" in form else None
    try:
        source_type, source = validate_video_source(youtube_url, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if source_type == "upload":
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in EXTENSIONS:
            raise HTTPException(status_code=415, detail="Unsupported media extension.")
        # Paths are generated here, never supplied through a URL or job ID.
        dest_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{ext}")
        try:
            await run_in_threadpool(copy_upload, file.file, dest_path)
            await run_in_threadpool(inspect_media, dest_path, max_media_seconds)
        except BaseException as exc:
            Path(dest_path).unlink(missing_ok=True)
            if isinstance(exc, MediaRejected):
                raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
            raise
        finally:
            await file.close()
        source = dest_path

    background_tasks.add_task(
        run_pipeline_job,
        job_id=job_id,
        source=source,
        source_type=source_type,
        language=language,
        max_media_seconds=max_media_seconds,
    )

    return {"status": "accepted", "job_id": job_id}


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    question: str = Field(min_length=1, max_length=2000)


@app.post("/ask")
def ask(payload: AskRequest):
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="question is required")
    if not ask_slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="AI chat capacity is full. Try again later.", headers={"Retry-After": "60"})
    try:
        rag_chain = load_rag_chain(payload.job_id)
        answer = ask_question(rag_chain, payload.question)
        return {"answer": answer}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        ask_slots.release()


@app.get("/")
def root():
    return {"service": "ai-video-assistant-ai-service", "id": str(uuid.uuid4())[:8]}
