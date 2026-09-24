import os
import re
import shutil
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from utils.internal_security import load_internal_settings, secret_matches
from utils.job_ids import validate_job_id

load_dotenv()
# Validate before importing models or creating storage directories.
INTERNAL_SETTINGS = load_internal_settings()

from core.rag_engine import ask_question, load_rag_chain
from pipeline import run_pipeline_job
from utils.video_source import validate_video_source

app = FastAPI(title="AI Video Assistant - AI Service")


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
    job_id: str = Form(...),
    language: str = Form("english"),
    youtube_url: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    form = await request.form()
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
        ext = os.path.splitext(file.filename or "")[1] or ".mp4"
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", ext):
            ext = ".bin"
        # Paths are generated here, never supplied through a URL or job ID.
        dest_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{ext}")
        with open(dest_path, "wb") as out:
            shutil.copyfileobj(file.file, out)
        source = dest_path

    background_tasks.add_task(
        run_pipeline_job,
        job_id=job_id,
        source=source,
        source_type=source_type,
        language=language,
    )

    return {"status": "accepted", "job_id": job_id}


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    question: str


@app.post("/ask")
def ask(payload: AskRequest):
    try:
        rag_chain = load_rag_chain(payload.job_id)
        answer = ask_question(rag_chain, payload.question)
        return {"answer": answer}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/")
def root():
    return {"service": "ai-video-assistant-ai-service", "id": str(uuid.uuid4())[:8]}
