import os
from pathlib import Path
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


def _collection_name(job_id: str) -> str:
    validate_job_id(job_id)
    return f"job_{job_id}"


def _persist_dir(job_id: str) -> str:
    validate_job_id(job_id)
    root = Path(CHROMA_BASE_DIR).resolve()
    path = (root / job_id).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Vector storage must stay inside the configured directory.")
    os.makedirs(path, exist_ok=True)
    return str(path)


def build_vector_store(transcript: str, job_id: str) -> Chroma:
    validate_job_id(job_id)
    print(f"Building vector store for job {job_id}")

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_text(transcript)

    docs = [
        Document(page_content=chunk, metadata={"chunk_index": i})
        for i, chunk in enumerate(chunks)
    ]

    embeddings = get_embeddings()
    vector_store = Chroma.from_documents(
        documents=docs,
        embedding=embeddings,
        collection_name=_collection_name(job_id),
        client=local_chroma_client(_persist_dir(job_id)),
    )

    return vector_store


def load_vector_store(job_id: str) -> Chroma:
    validate_job_id(job_id)
    embeddings = get_embeddings()
    vector_store = Chroma(
        collection_name=_collection_name(job_id),
        embedding_function=embeddings,
        client=local_chroma_client(_persist_dir(job_id)),
    )

    return vector_store

def get_retriever(vector_store : Chroma, k :int = 4):
    return vector_store.as_retriever(
        search_type = 'similarity',
        search_kwargs = {"k":k}
    )
