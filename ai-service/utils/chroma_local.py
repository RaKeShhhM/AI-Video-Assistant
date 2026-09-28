"""Reviewed embedded-only Chroma configuration; see security/chroma-review.md."""
import os
from importlib.metadata import version

import chromadb
from chromadb.config import Settings

REVIEWED_CHROMA_VERSION = "1.5.9"
LOCAL_API = "chromadb.api.rust.RustBindingsAPI"


def local_chroma_client(directory):
    if version("chromadb") != REVIEWED_CHROMA_VERSION:
        raise RuntimeError("Chroma version changed; review the embedded storage security policy.")
    if os.environ.get("CHROMA_API_IMPL", LOCAL_API) != LOCAL_API:
        raise RuntimeError("Only embedded Chroma is supported.")
    for key in ("CHROMA_SERVER_HOST", "CHROMA_SERVER_HTTP_PORT", "CHROMA_CLOUD_API_KEY",
                "CHROMA_CLIENT_AUTH_PROVIDER", "CHROMA_SERVER_AUTHN_PROVIDER",
                "CHROMA_SERVER_AUTHZ_PROVIDER"):
        if os.environ.get(key):
            raise RuntimeError(f"{key} is not supported by embedded storage.")
    settings = Settings(
        _env_file=None,
        chroma_api_impl=LOCAL_API,
        chroma_server_host=None,
        chroma_server_http_port=None,
        chroma_client_auth_provider=None,
        chroma_server_authn_provider=None,
        chroma_server_authz_provider=None,
        anonymized_telemetry=False,
        allow_reset=False,
    )
    return chromadb.PersistentClient(path=directory, settings=settings)
