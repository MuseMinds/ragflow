# Copyright 2026 The InfiniFlow Authors. All Rights Reserved.

"""Immutable MuseMind Gemini generation-v2 runtime contract."""

from __future__ import annotations

from dataclasses import dataclass

MUSEMIND_GEMINI_EMBEDDING_MODEL = "gemini-embedding-2"
MUSEMIND_GEMINI_EMBEDDING_ID = "gemini-embedding-2@musemind@Gemini"
MUSEMIND_GEMINI_CHAT_MODEL = "gemini-3.1-flash-lite"
MUSEMIND_GEMINI_CHAT_ID = "gemini-3.1-flash-lite@musemind@Gemini"
MUSEMIND_GEMINI_IMAGE_MODEL = "gemini-3.5-flash"
MUSEMIND_GEMINI_IMAGE_ID = "gemini-3.5-flash@musemind@Gemini"

EMBEDDING_DIMENSION = 3072
EMBEDDING_TEXT_BATCH_SIZE = 16
EMBEDDING_IMAGE_BATCH_SIZE = 6
MAX_TEXT_TOKENS = 8192
MAX_IMAGE_BYTES = 4 * 1024 * 1024
RETRYABLE_HTTP_STATUSES = (408, 429, 500, 502, 503, 504)
REQUEST_TIMEOUT_MS = 10_000
REQUEST_ATTEMPTS = 3
PRIVATE_IMAGE_FIELD = "_musemind_embedding_image"

FIGURE_PROMPT_SHA256 = "97042f2f5ffa00cd18065e32b74841aa1a507bcd4c9a76d954dc0c91d42bab59"
PAGE_PROMPT_SHA256 = "15f6c379d964e47a33bcd9c747a5990a010f46e6c0c71b1824eb52343c17047f"


@dataclass(frozen=True, slots=True)
class GeminiPassage:
    """A passage whose title must remain distinct until wire serialization."""

    title: str
    content: str


def is_musemind_gemini_embedding(model_name: str | None, factory: str | None = None) -> bool:
    return model_name == MUSEMIND_GEMINI_EMBEDDING_MODEL and factory in (None, "Gemini")


def image_mime_type(data: bytes) -> str:
    """Validate generation-v2 image bytes without decoding or rewriting them."""
    if not isinstance(data, bytes):
        raise ValueError("Gemini image input must be bytes")
    if not data:
        raise ValueError("Gemini image input is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Gemini image input exceeds the 4194304-byte limit")
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    raise ValueError("Gemini image input must be JPEG or PNG")


def provider_http_options(types):
    """Return the bounded retry/timeout policy shared by the pinned Gemini models."""
    return types.HttpOptions(
        timeout=REQUEST_TIMEOUT_MS,
        retry_options=types.HttpRetryOptions(
            attempts=REQUEST_ATTEMPTS,
            # The contract's 30-second total deadline is three 10-second
            # attempts; retry sleeps must not extend it.
            initial_delay=0.0,
            max_delay=0.0,
            exp_base=2.0,
            jitter=0.0,
            http_status_codes=list(RETRYABLE_HTTP_STATUSES),
        ),
    )
