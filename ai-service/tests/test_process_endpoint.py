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
            with patch("dotenv.load_dotenv"), patch.dict("os.environ", {"UPLOAD_DIR": cls.directory.name}):
                spec = importlib.util.spec_from_file_location("source_test_app", BASE / "main.py")
                cls.module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(cls.module)
        cls.module.SERVICE_SECRET = "test-secret"
        cls.worker = pipeline.run_pipeline_job
        cls.client = TestClient(cls.module.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.directory.cleanup()

    def setUp(self):
        self.worker.reset_mock()
        self.fields = {
            "job_id": "test-job", "callback_url": "http://unused.invalid", 
            "callback_secret": "test-secret", "service_secret": "test-secret",
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

    def test_upload_path_is_server_generated_even_for_path_shaped_identifiers(self):
        response = self.client.post("/process", data={**self.fields, "job_id": "../../escape"},
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
