"""
Orchestrates the existing core/ + utils/ pipeline for one job, posting
progress callbacks back to the Node/Express server after each stage so the
UI can show live status via Socket.io.

Nothing about the underlying AI logic changes here — this is purely the
wiring that turns the original CLI script (main.py) into a resumable,
callback-driven background job.
"""
import os
import traceback

import requests

from utils.audio_processor import process_input
from core.transcriber import transcribe_all
from core.summarizer import summarize, generate_title
from core.extractor import extract_action_items, extract_key_decisions, extract_questions
from core.rag_engine import build_rag_chain


def _post_callback(callback_url: str, callback_secret: str, payload: dict):
    """Best-effort callback to Express. Never let a callback failure crash the job."""
    try:
        requests.post(
            callback_url,
            json=payload,
            headers={"X-Internal-Secret": callback_secret},
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[callback] failed to reach {callback_url}: {exc}")



def run_pipeline_job(
    job_id: str,
    source: str,
    source_type: str,
    language: str,
    callback_url: str,
    callback_secret: str,
):
    chunk_paths = []

    def progress(stage: str, percent: int, status: str = "processing", data: dict | None = None):
        _post_callback(
            callback_url,
            callback_secret,
            {
                "job_id": job_id,
                "status": status,
                "stage": stage,
                "percent": percent,
                "data": data or {},
            },
        )

    try:
        progress("downloading_audio", 5)
        chunk_paths = process_input(source, source_type=source_type)

        progress("transcribing", 20)
        transcript = transcribe_all(chunk_paths, language)
        progress("transcript_ready", 45, data={"transcript": transcript})

        progress("generating_title", 55)
        title = generate_title(transcript)

        progress("summarizing", 65)
        summary = summarize(transcript)
        progress("summary_ready", 72, data={"title": title, "summary": summary})

        progress("extracting_insights", 80)
        action_items = extract_action_items(transcript)
        key_decisions = extract_key_decisions(transcript)
        open_questions = extract_questions(transcript)
        progress(
            "insights_ready",
            90,
            data={
                "action_items": action_items,
                "key_decisions": key_decisions,
                "open_questions": open_questions,
            },
        )

        progress("indexing_transcript", 95)
        build_rag_chain(transcript, job_id)  # persists the per-job Chroma collection to disk

        progress(
            "completed",
            100,
            status="completed",
            data={
                "title": title,
                "transcript": transcript,
                "summary": summary,
                "action_items": action_items,
                "key_decisions": key_decisions,
                "open_questions": open_questions,
            },
        )

    except Exception as exc:  # noqa: BLE001
        print(f"[pipeline] job {job_id} failed: {exc}")
        traceback.print_exc()
        progress("error", 0, status="failed", data={"error": str(exc)})

    finally:
        # Clean up temp chunk files (the original audio/video stays only if it
        # was a direct upload we intentionally keep — chunks are always scratch).
        for path in chunk_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
