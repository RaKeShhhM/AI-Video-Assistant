"""Exercise real FastAPI form parsing without loading models or running jobs."""
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

BASE = Path(__file__).resolve().parents[1]
CASES = json.loads((BASE.parent / "tests/fixtures/video_sources.json").read_text())
TEST_SECRET = "test-only-internal-secret-0123456789abcdef"
JOB_ID = "6ab43b980163a134e354a9d9"


class ProcessEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        rag = types.ModuleType("core.rag_engine")
        rag.ask_question = Mock()
        rag.load_rag_chain = Mock()
        pipeline = types.ModuleType("pipeline")
        pipeline.run_pipeline_job = Mock()
        with patch.dict(sys.modules, {"core.rag_engine": rag, "pipeline": pipeline}):
            with patch("dotenv.load_dotenv"), patch.dict("os.environ", {
                "UPLOAD_DIR": cls.directory.name, "AI_SERVICE_SECRET": TEST_SECRET,
                "EXPRESS_INTERNAL_URL": "http://localhost:5000",
            }):
                spec = importlib.util.spec_from_file_location("source_test_app", BASE / "main.py")
                cls.module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(cls.module)
        cls.rag = rag
        cls.worker = pipeline.run_pipeline_job
        cls.client = TestClient(cls.module.app, headers={"X-Internal-Secret": TEST_SECRET})

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.directory.cleanup()

    def setUp(self):
        self.worker.reset_mock()
        self.rag.load_rag_chain.reset_mock()
        self.rag.ask_question.reset_mock()
        self.fields = {
            "job_id": JOB_ID,
        }

    def test_invalid_sources_never_write_files_or_schedule_jobs(self):
        for value in [item for item in CASES["invalid"] if isinstance(item, str)]:
            with self.subTest(value=value):
                response = self.client.post("/process", data={**self.fields, "youtube_url": value})
                self.assertEqual(response.status_code, 400, response.text)
        self.worker.assert_not_called()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_both_sources_and_missing_source_are_rejected(self):
        self.assertEqual(self.client.post("/process", data=self.fields).status_code, 400)
        for url in ["", CASES["canonical"]]:
            response = self.client.post("/process", data={**self.fields, "youtube_url": url},
                                        files={"file": ("clip.wav", io.BytesIO(b"test"), "audio/wav")})
            self.assertEqual(response.status_code, 400)
        self.worker.assert_not_called()

    def test_canonical_youtube_source_is_scheduled(self):
        response = self.client.post("/process", data={**self.fields, "youtube_url": CASES["valid"][2]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.worker.call_args.kwargs["source"], CASES["canonical"])
        self.assertEqual(self.worker.call_args.kwargs["source_type"], "youtube")

    def test_upload_path_is_server_generated_even_for_path_shaped_filenames(self):
        response = self.client.post("/process", data=self.fields,
                                    files={"file": ("../../clip.wav", io.BytesIO(b"test"), "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        source = Path(self.worker.call_args.kwargs["source"])
        try:
            self.assertEqual(source.parent, Path(self.directory.name))
            self.assertEqual(source.read_bytes(), b"test")
            self.assertEqual(self.worker.call_args.kwargs["source_type"], "upload")
        finally:
            source.unlink()

    def test_repeated_url_fields_are_rejected(self):
        fields = [(key, (None, value)) for key, value in self.fields.items()]
        fields.extend([("youtube_url", (None, CASES["canonical"]))] * 2)
        response = self.client.post("/process", files=fields)
        self.assertEqual(response.status_code, 400)
        self.worker.assert_not_called()

    def test_authentication_is_required_before_parsing_body(self):
        with TestClient(self.module.app) as client:
            for path in ["/process", "/ask", "/process/", "/ask/"]:
                for headers in [{}, {"X-Internal-Secret": "wrong"}]:
                    response = client.post(path, content=b"not even a valid request", headers=headers)
                    self.assertEqual(response.status_code, 401)
            response = client.post("/ask", json={"job_id": JOB_ID, "question": "test", "service_secret": TEST_SECRET})
            self.assertEqual(response.status_code, 401)
        self.worker.assert_not_called()
        self.rag.load_rag_chain.assert_not_called()

    def test_duplicate_auth_headers_are_rejected(self):
        with TestClient(self.module.app) as client:
            response = client.post("/ask", headers=[("X-Internal-Secret", TEST_SECRET)] * 2,
                                   json={"job_id": JOB_ID, "question": "test"})
            self.assertEqual(response.status_code, 401)

    def test_legacy_callback_overrides_are_rejected(self):
        for key in ["callback_url", "callback_secret", "service_secret"]:
            response = self.client.post("/process", data={**self.fields,
                "youtube_url": CASES["canonical"], key: "https://untrusted.invalid"})
            self.assertEqual(response.status_code, 400)
        self.worker.assert_not_called()

    def test_invalid_job_ids_cannot_schedule_jobs_or_load_vectors(self):
        for job_id in ["../../escape", "C:\\private", "x" * 24, "", "a" * 25]:
            response = self.client.post("/process", data={**self.fields, "job_id": job_id,
                                        "youtube_url": CASES["canonical"]})
            self.assertIn(response.status_code, (400, 422))
            response = self.client.post("/ask", json={"job_id": job_id, "question": "test"})
            self.assertEqual(response.status_code, 422)
        self.worker.assert_not_called()
        self.rag.load_rag_chain.assert_not_called()

    def test_valid_header_allows_ask_without_body_credentials(self):
        self.rag.ask_question.return_value = "test answer"
        response = self.client.post("/ask", json={"job_id": JOB_ID, "question": "test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"answer": "test answer"})
        self.rag.load_rag_chain.assert_called_once_with(JOB_ID)

    def test_ask_rejects_body_credentials_even_with_valid_header(self):
        response = self.client.post("/ask", json={"job_id": JOB_ID, "question": "test", "service_secret": TEST_SECRET})
        self.assertEqual(response.status_code, 422)
        self.rag.load_rag_chain.assert_not_called()
