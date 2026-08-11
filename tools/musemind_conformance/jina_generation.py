"""Content-free live probe for the MuseMind Jina v3 embedding generation.

The fixed strings are synthetic and carry no museum or visitor content. The
probe exercises both retrieval adapters through the same client used by
RAGFlow, verifies its dimension/normalization guards, and emits only vector
fingerprints and aggregate similarities. It never logs the API key or text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable

import numpy as np

from api.db.musemind_provider_identity import ReconciliationConflict, read_secret_file
from rag.llm.embedding_model import JinaMultiVecEmbed

SCHEMA_VERSION = "musemind.jina-generation-probe/v1"
MODEL = "jina-embeddings-v3"
ENDPOINT = "https://api.jina.ai/v1/embeddings"
DIMENSION = 1024

_PASSAGES = (
    "La statua in marmo è esposta nella sala dedicata alla scultura rinascimentale.",
    "The marble statue is displayed in the gallery devoted to Renaissance sculpture.",
    "Il laboratorio botanico studia la crescita delle piante alpine.",
)
_QUERY = "Dove si trova la statua di marmo?"


def _fingerprint(vector: np.ndarray) -> str:
    canonical = np.asarray(vector, dtype="<f4")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


def run_probe(
    api_key: str,
    client_factory: Callable[..., JinaMultiVecEmbed] = JinaMultiVecEmbed,
) -> dict[str, object]:
    client = client_factory(api_key, MODEL, base_url=ENDPOINT)
    passage_vectors, passage_tokens = client.encode(list(_PASSAGES))
    query_vector, query_tokens = client.encode_queries(_QUERY)
    similarities = passage_vectors @ query_vector

    return {
        "schema_version": SCHEMA_VERSION,
        "outcome": "PASSED",
        "endpoint_hostname": "api.jina.ai",
        "model": MODEL,
        "dimension": DIMENSION,
        "probe_vector_count": 4,
        "reported_tokens": int(passage_tokens + query_tokens),
        "vector_fingerprints_sha256": [
            *[_fingerprint(vector) for vector in passage_vectors],
            _fingerprint(query_vector),
        ],
        "query_passage_cosine": [round(float(value), 8) for value in similarities],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe the exact MuseMind Jina v3 request contract.")
    parser.add_argument("--api-key-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        api_key = read_secret_file(args.api_key_file)
        result = run_probe(api_key)
        exit_code = 0
    except ReconciliationConflict as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "outcome": "FAILED",
            "reason_code": exc.reason_code,
        }
        exit_code = 2
    except Exception:  # noqa: BLE001 - the CLI boundary must remain content-free
        result = {
            "schema_version": SCHEMA_VERSION,
            "outcome": "FAILED",
            "reason_code": "JINA_PROBE_FAILED",
        }
        exit_code = 2
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
