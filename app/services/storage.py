"""Audio file storage behind a narrow interface.

Local filesystem for the demo. The rest of the system only ever sees an opaque
path string, so an S3-backed implementation swaps in without touching the
domain, the pipeline, or the API.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import get_settings

# Restrictive on purpose. An upload endpoint taking arbitrary extensions
# is an arbitrary file write.
ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac", ".txt"}


class UploadRejected(Exception):
    """Raised when an upload fails validation."""


def store_audio(*, filename: str, content: bytes) -> str:
    """Persist an upload and return its stored path.

    The stored name is generated, never derived from user input: the client
    filename is untrusted and path traversal via '../' is a real attack, not a
    theoretical one. The original suffix is validated against an allowlist and
    kept only for readability.
    """
    settings = get_settings()

    if not content:
        raise UploadRejected("audio upload is empty")
    if len(content) > settings.max_audio_bytes:
        raise UploadRejected(
            f"audio upload exceeds {settings.max_audio_bytes} bytes"
        )

    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
        raise UploadRejected(f"unsupported audio type '{suffix}'; allowed: {allowed}")

    destination = settings.audio_storage_dir / f"{uuid.uuid4().hex}{suffix}"
    destination.write_bytes(content)
    return str(destination)
