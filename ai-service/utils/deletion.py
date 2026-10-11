"""Idempotent cleanup of a marked job. MongoDB keeps a minimal tombstone."""
import re
import shutil
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import ObjectId
from gridfs import GridFSBucket
from gridfs.errors import NoFile
from pymongo import ReturnDocument

from utils.job_ids import validate_job_id

MAX_CLEANUP_ATTEMPTS = 8
UPLOAD_NAME = re.compile(r"^[a-f0-9]{24}-[a-f0-9]{32}\.[a-z0-9]{1,6}$")
LOG = logging.getLogger("ai-deletion")


def utcnow():
    return datetime.now(timezone.utc)


def _retry_delay(attempt):
    return timedelta(seconds=min(3600, 15 * 2 ** min(attempt - 1, 8)))


def _owned_path(root, candidate):
    root = Path(root).resolve()
    target = Path(candidate).resolve()
    if target.parent != root or Path(candidate).is_symlink():
        raise ValueError("Invalid cleanup target")
    return target


def cleanup_scratch(job_id, download_dir, upload_dir):
    validate_job_id(job_id)
    downloads = Path(download_dir).resolve()
    uploads = Path(upload_dir).resolve()
    if downloads.exists():
        for path in downloads.iterdir():
            if path.name.startswith(f"work-{job_id}-"):
                target = _owned_path(downloads, path)
                if target.is_dir():
                    shutil.rmtree(target)
    if uploads.exists():
        for path in uploads.iterdir():
            if path.name.startswith(f"{job_id}-") and UPLOAD_NAME.fullmatch(path.name):
                target = _owned_path(uploads, path)
                if target.is_file():
                    target.unlink()


def delete_vector(job_id):
    # Import Chroma only for cleanup work. Queue/relay startup stays lightweight.
    from core.vector_store import delete_vector_store
    delete_vector_store(job_id)


def delete_media(db, jobs, job):
    artifact = job.get("sourceArtifact") or {}
    file_id = artifact.get("fileId")
    if not file_id:
        return
    file_id = ObjectId(file_id)
    # R4 successors may still reference the same original upload. The last
    # referencing job removes it; older deployments can retain shared media.
    if jobs.find_one({"_id": {"$ne": job["_id"]}, "sourceArtifact.fileId": file_id}, {"_id": 1}):
        return
    try:
        GridFSBucket(db, bucket_name="jobMedia").delete(file_id)
    except NoFile:
        pass


def cleanup_one(db, jobs, download_dir, upload_dir, *, now=None,
                vector_delete=delete_vector, media_delete=delete_media):
    now = now or utcnow()
    token = uuid.uuid4().hex
    job = jobs.find_one_and_update({"status": "deleting", "$or": [
        {"deletion.state": "pending", "deletion.nextAt": {"$lte": now}},
        {"deletion.state": "working", "deletion.leaseUntil": {"$lt": now}},
    ]}, {"$set": {"deletion.state": "working", "deletion.token": token,
                 "deletion.leaseUntil": now + timedelta(minutes=5)},
         "$inc": {"deletion.attempts": 1}},
        sort=[("deletion.nextAt", 1), ("_id", 1)], return_document=ReturnDocument.AFTER)
    if not job:
        return False
    owner = {"_id": job["_id"], "status": "deleting", "deletion.token": token}
    wait_until = job["deletion"].get("waitUntil")
    stopped = job["deletion"].get("workerStoppedAt")
    if wait_until and not stopped and wait_until > now:
        jobs.update_one(owner, {"$set": {"deletion.state": "pending", "deletion.nextAt": wait_until},
                                "$unset": {"deletion.token": "", "deletion.leaseUntil": ""}})
        return True
    try:
        # The marker already fences queue claims, callbacks and result writes.
        # vector_delete holds the per-job OS lock against an index builder.
        vector_delete(str(job["_id"]))
        cleanup_scratch(str(job["_id"]), download_dir, upload_dir)
        media_delete(db, jobs, job)
        db["chatmessages"].delete_many({"job": job["_id"]})
        # Keep only an opaque tombstone so replaying a deleted submission key
        # cannot recreate its job. All user content and artifacts are erased.
        jobs.update_one(owner, {"$set": {"status": "deleted", "deletion.state": "done",
                          "deletion.completedAt": utcnow()},
            "$unset": {key: "" for key in ["sourceArtifact", "sourceValue", "sourceType", "language",
                         "title", "transcript", "summary", "actionItems", "keyDecisions", "openQuestions",
                         "chat", "error", "durationSeconds", "retryOf", "indexAttempt", "submission",
                         "execution", "delivery", "callbackAck", "stage", "percent", "deletion.token",
                         "deletion.leaseUntil", "deletion.activeAttemptId", "deletion.workerStoppedAt",
                         "deletion.waitUntil", "deletion.lastError"]}})
    except Exception as exc:
        attempt = int(job["deletion"].get("attempts", 1))
        LOG.warning("cleanup retry job=%s attempt=%s cause=%s", job["_id"], attempt, type(exc).__name__)
        jobs.update_one(owner, {"$set": {"deletion.state": "failed" if attempt >= MAX_CLEANUP_ATTEMPTS else "pending",
                          "deletion.lastError": type(exc).__name__,
                          "deletion.nextAt": now + _retry_delay(attempt)},
                                "$unset": {"deletion.token": "", "deletion.leaseUntil": ""}})
    return True


def expire_jobs(jobs, now=None):
    now = now or utcnow()
    changed = 0
    for job in jobs.find({"status": {"$in": ["queued", "processing", "completed", "failed", "cancelled"]},
                          "retention.expiresAt": {"$lte": now}}, {"_id": 1}):
        token = uuid.uuid4().hex
        result = jobs.update_one({"_id": job["_id"],
            "status": {"$in": ["queued", "processing", "completed", "failed", "cancelled"]},
            "retention.expiresAt": {"$lte": now}}, [
            {"$set": {"deletion.activeAttemptId": "$execution.attemptId",
                      "deletion.workerStoppedAt": {"$cond": [{"$eq": ["$status", "processing"]}, None, now]},
                      "deletion.waitUntil": {"$ifNull": ["$execution.leaseUntil", now]}}},
            {"$set": {"status": "deleting", "stage": "deleting", "execution.ready": False,
                      "execution.attemptId": token, "execution.leaseUntil": None,
                      "submission.token": None, "submission.leaseUntil": None,
                      "deletion.state": "pending", "deletion.requestedAt": now,
                      "deletion.nextAt": now, "deletion.attempts": 0,
                      "progressVersion": {"$add": [{"$ifNull": ["$progressVersion", 0]}, 1]}}},
            {"$unset": ["delivery"]}])
        changed += result.modified_count
    return changed


def reconcile_stale_scratch(jobs, download_dir, upload_dir, *, now=None, older_than_hours=24):
    """Recover generated files left behind when a worker process was killed."""
    now = now or utcnow()
    cutoff = now.timestamp() - older_than_hours * 3600
    removed = 0
    for root, workspace in ((Path(download_dir).resolve(), True), (Path(upload_dir).resolve(), False)):
        if not root.exists():
            continue
        for path in root.iterdir():
            if path.is_symlink() or path.stat().st_mtime >= cutoff:
                continue
            name = path.name
            match = re.fullmatch(r"work-([a-f0-9]{24})-[A-Za-z0-9_-]+", name) if workspace else UPLOAD_NAME.fullmatch(name)
            if not match:
                continue
            job_id = match.group(1) if workspace else name[:24]
            if jobs.find_one({"_id": ObjectId(job_id), "status": "processing",
                              "execution.leaseUntil": {"$gt": now}}, {"_id": 1}):
                continue
            target = _owned_path(root, path)
            if workspace and target.is_dir():
                shutil.rmtree(target)
                removed += 1
            elif not workspace and target.is_file():
                target.unlink()
                removed += 1
    return removed


def reconcile_orphan_media(db, jobs, *, now=None, older_than_hours=1):
    """Collect media left by an interrupted intake or simultaneous old deletions."""
    now = now or utcnow()
    cutoff = now - timedelta(hours=older_than_hours)
    removed = 0
    files = db["jobMedia.files"].find({"metadata.owner": "job",
        "metadata.purpose": "processing-input", "uploadDate": {"$lt": cutoff}}, {"_id": 1})
    bucket = GridFSBucket(db, bucket_name="jobMedia")
    for file in files:
        if jobs.find_one({"sourceArtifact.fileId": file["_id"]}, {"_id": 1}):
            continue
        try:
            bucket.delete(file["_id"])
            removed += 1
        except NoFile:
            pass
    return removed
