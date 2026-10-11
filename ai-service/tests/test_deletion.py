"""Deletion recovery and artifact ownership against a disposable MongoDB."""
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from bson import ObjectId
from gridfs import GridFSBucket
from pymongo import MongoClient

from utils.deletion import cleanup_one, cleanup_scratch, expire_jobs, reconcile_stale_scratch, reconcile_orphan_media


class DeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = None
        cls.directory = None
        uri = os.getenv("CALLBACK_TEST_MONGO_URI")
        if not uri:
            binary = shutil.which("mongod")
            if not binary:
                raise unittest.SkipTest("mongod is needed for deletion integration tests")
            cls.directory = tempfile.TemporaryDirectory(prefix="reel-r5-mongo-")
            with socket.socket() as port:
                port.bind(("127.0.0.1", 0))
                number = port.getsockname()[1]
            cls.process = subprocess.Popen([binary, "--dbpath", cls.directory.name, "--port", str(number),
                "--bind_ip", "127.0.0.1", "--quiet"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            uri = f"mongodb://127.0.0.1:{number}"
        cls.client = MongoClient(uri, serverSelectionTimeoutMS=300, tz_aware=True)
        for _ in range(50):
            try:
                cls.client.admin.command("ping")
                break
            except Exception:
                time.sleep(0.1)
        else:
            cls.tearDownClass()
            raise RuntimeError("Test MongoDB unavailable")
        cls.database_name = "r5_test_" + uuid.uuid4().hex
        cls.db = cls.client[cls.database_name]
        cls.jobs = cls.db.jobs

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "database_name"):
            cls.client.drop_database(cls.database_name)
        cls.client.close()
        if cls.process:
            cls.process.terminate()
            cls.process.wait(timeout=15)
            cls.directory.cleanup()

    def setUp(self):
        self.jobs.delete_many({})
        self.temp = tempfile.TemporaryDirectory(prefix="reel-r5-scratch-")
        self.addCleanup(self.temp.cleanup)
        self.downloads = Path(self.temp.name) / "downloads"
        self.uploads = Path(self.temp.name) / "uploads"
        self.downloads.mkdir()
        self.uploads.mkdir()
        self.id = ObjectId()
        self.now = datetime.now(timezone.utc)
        self.jobs.insert_one({"_id": self.id, "user": ObjectId(), "status": "deleting",
            "sourceType": "upload", "sourceValue": "private.wav", "transcript": "private words",
            "chat": [{"question": "private", "answer": "answer"}],
            "deletion": {"state": "pending", "nextAt": self.now, "attempts": 0,
                         "workerStoppedAt": self.now, "waitUntil": self.now}})

    def _run(self, **kwargs):
        return cleanup_one(self.db, self.jobs, self.downloads, self.uploads, now=self.now,
                           vector_delete=kwargs.pop("vector_delete", Mock()), **kwargs)

    def test_cleanup_scrubs_content_media_and_partial_workspaces_idempotently(self):
        bucket = GridFSBucket(self.db, bucket_name="jobMedia")
        file_id = ObjectId()
        with bucket.open_upload_stream_with_id(file_id, "private.wav") as stream:
            stream.write(b"private recording")
        self.jobs.update_one({"_id": self.id}, {"$set": {"sourceArtifact.fileId": file_id}})
        workspace = self.downloads / f"work-{self.id}-partial"
        workspace.mkdir()
        (workspace / "converted.wav").write_bytes(b"partial")
        incoming = self.uploads / f"{self.id}-{'a' * 32}.wav"
        incoming.write_bytes(b"private source")
        outside = self.downloads / "unrelated.txt"
        outside.write_text("keep")
        self.db["chatmessages"].insert_one({
            "messageId": "r6-delete-message", "job": self.id, "user": ObjectId(),
            "sequence": 1, "question": "private", "answer": "private",
        })
        vector = Mock()
        self.assertTrue(self._run(vector_delete=vector))
        self.assertEqual(vector.call_args.args[0], str(self.id))
        self.assertFalse(workspace.exists())
        self.assertFalse(incoming.exists())
        self.assertEqual(outside.read_text(), "keep")
        self.assertIsNone(self.db["jobMedia.files"].find_one({"_id": file_id}))
        saved = self.jobs.find_one({"_id": self.id})
        self.assertEqual(saved["status"], "deleted")
        for key in ("sourceArtifact", "sourceValue", "transcript", "chat", "execution", "submission"):
            self.assertNotIn(key, saved)
        self.assertFalse(self._run(vector_delete=vector))
        self.assertEqual(vector.call_count, 1)
        self.assertIsNone(self.db["chatmessages"].find_one({"job": self.id}))

    def test_failure_is_visible_and_retries_without_recreating_results(self):
        fail = Mock(side_effect=PermissionError("locked"))
        self.assertTrue(self._run(vector_delete=fail))
        failed = self.jobs.find_one({"_id": self.id})
        self.assertEqual(failed["status"], "deleting")
        self.assertEqual(failed["deletion"]["state"], "pending")
        self.assertEqual(failed["deletion"]["lastError"], "PermissionError")
        self.assertFalse(self._run())  # delay prevents an immediate tight retry loop
        self.now += timedelta(hours=2)
        self.assertTrue(self._run())
        self.assertEqual(self.jobs.find_one({"_id": self.id})["status"], "deleted")

    def test_active_attempt_waits_for_stop_or_lease_timeout(self):
        future = self.now + timedelta(minutes=2)
        self.jobs.update_one({"_id": self.id}, {"$set": {"deletion.workerStoppedAt": None,
            "deletion.waitUntil": future, "deletion.activeAttemptId": "a" * 32}})
        vector = Mock()
        self.assertTrue(self._run(vector_delete=vector))
        vector.assert_not_called()
        self.jobs.update_one({"_id": self.id}, {"$set": {"deletion.workerStoppedAt": self.now,
            "deletion.nextAt": self.now}})
        self.assertTrue(self._run(vector_delete=vector))
        self.assertEqual(self.jobs.find_one({"_id": self.id})["status"], "deleted")

    def test_media_shared_by_older_jobs_waits_until_last_reference(self):
        bucket = GridFSBucket(self.db, bucket_name="jobMedia")
        file_id = ObjectId()
        with bucket.open_upload_stream_with_id(file_id, "shared.wav") as stream:
            stream.write(b"shared")
        other = ObjectId()
        self.jobs.update_one({"_id": self.id}, {"$set": {"sourceArtifact.fileId": file_id}})
        self.jobs.insert_one({"_id": other, "status": "completed", "sourceArtifact": {"fileId": file_id}})
        self._run()
        self.assertIsNotNone(self.db["jobMedia.files"].find_one({"_id": file_id}))
        self.jobs.update_one({"_id": other}, {"$set": {"status": "deleting", "deletion": {
            "state": "pending", "nextAt": self.now, "workerStoppedAt": self.now}}})
        self._run()
        self.assertIsNone(self.db["jobMedia.files"].find_one({"_id": file_id}))

    def test_retention_marks_expired_jobs_before_cleanup(self):
        self.jobs.update_one({"_id": self.id}, {"$set": {"status": "completed",
            "retention.expiresAt": self.now - timedelta(days=1), "execution.attemptId": "b" * 32}})
        self.assertEqual(expire_jobs(self.jobs, self.now), 1)
        marked = self.jobs.find_one({"_id": self.id})
        self.assertEqual(marked["status"], "deleting")
        self.assertNotEqual(marked["execution"]["attemptId"], "b" * 32)
        self.assertEqual(expire_jobs(self.jobs, self.now), 0)
        self.assertTrue(self._run())
        self.assertEqual(self.jobs.find_one({"_id": self.id})["status"], "deleted")

    def test_scratch_rejects_symlinks_and_other_job_paths(self):
        other = self.downloads / f"work-{'b' * 24}-keep"
        other.mkdir()
        (other / "keep").write_text("safe")
        cleanup_scratch(str(self.id), self.downloads, self.uploads)
        self.assertTrue((other / "keep").exists())

    def test_stale_scratch_recovery_preserves_live_and_other_files(self):
        stale = self.downloads / f"work-{self.id}-crashed"
        stale.mkdir()
        (stale / "partial.wav").write_bytes(b"partial")
        leftover = self.uploads / f"{self.id}-{'c' * 32}.wav"
        leftover.write_bytes(b"partial")
        other = self.downloads / "private-user-file.txt"
        other.write_bytes(b"safe")
        old = self.now.timestamp() - 25 * 3600
        os.utime(stale, (old, old))
        os.utime(leftover, (old, old))
        self.jobs.update_one({"_id": self.id}, {"$set": {"status": "processing",
            "execution.leaseUntil": self.now + timedelta(hours=1)}})
        self.assertEqual(reconcile_stale_scratch(self.jobs, self.downloads, self.uploads, now=self.now), 0)
        self.jobs.update_one({"_id": self.id}, {"$set": {"status": "failed"}})
        self.assertEqual(reconcile_stale_scratch(self.jobs, self.downloads, self.uploads, now=self.now), 2)
        self.assertFalse(stale.exists())
        self.assertFalse(leftover.exists())
        self.assertTrue(other.exists())

    def test_orphan_media_scan_preserves_references_and_removes_abandoned_copy(self):
        bucket = GridFSBucket(self.db, bucket_name="jobMedia")
        retained, orphan = ObjectId(), ObjectId()
        for file_id in (retained, orphan):
            with bucket.open_upload_stream_with_id(file_id, "media.wav",
                 metadata={"owner": "job", "purpose": "processing-input"}) as stream:
                stream.write(b"source")
            self.db["jobMedia.files"].update_one({"_id": file_id}, {"$set": {
                "uploadDate": self.now - timedelta(hours=2)}})
        self.jobs.update_one({"_id": self.id}, {"$set": {"sourceArtifact.fileId": retained}})
        self.assertEqual(reconcile_orphan_media(self.db, self.jobs, now=self.now), 1)
        self.assertIsNotNone(self.db["jobMedia.files"].find_one({"_id": retained}))
        self.assertIsNone(self.db["jobMedia.files"].find_one({"_id": orphan}))
