import os
import re
import shutil
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.rag_engine import ask_question, load_rag_chain
from pipeline import run_pipeline_job
from utils.video_source import validate_video_source

load_dotenv()

app = FastAPI(title="AI Video Assistant - AI Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # this service is internal-only, sits behind the Express server
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "storage/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Shared secret Express must present when it calls us, and that we present
# back to Express's internal callback route. Keeps the internal link private.
SERVICE_SECRET = os.getenv("AI_SERVICE_SECRET", "")


def _check_secret(secret: str):
    if SERVICE_SECRET and secret != SERVICE_SECRET:
        raise HTTPException(status_code=401, detail="invalid service secret")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/process")
async def process_video(
    background_tasks: BackgroundTasks,
    request: Request,
    job_id: str = Form(...),
    language: str = Form("english"),
    callback_url: str = Form(...),
    callback_secret: str = Form(...),
    service_secret: str = Form(""),
    youtube_url: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    _check_secret(service_secret)

    form = await request.form()
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
        callback_url=callback_url,
        callback_secret=callback_secret,
    )

    return {"status": "accepted", "job_id": job_id}


class AskRequest(BaseModel):
    job_id: str
    question: str
    service_secret: str = ""


@app.post("/ask")
def ask(payload: AskRequest):
    _check_secret(payload.service_secret)
    try:
        rag_chain = load_rag_chain(payload.job_id)
        answer = ask_question(rag_chain, payload.question)
        return {"answer": answer}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/")
def root():
    return {"service": "ai-video-assistant-ai-service", "id": str(uuid.uuid4())[:8]}
