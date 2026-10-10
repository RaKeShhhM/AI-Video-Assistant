"""A writer exits before a fresh reader opens the same on-disk index."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import sys, types, socket
from pathlib import Path
from unittest.mock import Mock, patch
from langchain_core.embeddings import Embeddings
stub = types.ModuleType("langchain_community.embeddings")
stub.HuggingFaceEmbeddings = Mock(side_effect=AssertionError("No model downloads"))
sys.modules["langchain_community.embeddings"] = stub
from core import vector_store as store
from utils.index_errors import IndexRebuildRequired
class Fixed(Embeddings):
    def embed_documents(self, texts): return [self.embed_query(t) for t in texts]
    def embed_query(self, text): return [1.0, 0.0]
store._embeddings = Fixed()
job = "a" * 24
with patch.object(socket.socket, "connect", side_effect=AssertionError("No network")):
    if sys.argv[1] == "write":
        store.build_vector_store("The launch deadline is Friday.", job)
        # Stable IDs make rebuilds idempotent and remove trailing old chunks.
        old_attempt, new_attempt = "1" * 32, "2" * 32
        store.build_vector_store("Old transcript. " * 200, job, old_attempt)
        fresh = store.build_vector_store("Current transcript.", job, new_attempt)
        store.build_vector_store("Current transcript.", job, new_attempt)
        assert len(fresh.get()["ids"]) == 1
        # A stale worker's late rebuild stays in its own attempt collection.
        stale = store.build_vector_store("Stale worker result.", job, old_attempt)
        assert len(stale.get()["ids"]) == 1
        assert fresh.get()["documents"] == ["Current transcript."]
        try: store.load_vector_store(job, "../invalid")
        except ValueError: pass
        else: raise AssertionError("Invalid generation accepted")
        client = store.local_chroma_client(store._persist_dir("b" * 24))
        client.create_collection(store._collection_name("b" * 24))
        store.local_chroma_client(store._persist_dir("c" * 24))
    else:
        found = store.get_retriever(store.load_vector_store(job)).invoke("What is the launch deadline?")
        assert found[0].page_content == "The launch deadline is Friday."
        current = store.load_vector_store(job, "2" * 32)
        assert current.get()["documents"] == ["Current transcript."]
        try: store.load_vector_store(job, "3" * 32)
        except IndexRebuildRequired: pass
        else: raise AssertionError("Missing generation accepted")
        # Existing but empty collection, missing collection, missing directory:
        for missing in ["b" * 24, "c" * 24, "d" * 24]:
            with patch.object(store, "get_embeddings", side_effect=AssertionError("No embeddings for missing indexes")):
                try: store.load_vector_store(missing)
                except IndexRebuildRequired: pass
                else: raise AssertionError("Missing index was accepted")
        assert not Path(store._persist_dir("d" * 24, create=False)).exists()
        client = store.local_chroma_client(store._persist_dir("c" * 24, create=False))
        assert client.list_collections() == []
print("OK")
'''

class VectorPersistenceTests(unittest.TestCase):
    def test_fresh_process_retrieves_persisted_index_and_missing_indexes_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "VECTOR_DB_DIR": directory}
            for mode in ["write", "read"]:
                result = subprocess.run([sys.executable, "-c", SCRIPT, mode], cwd=BASE,
                    env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("OK", result.stdout)
