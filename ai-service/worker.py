"""Durable MongoDB-backed worker for processing jobs.

The worker is deliberately separate from FastAPI's request process. MongoDB is
the queue, and leases make a crashed worker recoverable without pretending
provider calls are exactly-once.
"""
import logging
import os
import signal
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from settings import load_settings

SETTINGS = load_settings()

from gridfs import GridFSBucket
from pymongo import MongoClient, ReturnDocument
from bson import ObjectId

from pipeline import run_pipeline_job
from utils.internal_security import post_progress

LOG = logging.getLogger("ai-worker")
STOP = threading.Event()
LEASE_SECONDS = int(os.getenv("JOB_LEASE_SECONDS", "120"))
HEARTBEAT_SECONDS = int(os.getenv("JOB_HEARTBEAT_SECONDS", "15"))
MAX_ATTEMPTS = int(os.getenv("JOB_MAX_ATTEMPTS", "3"))
POLL_SECONDS = float(os.getenv("JOB_POLL_SECONDS", "2"))
WORKER_ID = os.getenv("WORKER_ID", f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}")


def utcnow():
    return datetime.now(timezone.utc)


def retry_delay(attempt):
    return timedelta(seconds=(30, 120, 600)[min(max(attempt - 1, 0), 2)])


def recover_expired(collection, now=None):
    now = now or utcnow()
    changed = 0
    for job in collection.find({
        "status": "processing",
        "execution.leaseUntil": {"$lt": now},
    }, {"_id": 1, "execution": 1}):
        execution = job.get("execution", {})
        attempts = int(execution.get("attempts", 0))
        if attempts >= int(execution.get("maxAttempts", MAX_ATTEMPTS)):
            result = collection.update_one(
                {"_id": job["_id"], "status": "processing", "execution.leaseUntil": {"$lt": now}},
                {"$set": {
                    "status": "failed",
                    "stage": "error",
                    "error": "Processing stopped after the worker lease expired too many times.",
                    "execution.lastError": "worker_lease_expired",
                    "execution.checkpoint": "failed",
                }},
            )
        else:
            result = collection.update_one(
                {"_id": job["_id"], "status": "processing", "execution.leaseUntil": {"$lt": now}},
                {"$set": {
                    "status": "queued",
                    "stage": "queued",
                    "execution.nextAttemptAt": now + retry_delay(attempts),
                    "execution.lastError": "worker_lease_expired",
                    "execution.checkpoint": execution.get("checkpoint", "queued"),
                }, "$unset": {"execution.workerId": "", "execution.attemptId": "", "execution.leaseUntil": ""}},
            )
        changed += result.modified_count
    return changed


def claim(collection, now=None):
    now = now or utcnow()
    attempt_id = uuid.uuid4().hex
    return collection.find_one_and_update(
        {
            "status": "queued",
            "execution.nextAttemptAt": {"$lte": now},
            "$or": [{"execution.attempts": {"$lt": MAX_ATTEMPTS}}, {"execution.attempts": {"$exists": False}}],
        },
        {"$set": {
            "status": "processing",
            "stage": "processing",
            "execution.workerId": WORKER_ID,
            "execution.attemptId": attempt_id,
            "execution.leaseUntil": now + timedelta(seconds=LEASE_SECONDS),
            "execution.checkpoint": "processing",
        }, "$inc": {"execution.attempts": 1}},
        sort=[("createdAt", 1), ("_id", 1)],
        return_document=ReturnDocument.AFTER,
    )


def _heartbeat(collection, job_id, attempt_id, done):
    while not done.wait(HEARTBEAT_SECONDS):
        result = collection.update_one(
            {"_id": job_id, "status": "processing", "execution.attemptId": attempt_id},
            {"$set": {"execution.leaseUntil": utcnow() + timedelta(seconds=LEASE_SECONDS)}},
        )
        if result.matched_count != 1:
            return


def _download_gridfs(db, job, destination):
    artifact = job.get("sourceArtifact") or {}
    file_id = artifact.get("fileId")
    if not file_id:
        raise RuntimeError("job upload artifact is missing")
    bucket = GridFSBucket(db, bucket_name=artifact.get("bucket", "jobMedia"))
    with destination.open("wb") as output:
        bucket.download_to_stream(ObjectId(file_id), output)


def _progress_updater(collection, job_id, attempt_id):
    def update(payload):
        data = payload.get("data") or {}
        fields = {
            "stage": payload["stage"],
            "percent": payload["percent"],
            "execution.checkpoint": payload["stage"],
        }
        mapping = {
            "title": "title",
            "transcript": "transcript",
            "summary": "summary",
            "action_items": "actionItems",
            "key_decisions": "keyDecisions",
            "open_questions": "openQuestions",
            "duration_seconds": "durationSeconds",
        }
        for source, target in mapping.items():
            if source in data:
                fields[target] = data[source]
        collection.update_one(
            {"_id": job_id, "status": "processing", "execution.attemptId": attempt_id},
            {"$set": fields},
        )
    return update


def process_one(db, collection, job):
    job_id = job["_id"]
    attempt_id = job["execution"]["attemptId"]
    done = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(collection, job_id, attempt_id, done), daemon=True)
    heartbeat.start()
    source = job.get("sourceValue")
    source_type = job["sourceType"]
    temporary = None
    try:
        if source_type == "upload":
            extension = Path((job.get("sourceArtifact") or {}).get("filename", "input.bin")).suffix.lower()
            temporary = SETTINGS.upload_dir / f"{job_id}-{attempt_id}{extension}"
            _download_gridfs(db, job, temporary)
            source = str(temporary)
        succeeded = run_pipeline_job(
            str(job_id),
            source,
            source_type,
            job.get("language", "english"),
            SETTINGS.max_media_seconds,
            progress_hook=_progress_updater(collection, job_id, attempt_id),
            send_failure_callback=False,
        )
        if not succeeded:
            raise RuntimeError("pipeline_failed")
        collection.update_one(
            {"_id": job_id, "status": "processing", "execution.attemptId": attempt_id},
            {"$set": {"status": "completed", "stage": "completed", "percent": 100,
                      "execution.checkpoint": "completed", "execution.leaseUntil": None}},
        )
    except Exception as exc:
        attempts = int(job["execution"].get("attempts", 1))
        terminal = attempts >= int(job["execution"].get("maxAttempts", MAX_ATTEMPTS))
        fields = {
            "status": "failed" if terminal else "queued",
            "stage": "error" if terminal else "queued",
            "error": "AI processing failed after bounded retries." if terminal else "AI processing will retry.",
            "execution.lastError": type(exc).__name__,
            "execution.checkpoint": "failed" if terminal else job["execution"].get("checkpoint", "queued"),
        }
        if not terminal:
            fields["execution.nextAttemptAt"] = utcnow() + retry_delay(attempts)
        collection.update_one(
            {"_id": job_id, "status": "processing", "execution.attemptId": attempt_id},
            {"$set": fields, "$unset": {"execution.workerId": "", "execution.attemptId": "", "execution.leaseUntil": ""}},
        )
        if terminal:
            try:
                post_progress({
                    "job_id": str(job_id),
                    "status": "failed",
                    "stage": "error",
                    "percent": 0,
                    "data": {},
                })
            except Exception:
                LOG.warning("terminal failure callback unavailable id=%s", job_id)
        LOG.exception("job failed id=%s attempt=%s terminal=%s", job_id, attempt_id, terminal)
    finally:
        done.set()
        if temporary:
            temporary.unlink(missing_ok=True)


def run():
    client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=10000)
    db = client.get_default_database()
    jobs = db.jobs
    jobs.create_index([("status", 1), ("execution.nextAttemptAt", 1), ("createdAt", 1)])
    jobs.create_index([("status", 1), ("execution.leaseUntil", 1)])
    LOG.info("worker started id=%s", WORKER_ID)
    while not STOP.is_set():
        now = utcnow()
        recover_expired(jobs, now)
        job = claim(jobs, now)
        if job:
            process_one(db, jobs, job)
        else:
            STOP.wait(POLL_SECONDS)
    client.close()


def stop(*_):
    STOP.set()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run()
