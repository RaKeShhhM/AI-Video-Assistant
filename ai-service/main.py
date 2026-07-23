import os
import shutil
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.rag_engine import ask_question, load_rag_chain
from pipeline import run_pipeline_job

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
    job_id: str = Form(...),
    language: str = Form("english"),
    callback_url: str = Form(...),
    callback_secret: str = Form(...),
    service_secret: str = Form(""),
    youtube_url: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    _check_secret(service_secret)

    if not youtube_url and not file:
        raise HTTPException(status_code=400, detail="Provide either youtube_url or file")

    if youtube_url:
        source = youtube_url
    else:
        ext = os.path.splitext(file.filename or "")[1] or ".mp4"
        dest_path = os.path.join(UPLOAD_DIR, f"{job_id}{ext}")
        with open(dest_path, "wb") as out:
            shutil.copyfileobj(file.file, out)
        source = dest_path

    background_tasks.add_task(
        run_pipeline_job,
        job_id=job_id,
        source=source,
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
