"""No provider calls or model downloads; real storage is confined to temp paths."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from utils.chroma_local import LOCAL_API, local_chroma_client

BASE = Path(__file__).resolve().parents[1]


class ChromaBoundaryTests(unittest.TestCase):
    def test_remote_configuration_is_rejected_before_client_creation(self):
        for key, value in {
            "CHROMA_API_IMPL": "chromadb.api.fastapi.FastAPI",
            "CHROMA_SERVER_HOST": "example.invalid", "CHROMA_SERVER_HTTP_PORT": "8000",
            "CHROMA_CLOUD_API_KEY": "test", "CHROMA_CLIENT_AUTH_PROVIDER": "test",
            "CHROMA_SERVER_AUTHN_PROVIDER": "test", "CHROMA_SERVER_AUTHZ_PROVIDER": "test",
        }.items():
            with self.subTest(key=key), patch.dict(os.environ, {key: value}), \
                    patch("utils.chroma_local.chromadb.PersistentClient") as client:
                with self.assertRaises(RuntimeError): local_chroma_client("unused")
                client.assert_not_called()

    def test_version_change_requires_review(self):
        with patch("utils.chroma_local.version", return_value="1.5.10"), \
                patch("utils.chroma_local.chromadb.PersistentClient") as client:
            with self.assertRaises(RuntimeError): local_chroma_client("unused")
            client.assert_not_called()

    def test_local_settings_override_environment_and_disable_remote_providers(self):
        with patch.dict(os.environ, {"ANONYMIZED_TELEMETRY": "true", "ALLOW_RESET": "true"}), \
                patch("utils.chroma_local.chromadb.PersistentClient") as client:
            local_chroma_client("owned-test-path")
            settings = client.call_args.kwargs["settings"]
            self.assertEqual(settings.chroma_api_impl, LOCAL_API)
            self.assertIsNone(settings.chroma_server_host)
            self.assertIsNone(settings.chroma_server_http_port)
            self.assertIsNone(settings.chroma_server_authz_provider)
            self.assertFalse(settings.anonymized_telemetry)
            self.assertFalse(settings.allow_reset)

    def test_real_storage_roundtrip_existing_format_and_job_isolation_without_network(self):
        # Exit the child before deleting the directory: Rust/SQLite can retain
        # open handles for the client's lifetime, especially on Windows.
        script = r'''
import os, sys, types, socket
from unittest.mock import Mock, patch
from langchain_core.embeddings import Embeddings
from langchain_core.documents import Document
from langchain_chroma import Chroma
from chromadb.config import Settings

stub = types.ModuleType("langchain_community.embeddings")
stub.HuggingFaceEmbeddings = Mock()
sys.modules["langchain_community.embeddings"] = stub
from core import vector_store as store
class FixedEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]
    def embed_query(self, text):
        return [1.0, 0.0] if "alpha" in text else [0.0, 1.0]

store.CHROMA_BASE_DIR = sys.argv[1]
store.get_embeddings()
assert stub.HuggingFaceEmbeddings.call_args.kwargs["model_kwargs"]["trust_remote_code"] is False
assert stub.HuggingFaceEmbeddings.call_args.kwargs["model_name"] == "all-MiniLM-L6-v2"
store._embeddings = FixedEmbeddings()
a, b = "a" * 24, "b" * 24
with patch.object(socket.socket, "connect", side_effect=AssertionError("Unexpected network connection")):
    # Write with the previous adapter/persist_directory API, then reopen with
    # the new explicit-client path. Never touch an existing user's index.
    Chroma.from_documents([Document(page_content="legacy alpha")], store.get_embeddings(),
        collection_name=store._collection_name(a), persist_directory=store._persist_dir(a),
        client_settings=Settings(anonymized_telemetry=False))
    loaded = store.load_vector_store(a)
    assert loaded.similarity_search("alpha", k=1)[0].page_content == "legacy alpha"
    other = store.build_vector_store("private beta", b)
    assert store.get_retriever(other, k=1).invoke("beta")[0].page_content == "private beta"
    assert store.load_vector_store(a).similarity_search("beta", k=1)[0].page_content == "legacy alpha"
    assert loaded._client.get_settings().chroma_api_impl == "chromadb.api.rust.RustBindingsAPI"
    assert "chromadb.server.fastapi" not in sys.modules
print("Real legacy-format read, embedded writes, retrieval and isolation passed")
'''
        with tempfile.TemporaryDirectory(prefix="reel-chroma-test-") as directory:
            result = subprocess.run([sys.executable, "-c", script, directory], cwd=BASE,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
