"""Strict content-free live probe for MuseMind Gemini generation v2."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from collections.abc import Callable
from pathlib import Path

from api.db.musemind_provider_identity import ReconciliationConflict, read_secret_file
from rag.llm.embedding_model import GeminiEmbed
from rag.llm.musemind_gemini import (
    EMBEDDING_DIMENSION,
    MUSEMIND_GEMINI_CHAT_MODEL,
    MUSEMIND_GEMINI_EMBEDDING_MODEL,
    MUSEMIND_GEMINI_IMAGE_MODEL,
    GeminiPassage,
    log_gemini_failure,
    provider_http_options,
)

SCHEMA_VERSION = "musemind.gemini-generation-probe/v1"
ENDPOINT_HOSTNAME = "generativelanguage.googleapis.com"
_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def run_probe(
    api_key: str,
    *,
    embedding_factory: Callable[..., GeminiEmbed] = GeminiEmbed,
    client_factory: Callable[..., object] | None = None,
) -> dict[str, object]:
    from google import genai
    from google.genai import types

    embedding = embedding_factory(api_key, MUSEMIND_GEMINI_EMBEDDING_MODEL)
    passage_vectors, _ = embedding.encode([GeminiPassage(title="synthetic", content="synthetic")])
    image_vectors, _ = embedding.encode([_PNG])
    query_vector, _ = embedding.encode_queries("synthetic")
    if passage_vectors.shape != (1, EMBEDDING_DIMENSION) or image_vectors.shape != (1, EMBEDDING_DIMENSION) or query_vector.shape != (EMBEDDING_DIMENSION,):
        raise RuntimeError("Gemini probe dimension mismatch")

    factory = client_factory or genai.Client
    client = factory(api_key=api_key, http_options=provider_http_options(types))
    try:
        chat = client.models.generate_content(
            model=MUSEMIND_GEMINI_CHAT_MODEL,
            contents=[types.Content(role="user", parts=[types.Part(text="synthetic")])],
            config=types.GenerateContentConfig(candidate_count=1, max_output_tokens=8, thinking_config=types.ThinkingConfig(thinking_budget=0)),
        )
        if not getattr(chat, "text", None):
            raise RuntimeError
    except Exception as error:
        log_gemini_failure("CHAT", error)
        raise RuntimeError("Gemini CHAT probe failed") from None

    try:
        image = client.models.generate_content(
            model=MUSEMIND_GEMINI_IMAGE_MODEL,
            contents=[types.Content(role="user", parts=[types.Part(text="synthetic"), types.Part.from_bytes(data=_PNG, mime_type="image/png")])],
            config=types.GenerateContentConfig(candidate_count=1, max_output_tokens=8, thinking_config=types.ThinkingConfig(thinking_budget=0)),
        )
        if not getattr(image, "text", None):
            raise RuntimeError
    except Exception as error:
        log_gemini_failure("IMAGE_TO_TEXT", error)
        raise RuntimeError("Gemini IMAGE_TO_TEXT probe failed") from None

    return {
        "schema_version": SCHEMA_VERSION,
        "outcome": "PASSED",
        "endpoint_hostname": ENDPOINT_HOSTNAME,
        "embedding_model": MUSEMIND_GEMINI_EMBEDDING_MODEL,
        "chat_model": MUSEMIND_GEMINI_CHAT_MODEL,
        "image_to_text_model": MUSEMIND_GEMINI_IMAGE_MODEL,
        "dimension": EMBEDDING_DIMENSION,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe the exact MuseMind Gemini generation-v2 contract.")
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--result-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run_probe(read_secret_file(args.api_key_file))
        exit_code = 0
    except ReconciliationConflict as exc:
        result = {"schema_version": SCHEMA_VERSION, "outcome": "FAILED", "reason_code": exc.reason_code}
        exit_code = 2
    except Exception:  # noqa: BLE001 - probe output must collapse all provider failures to one content-free code
        result = {"schema_version": SCHEMA_VERSION, "outcome": "FAILED", "reason_code": "GEMINI_PROBE_FAILED"}
        exit_code = 2
    serialized = json.dumps(result, sort_keys=True)
    if args.result_file:
        Path(args.result_file).write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
