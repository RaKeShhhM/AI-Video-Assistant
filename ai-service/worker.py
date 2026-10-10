"""Durable MongoDB-backed worker for processing jobs.

The worker is deliberately separate from FastAPI's request process. MongoDB is
the queue, and leases make a crashed worker recoverable without pretending
provider calls are exactly-once.
"""
import logging
import math
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
from pymongo.errors import PyMongoError
from bson import ObjectId

from pipeline import run_pipeline_job
from utils.callback_delivery import event_update, delivery_loop, retry_blocked

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
                {"_id": job["_id"], "status": "processing", "execution.attemptId": execution.get("attemptId"), "execution.leaseUntil": {"$lt": now}},
                event_update({
                    "status": "failed",
                    "stage": "error",
                    "error": "Processing stopped after the worker lease expired too many times.",
                    "execution.lastError": "worker_lease_expired",
                    "execution.checkpoint": "failed",
                }, now=now),
            )
        else:
            result = collection.update_one(
                {"_id": job["_id"], "status": "processing", "execution.attemptId": execution.get("attemptId"), "execution.leaseUntil": {"$lt": now}},
                event_update({
                    "status": "queued",
                    "stage": "queued",
                    "execution.nextAttemptAt": now + retry_delay(attempts),
                    "execution.lastError": "worker_lease_expired",
                    "execution.checkpoint": execution.get("checkpoint", "queued"),
                }, now=now, unset={"execution.workerId": "", "execution.leaseUntil": ""}),
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
            "execution.ready": {"$ne": False},
            "$expr": {"$lt": [{"$ifNull": ["$execution.attempts", 0]}, {"$ifNull": ["$execution.maxAttempts", MAX_ATTEMPTS]}]},
        },
        {**event_update({
            "status": "processing",
            "stage": "downloading_audio",
            "percent": 0,
            "error": "",
            "execution.workerId": WORKER_ID,
            "execution.attemptId": attempt_id,
            "execution.leaseUntil": now + timedelta(seconds=LEASE_SECONDS),
            "execution.checkpoint": "processing",
        }, now=now), "$inc": {"execution.attempts": 1, "progressVersion": 1}},
        sort=[("createdAt", 1), ("_id", 1)],
        return_document=ReturnDocument.AFTER,
    )


def _heartbeat(collection, job_id, attempt_id, done):
    while not done.wait(HEARTBEAT_SECONDS):
        try:
            result = collection.update_one(
                _owned(job_id, attempt_id),
                {"$set": {"execution.leaseUntil": utcnow() + timedelta(seconds=LEASE_SECONDS)}},
            )
            if result.matched_count != 1:
                return
        except PyMongoError:
            LOG.warning("heartbeat unavailable for job %s", job_id)
            # Progress/provider boundaries still fail closed against the lease.


def _owned(job_id, attempt_id):
    return {"_id": job_id, "status": "processing", "execution.attemptId": attempt_id,
            "execution.leaseUntil": {"$gt": utcnow()}}


def _assert_active(collection, job_id, attempt_id):
    if not collection.find_one(_owned(job_id, attempt_id), {"_id": 1}):
        raise RuntimeError("worker_lease_lost")


def _download_gridfs(db, job, destination):
    artifact = job.get("sourceArtifact") or {}
    file_id = artifact.get("fileId")
    if not file_id:
        raise RuntimeError("job upload artifact is missing")
    bucket = GridFSBucket(db, bucket_name=artifact.get("bucket", "jobMedia"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        bucket.download_to_stream(ObjectId(file_id), output)


def _progress_updater(collection, job_id, attempt_id):
    def update(payload):
        if payload["status"] == "failed":
            return  # The worker decides retry versus terminal failure.
        stages = {"downloading_audio": 5, "transcribing": 20, "transcript_ready": 45,
                  "generating_title": 55, "summarizing": 65, "summary_ready": 72,
                  "extracting_insights": 80, "insights_ready": 90, "indexing_transcript": 95, "completed": 100}
        expected_status = "completed" if payload["stage"] == "completed" else "processing"
        if (payload["status"] != expected_status or payload["stage"] not in stages
                or payload["percent"] != stages[payload["stage"]]):
            raise ValueError("Invalid pipeline transition")
        data = payload.get("data") or {}
        text_limits = {"title": 300, "transcript": 1000000, "summary": 100000,
                       "action_items": 100000, "key_decisions": 100000, "open_questions": 100000}
        for key, limit in text_limits.items():
            if key in data and (not isinstance(data[key], str) or len(data[key]) > limit):
                raise ValueError("Invalid pipeline result field")
        duration = data.get("duration_seconds")
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                    or not math.isfinite(duration) or not 0 < duration <= SETTINGS.max_media_seconds):
            raise ValueError("Invalid pipeline duration")
        fields = {
            "status": payload["status"],
            "stage": payload["stage"],
            "percent": payload["percent"],
            "execution.checkpoint": payload["stage"],
        }
        if payload["status"] == "completed":
            fields["indexAttempt"] = attempt_id
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
        result = collection.update_one(
            {**_owned(job_id, attempt_id), "percent": {"$lte": payload["percent"]}},
            event_update(fields),
        )
        if result.matched_count != 1:
            raise RuntimeError("worker_lease_lost")
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
            index_attempt=attempt_id,
            ensure_active=lambda: _assert_active(collection, job_id, attempt_id),
        )
        if not succeeded:
            raise RuntimeError("pipeline_failed")
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
            _owned(job_id, attempt_id),
            event_update(fields, unset={"execution.workerId": "", "execution.leaseUntil": ""}),
        )
        LOG.exception("job failed id=%s attempt=%s terminal=%s", job_id, attempt_id, terminal)
    finally:
        done.set()
        heartbeat.join(timeout=2)
        if temporary:
            temporary.unlink(missing_ok=True)


def run():
    client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=10000)
    db = client.get_default_database()
    jobs = db.jobs
    jobs.create_index([("status", 1), ("execution.nextAttemptAt", 1), ("createdAt", 1)])
    jobs.create_index([("status", 1), ("execution.leaseUntil", 1)])
    jobs.create_index([("delivery.state", 1), ("delivery.nextAt", 1)])
    jobs.create_index([("delivery.state", 1), ("delivery.leaseUntil", 1)])
    import sys
    if "--retry-callbacks" in sys.argv:
        print(f"Requeued {retry_blocked(jobs)} blocked callback notifications")
        client.close()
        return
    relay = threading.Thread(target=delivery_loop, args=(jobs, STOP), daemon=True)
    relay.start()
    LOG.info("worker started id=%s", WORKER_ID)
    while not STOP.is_set():
        try:
            now = utcnow()
            recover_expired(jobs, now)
            job = claim(jobs, now)
            if job:
                process_one(db, jobs, job)
            else:
                STOP.wait(POLL_SECONDS)
        except PyMongoError:
            LOG.exception("queue unavailable; retrying after poll delay")
            STOP.wait(POLL_SECONDS)
    relay.join(timeout=20)
    client.close()


def stop(*_):
    STOP.set()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run()
