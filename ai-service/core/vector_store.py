import os
import re
from pathlib import Path
from chromadb.errors import NotFoundError
from utils.index_errors import IndexRebuildRequired
from utils.job_ids import validate_job_id
from utils.chroma_local import local_chroma_client
from langchain_chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# Base directory that holds one Chroma collection per job, so concurrent
# users/jobs never share or clobber each other's vectors.
CHROMA_BASE_DIR = os.getenv("VECTOR_DB_DIR", "storage/vector_db")
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_embeddings = None


def get_embeddings():
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu", "trust_remote_code": False},
        )
    return _embeddings


def _collection_name(job_id: str, index_attempt: str | None = None) -> str:
    validate_job_id(job_id)
    if index_attempt is not None and not re.fullmatch(r"[a-f0-9]{32}", index_attempt):
        raise ValueError("Invalid index attempt")
    return f"job_{job_id}" + (f"_{index_attempt}" if index_attempt else "")


def _persist_dir(job_id: str, *, create=True) -> str:
    validate_job_id(job_id)
    root = Path(CHROMA_BASE_DIR).resolve()
    path = (root / job_id).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Vector storage must stay inside the configured directory.")
    if create:
        os.makedirs(path, exist_ok=True)
    return str(path)


def build_vector_store(transcript: str, job_id: str, index_attempt: str | None = None) -> Chroma:
    validate_job_id(job_id)
    collection_name = _collection_name(job_id, index_attempt)
    print(f"Building vector store for job {job_id}")

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_text(transcript)

    if not chunks:
        raise ValueError("Cannot index an empty transcript")
    ids = [f"chunk_{i}" for i in range(len(chunks))]
    docs = [
        Document(page_content=chunk, metadata={"chunk_index": i})
        for i, chunk in enumerate(chunks)
    ]

    embeddings = get_embeddings()
    vector_store = Chroma.from_documents(
        documents=docs,
        ids=ids,
        embedding=embeddings,
        collection_name=collection_name,
        client=local_chroma_client(_persist_dir(job_id)),
    )

    # Upsert stable IDs, then remove chunks from a previous, longer rebuild.
    # A different worker attempt uses a different collection and cannot touch
    # the generation referenced by the completed MongoDB job.
    stale = set(vector_store.get()["ids"]) - set(ids)
    if stale:
        vector_store.delete(ids=list(stale))
    return vector_store


def load_vector_store(job_id: str, index_attempt: str | None = None) -> Chroma:
    validate_job_id(job_id)
    collection_name = _collection_name(job_id, index_attempt)
    path = Path(_persist_dir(job_id, create=False))
    # Do not let a read initialize an empty database after an ephemeral-disk loss.
    if not (path / "chroma.sqlite3").is_file():
        raise IndexRebuildRequired()
    client = local_chroma_client(str(path))
    try:
        collection = client.get_collection(collection_name)
    except NotFoundError as exc:
        raise IndexRebuildRequired() from exc
    if collection.count() == 0:
        raise IndexRebuildRequired()
    return Chroma(
        collection_name=collection_name,
        embedding_function=get_embeddings(),
        client=client,
        create_collection_if_not_exists=False,
    )


def get_retriever(vector_store : Chroma, k :int = 4):
    return vector_store.as_retriever(
        search_type = 'similarity',
        search_kwargs = {"k":k}
    )
