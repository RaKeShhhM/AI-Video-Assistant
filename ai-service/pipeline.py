"""Run one claimed durable job and report bounded stage progress.

The worker owns leases, checkpoints and retry decisions. This module keeps the
AI pipeline provider-focused and returns whether the attempt completed;
the durable worker hook commits results and notification state atomically.
"""
import traceback
import wave

from utils.internal_security import post_progress
from utils.job_ids import validate_job_id

from utils.audio_processor import process_input, cleanup_upload, MediaWorkspace
from utils.media_limits import MAX_SECONDS
from core.transcriber import transcribe_all
from core.summarizer import summarize, generate_title
from core.extractor import extract_action_items, extract_key_decisions, extract_questions
from core.rag_engine import build_rag_chain


def _post_callback(payload: dict):
    """Best-effort callback to Express. Never let a callback failure crash the job."""
    try:
        post_progress(payload)
        succeeded = True

    except Exception as exc:  # noqa: BLE001
        print(f"[callback] delivery failed ({type(exc).__name__})")



def run_pipeline_job(
    job_id: str,
    source: str,
    source_type: str,
    language: str,
    max_media_seconds: int = MAX_SECONDS,
    progress_hook=None,
    send_failure_callback: bool = True,
):
    validate_job_id(job_id)
    chunk_paths = []
    workspace = None
    succeeded = False

    def progress(stage: str, percent: int, status: str = "processing", data: dict | None = None):
        payload = {
            "job_id": job_id,
            "status": status,
            "stage": stage,
            "percent": percent,
            "data": data or {},
        }
        if progress_hook is not None:
            progress_hook(payload)
        elif status != "failed" or send_failure_callback:
            _post_callback(payload)

    try:
        workspace = MediaWorkspace(job_id)
        progress("downloading_audio", 5)
        chunk_paths = process_input(source, source_type=source_type, workspace=workspace, max_seconds=max_media_seconds)
        duration = 0
        for path in chunk_paths:
            with wave.open(path, "rb") as audio:
                duration += audio.getnframes() / audio.getframerate()

        progress("transcribing", 20, data={"duration_seconds": duration})
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
                "duration_seconds": duration,
                "transcript": transcript,
                "summary": summary,
                "action_items": action_items,
                "key_decisions": key_decisions,
                "open_questions": open_questions,
            },
        )

        succeeded = True

    except Exception as exc:  # noqa: BLE001
        print(f"[pipeline] job {job_id} failed: {exc}")
        traceback.print_exc()
        progress("error", 0, status="failed", data={"error": "AI processing failed. Please try again."})

    finally:
        try:
            if workspace is not None:
                workspace.cleanup()
        except (OSError, ValueError):
            print("[pipeline] temporary media cleanup failed")
        finally:
            try:
                cleanup_upload(source, source_type)
            except OSError:
                print("[pipeline] ingress upload cleanup failed")
    return succeeded
