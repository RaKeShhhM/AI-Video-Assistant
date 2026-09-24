import importlib.util
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from utils.internal_security import load_internal_settings, post_progress, secret_matches, validate_secret
from utils.job_ids import validate_job_id

BASE = Path(__file__).resolve().parents[1]
SECRET = "test-only-internal-secret-0123456789abcdef"
JOB_ID = "6ab43b980163a134e354a9d9"
ENV = {"AI_SERVICE_SECRET": SECRET, "EXPRESS_INTERNAL_URL": "http://server:5000"}


class InternalSecurityTests(unittest.TestCase):
    def test_bad_secrets_fail_closed(self):
        for value in [None, "", "short", "change_this_shared_secret" * 2, "x" * 257, "a b" * 20]:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                validate_secret(value)
        self.assertEqual(validate_secret(SECRET), SECRET)
        self.assertTrue(secret_matches(SECRET, SECRET))
        for value in [None, "", "wrong", [SECRET], "é" * 40]:
            self.assertFalse(secret_matches(value, SECRET))

    def test_startup_stops_before_model_imports_if_secret_is_missing(self):
        result = subprocess.run([sys.executable, "-c", "import main"], cwd=BASE,
                                env={**os.environ, **ENV, "AI_SERVICE_SECRET": ""},
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AI_SERVICE_SECRET must", result.stderr)

    def test_callback_origin_is_required_and_cannot_include_credentials_or_path(self):
        for value in ["", "file:///private", "https://user:pass@example.com", "https://example.com/path", "https://example.com?query=x", "https://example.com#fragment", "http://server:99999", "http://server\\evil"]:
            with patch.dict(os.environ, {**ENV, "EXPRESS_INTERNAL_URL": value}), self.assertRaises(RuntimeError):
                load_internal_settings()
        with patch.dict(os.environ, ENV):
            settings = load_internal_settings()
            self.assertEqual(settings.callback_url, "http://server:5000/api/internal/jobs/progress")
            self.assertNotIn(SECRET, repr(settings))

    def test_callback_destination_and_header_come_only_from_configuration(self):
        with patch.dict(os.environ, ENV), patch("utils.internal_security.requests.post") as post:
            post.return_value.status_code = 200
            payload = {"job_id": JOB_ID, "status": "processing"}
            post_progress(payload)
            post.assert_called_once_with("http://server:5000/api/internal/jobs/progress",
                json=payload, headers={"X-Internal-Secret": SECRET}, timeout=15, allow_redirects=False)

    def test_callback_redirects_never_get_followed(self):
        with patch.dict(os.environ, ENV), patch("utils.internal_security.requests.post") as post:
            post.return_value.status_code = 307
            with self.assertRaisesRegex(RuntimeError, "redirects"):
                post_progress({"job_id": JOB_ID})
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
            self.assertEqual(post.call_count, 1)

    def test_invalid_ids_are_rejected(self):
        for value in [None, "", "../escape", "C:\\private", "a" * 23, "A" * 24, "g" * 24]:
            with self.assertRaises(ValueError):
                validate_job_id(value)
        self.assertEqual(validate_job_id(JOB_ID), JOB_ID)

    def test_vector_storage_rechecks_ids_before_filesystem_or_embeddings(self):
        modules = {}
        for name, attribute in [("langchain_chroma", "Chroma"), ("langchain_community.embeddings", "HuggingFaceEmbeddings"), ("langchain_text_splitters", "RecursiveCharacterTextSplitter"), ("langchain_core.documents", "Document")]:
            modules[name] = types.ModuleType(name)
            setattr(modules[name], attribute, Mock())
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location("vector_security_test", BASE / "core/vector_store.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            module.CHROMA_BASE_DIR = directory
            for operation in [module._persist_dir, module._collection_name, module.load_vector_store,
                              lambda job: module.build_vector_store("transcript", job)]:
                with self.assertRaises(ValueError):
                    operation("../../escape")
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(Path(module._persist_dir(JOB_ID)), Path(directory).resolve() / JOB_ID)
        modules["langchain_community.embeddings"].HuggingFaceEmbeddings.assert_not_called()
