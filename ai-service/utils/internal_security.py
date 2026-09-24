"""Configuration and transport for the trusted Express/AI service boundary."""
import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests


def validate_secret(value, name="AI_SERVICE_SECRET"):
    if (not isinstance(value, str) or not 32 <= len(value) <= 256
            or not re.fullmatch(r"[!-~]+", value)
            or any(marker in value.lower() for marker in ("change_this", "changeme", "replace_me", "your_secret"))):
        raise RuntimeError(f"{name} must be a non-placeholder secret of 32-256 printable ASCII characters without spaces.")
    return value


def validate_origin(value):
    try:
        url = urlsplit(value)
        if (not value or re.search(r"[\s\\%]", value) or url.scheme not in {"http", "https"}
                or not url.hostname or url.username is not None or url.password is not None
                or url.path not in {"", "/"} or url.query or url.fragment
                or url.port == 0):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("EXPRESS_INTERNAL_URL must be an HTTP(S) origin without credentials, path, query or fragment.") from None
    return value.rstrip("/")


@dataclass(frozen=True)
class InternalSettings:
    secret: str = field(repr=False)
    callback_url: str


def load_internal_settings():
    secret = validate_secret(os.getenv("AI_SERVICE_SECRET"))
    origin = validate_origin(os.getenv("EXPRESS_INTERNAL_URL", ""))
    return InternalSettings(secret, f"{origin}/api/internal/jobs/progress")


def secret_matches(provided, expected):
    if not isinstance(provided, str) or not provided:
        return False
    # Equal-sized digests avoid variable-length secret comparisons.
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def post_progress(payload):
    settings = load_internal_settings()
    response = requests.post(
        settings.callback_url,
        json=payload,
        headers={"X-Internal-Secret": settings.secret},
        timeout=15,
        allow_redirects=False,
    )
    # Never forward the internal header to a redirect target.
    if 300 <= response.status_code < 400:
        raise RuntimeError("Internal callback redirects are not allowed.")
    response.raise_for_status()
