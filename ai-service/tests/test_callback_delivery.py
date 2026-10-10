"""Real MongoDB delivery/restart tests; providers and HTTP responses are fixtures."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from bson import ObjectId
from pymongo import MongoClient
import requests
from utils.callback_delivery import deliver_one, event_update, retry_blocked, MAX_DELIVERIES

BASE = Path(__file__).resolve().parents[1]

class CallbackDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = None
        cls.directory = None
        cls.uri = os.getenv("CALLBACK_TEST_MONGO_URI")
        if not cls.uri:
            binary = shutil.which("mongod")
            if not binary:
                raise unittest.SkipTest("Set CALLBACK_TEST_MONGO_URI or install mongod for delivery integration tests")
            cls.directory = tempfile.TemporaryDirectory(prefix="reel-r3-")
            with socket.socket() as port:
                port.bind(("127.0.0.1", 0))
                number = port.getsockname()[1]
            cls.process = subprocess.Popen([binary, "--dbpath", cls.directory.name, "--port", str(number), "--bind_ip", "127.0.0.1", "--quiet"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            cls.uri = f"mongodb://127.0.0.1:{number}"
        cls.client = MongoClient(cls.uri, serverSelectionTimeoutMS=300, tz_aware=True)
        for _ in range(50):
            try:
                cls.client.admin.command("ping")
                break
            except Exception:
                time.sleep(0.1)
        else:
            cls.tearDownClass()
            raise RuntimeError("Test MongoDB unavailable")
        cls.database_name = "r3_test_" + uuid.uuid4().hex
        cls.jobs = cls.client[cls.database_name].jobs

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
        self.now = datetime.now(timezone.utc)
        self.id = ObjectId()
        self.attempt = uuid.uuid4().hex
        self.jobs.insert_one({"_id": self.id, "execution": {"attemptId": self.attempt}, "status": "processing"})
        self.publish("completed")

    def publish(self, status):
        self.jobs.update_one({"_id": self.id}, event_update({"status": status, "summary": "saved result"}, now=self.now))

    def ack(self, payload):
        response = Mock()
        response.json.return_value = {"received": True, **payload}
        return response

    def test_success_deduplicates_and_keeps_result(self):
        send = Mock(side_effect=self.ack)
        self.assertTrue(deliver_one(self.jobs, now=self.now, send=send))
        self.assertFalse(deliver_one(self.jobs, now=self.now, send=send))
        self.assertEqual(send.call_count, 1)
        job = self.jobs.find_one({"_id": self.id})
        self.assertEqual(job["delivery"]["state"], "delivered")
        self.assertEqual(job["summary"], "saved result")

    def test_transient_failures_retry_and_permanent_errors_remain_visible(self):
        for status, expected in [(429, "pending"), (500, "pending"), (503, "pending"), (400, "blocked"), (401, "blocked"), (404, "blocked"), (413, "blocked")]:
            with self.subTest(status=status):
                self.publish("completed")
                response = requests.Response()
                response.status_code = status
                response.headers["Retry-After"] = "5"
                deliver_one(self.jobs, now=self.now, send=Mock(side_effect=requests.HTTPError(response=response)))
                job = self.jobs.find_one({"_id": self.id})
                self.assertEqual(job["delivery"]["state"], expected)
                self.assertGreater(job["delivery"]["nextAt"], self.now)
                self.assertEqual(job["status"], "completed")
        self.assertEqual(retry_blocked(self.jobs), 1)
        self.assertEqual(self.jobs.find_one()["delivery"]["attempts"], 0)

    def test_retry_budget_and_timeouts_do_not_repeat_ai_work(self):
        for attempt in range(MAX_DELIVERIES):
            deliver_one(self.jobs, now=self.now + timedelta(minutes=attempt * 10), send=Mock(side_effect=requests.Timeout()))
        job = self.jobs.find_one()
        self.assertEqual(job["delivery"]["state"], "blocked")
        self.assertEqual(job["delivery"]["attempts"], MAX_DELIVERIES)
        self.assertEqual(job["status"], "completed")

    def test_new_snapshot_supersedes_inflight_ack(self):
        def send(payload):
            self.publish("completed")
            return self.ack(payload)
        deliver_one(self.jobs, now=self.now, send=send)
        job = self.jobs.find_one()
        self.assertEqual(job["progressVersion"], 2)
        self.assertEqual(job["delivery"]["state"], "pending")
        deliver_one(self.jobs, now=self.now, send=self.ack)
        self.assertEqual(self.jobs.find_one()["delivery"]["state"], "delivered")

    def test_wrong_ack_cannot_clear_pending_event(self):
        response = Mock()
        response.json.return_value = {"received": True, "sequence": 99, "attempt_id": self.attempt}
        deliver_one(self.jobs, now=self.now, send=Mock(return_value=response))
        self.assertEqual(self.jobs.find_one()["delivery"]["state"], "pending")

    def test_process_crash_releases_delivery_lease_for_fresh_process(self):
        script = '''
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from pymongo import MongoClient
from utils.callback_delivery import deliver_one
client = MongoClient(sys.argv[1])
def send(payload):
    if sys.argv[3] == "crash": raise SystemExit(23)
    return Mock(json=lambda: {"received": True, **payload})
deliver_one(client[sys.argv[2]].jobs, now=datetime.now(timezone.utc)+timedelta(minutes=int(sys.argv[4])), send=send)
client.close()
'''
        for mode, minutes, code in [("crash", 0, 23), ("ack", 2, 0)]:
            result = subprocess.run([sys.executable, "-c", script, self.uri, self.database_name, mode, str(minutes)], cwd=BASE, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(self.jobs.find_one()["delivery"]["state"], "delivered")

    def test_real_gateway_reconciles_after_outage_and_ack_loss(self):
        import json
        from unittest.mock import patch
        from utils.internal_security import post_progress
        server = BASE.parent / "server"
        if not shutil.which("node") or not (server / "node_modules" / "express").exists():
            self.skipTest("Install server dependencies for the gateway integration")
        secret = "test-only-r3-secret-0123456789abcdef0123456789"
        env = {**os.environ, "CALLBACK_GATEWAY_TEST_URI": self.uri.rstrip("/") + "/" + self.database_name, "INTERNAL_AI_SECRET": secret}
        process = subprocess.Popen(["node", "test-support/callbackGateway.js"], cwd=server, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            # Wait for the listener without an unbounded blocking readline.
            from concurrent.futures import ThreadPoolExecutor
            reader = ThreadPoolExecutor(max_workers=1)
            line = reader.submit(process.stdout.readline)
            try:
                port = int(line.result(timeout=15).strip())
            except BaseException:
                process.terminate()
                raise
            finally:
                reader.shutdown(wait=False)
            user = ObjectId()
            self.jobs.update_one({"_id": self.id}, {"$set": {"user": user, "durationSeconds": 10}})
            usage = self.client[self.database_name].resourceusages
            usage.insert_one({"_id": str(user), "day": "2026-10-11", "secondsReserved": 60,
                "activeJobs": [{"jobId": str(self.id), "day": "2026-10-11", "seconds": 60}], "questions": 0})
            with patch.dict(os.environ, {"AI_SERVICE_SECRET": secret, "EXPRESS_INTERNAL_URL": f"http://127.0.0.1:{port}"}):
                def lose_ack(payload):
                    post_progress(payload)
                    raise requests.Timeout("response lost after commit")
                deliver_one(self.jobs, now=self.now, send=lose_ack)
                self.assertEqual(self.jobs.find_one()["callbackAck"], 1)
                self.assertEqual(usage.find_one()["secondsReserved"], 10)
                deliver_one(self.jobs, now=self.now + timedelta(minutes=1), send=post_progress)
                self.assertEqual(self.jobs.find_one()["delivery"]["state"], "delivered")
                self.assertEqual(usage.find_one()["secondsReserved"], 10)
                # A delayed older-attempt event is acknowledged but cannot change state.
                response = post_progress({"job_id": str(self.id), "attempt_id": "f" * 32, "sequence": 1})
                self.assertTrue(response.json()["ignored"])
                self.assertEqual(self.jobs.find_one()["status"], "completed")
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
