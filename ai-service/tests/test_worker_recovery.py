"""Failure recovery checks for the MongoDB queue without providers or MongoDB."""
import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch


BASE = Path(__file__).resolve().parents[1]


class Result:
    def __init__(self, modified_count=1, matched_count=1):
        self.modified_count = modified_count
        self.matched_count = matched_count


class FakeCollection:
    def __init__(self, jobs):
        self.jobs = jobs
        self.updates = []

    def find(self, *_args, **_kwargs):
        return list(self.jobs)

    def update_one(self, query, update):
        self.updates.append((query, update))
        return Result()

    def find_one_and_update(self, query, update, **_kwargs):
        self.updates.append((query, update))
        job = self.jobs[0]
        job["status"] = "processing"
        job.setdefault("execution", {}).update({
            "attemptId": "attempt-1",
            "attempts": job.get("execution", {}).get("attempts", 0) + 1,
        })
        return job


class WorkerRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pipeline = types.ModuleType("pipeline")
        pipeline.run_pipeline_job = Mock(return_value=True)
        with patch.dict(sys.modules, {"pipeline": pipeline}), patch.dict(os.environ, {
            "AI_SERVICE_SECRET": "test-only-internal-secret-0123456789abcdef",
            "EXPRESS_INTERNAL_URL": "http://localhost:5000",
            "MISTRAL_API_KEY": "test-mistral-key",
            "SARVAM_API_KEY": "",
            "MONGO_URI": "mongodb://localhost:27017/ai-video-assistant",
        }):
            spec = importlib.util.spec_from_file_location("durable_worker_test", BASE / "worker.py")
            cls.worker = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.worker)
        cls.pipeline = pipeline

    def test_expired_lease_is_requeued_with_bounded_backoff(self):
        now = datetime.now(timezone.utc)
        jobs = [{
            "_id": "job-1",
            "status": "processing",
            "execution": {"attempts": 1, "maxAttempts": 3, "leaseUntil": now - timedelta(seconds=1), "checkpoint": "transcribing"},
        }]
        collection = FakeCollection(jobs)
        self.assertEqual(self.worker.recover_expired(collection, now), 1)
        update = collection.updates[0][1]
        self.assertEqual(update["$set"]["status"], "queued")
        self.assertEqual(update["$set"]["execution.checkpoint"], "transcribing")
        self.assertEqual(update["$set"]["execution.nextAttemptAt"], now + timedelta(seconds=30))

    def test_expired_lease_becomes_explicit_failure_after_attempt_limit(self):
        now = datetime.now(timezone.utc)
        jobs = [{
            "_id": "job-1",
            "status": "processing",
            "execution": {"attempts": 3, "maxAttempts": 3, "leaseUntil": now - timedelta(seconds=1)},
        }]
        collection = FakeCollection(jobs)
        self.worker.recover_expired(collection, now)
        self.assertEqual(collection.updates[0][1]["$set"]["status"], "failed")
        self.assertEqual(collection.updates[0][1]["$set"]["execution.lastError"], "worker_lease_expired")

    def test_pipeline_failure_is_not_left_processing(self):
        now = datetime.now(timezone.utc)
        job = {
            "_id": "job-1",
            "status": "processing",
            "sourceType": "youtube",
            "sourceValue": "https://www.youtube.com/watch?v=BaW_jenozKc",
            "language": "english",
            "execution": {"attempts": 1, "maxAttempts": 2, "attemptId": "attempt-1", "checkpoint": "transcribing"},
        }
        collection = FakeCollection([job])
        self.worker.STOP.clear()
        with patch.object(self.worker, "run_pipeline_job", return_value=False):
            self.worker.process_one(Mock(), collection, job)
        self.assertTrue(collection.updates)
        terminal_update = collection.updates[-1][1]
        self.assertEqual(terminal_update["$set"]["status"], "queued")
        self.assertIn("execution.nextAttemptAt", terminal_update["$set"])

