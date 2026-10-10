"""Actual-library compatibility at the auth and PDF boundaries, without services.

These checks do not replace image scanning or provider qualification.
"""

import importlib.util
import io
import json
import socket
import sys
from pathlib import Path
from types import ModuleType

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pypdf import PdfReader, PdfWriter

from tools.musemind_conformance.c05_parser_intake import validate_intake


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = []

    def unexpected_dns(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Unexpected DNS lookup in offline compatibility test")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)
    yield
    assert not attempts, "A caught networking error must not make a negative test pass"


@pytest.fixture
def claims():
    return {"sub": "synthetic-visitor", "aud": "test-client", "iss": "https://issuer.invalid", "exp": 4102444800}


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc_module(monkeypatch):
    # Load the real auth files without bootstrapping the application or settings.
    # Only the unrelated discovery/OAuth HTTP boundary is replaced.
    http = ModuleType("common.http_client")

    def unexpected_http(*args, **kwargs):
        raise AssertionError("This compatibility test must not contact a service")

    http.sync_request = unexpected_http
    http.async_request = unexpected_http
    monkeypatch.setitem(sys.modules, "common.http_client", http)
    auth_path = Path(__file__).resolve().parents[3] / "api" / "apps" / "auth"
    package_name = "_musemind_security_dependency_auth"
    package = ModuleType(package_name)
    package.__path__ = [str(auth_path)]
    monkeypatch.setitem(sys.modules, package_name, package)
    for name in ("oauth", "oidc"):
        full_name = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(full_name, auth_path / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, full_name, module)
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def oidc_client(monkeypatch, oidc_module, rsa_key):
    issuer = "https://issuer.invalid"
    metadata = {
        "issuer": issuer,
        "jwks_uri": f"{issuer}/jwks",
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "userinfo_endpoint": f"{issuer}/userinfo",
        "id_token_signing_alg_values_supported": ["HS256", "RS256", "none"],
    }
    monkeypatch.setattr(oidc_module.OIDCClient, "_load_oidc_metadata", staticmethod(lambda _: metadata))
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key(), as_dict=True)
    public_jwk.update(kid="synthetic-key", use="sig", alg="RS256")

    def jwks_response(opener, request, *args, **kwargs):
        assert request.full_url == metadata["jwks_uri"]
        return io.BytesIO(json.dumps({"keys": [public_jwk]}).encode())

    # Keep PyJWKClient's fetch/parse/key selection real; intercept only its I/O.
    monkeypatch.setattr(jwt.jwks_client.urllib.request.OpenerDirector, "open", jwks_response)
    return oidc_module.OIDCClient(
        {
            "issuer": issuer,
            "client_id": "test-client",
            "client_secret": "synthetic-secret",
            "redirect_uri": "https://callback.invalid",
        }
    )


def test_hs256_round_trip_and_wrong_secret_rejected(claims):
    secret = "synthetic-test-secret-at-least-32-bytes"
    token = jwt.encode(claims, secret, algorithm="HS256")
    assert jwt.decode(token, secret, algorithms=["HS256"], audience=claims["aud"], issuer=claims["iss"]) == claims
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "another-synthetic-secret-at-least-32-bytes", algorithms=["HS256"], audience=claims["aud"])


def test_oidc_real_jwks_round_trip_retains_asymmetric_allowlist(oidc_client, rsa_key, claims):
    token = jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": "synthetic-key"})
    assert oidc_client.id_token_signing_algs == ["RS256"]
    assert oidc_client.parse_id_token(token) == claims


@pytest.mark.parametrize(
    ("changed_claim", "error_type"),
    [
        ({"aud": "other-client"}, jwt.InvalidAudienceError),
        ({"iss": "https://other.invalid"}, jwt.InvalidIssuerError),
        ({"exp": 1}, jwt.ExpiredSignatureError),
        ({"exp": "invalid-date"}, jwt.DecodeError),
    ],
    ids=["audience", "issuer", "expired", "invalid-expiry"],
)
def test_oidc_invalid_claims_fail_closed(oidc_client, rsa_key, claims, changed_claim, error_type):
    token = jwt.encode({**claims, **changed_claim}, rsa_key, algorithm="RS256", headers={"kid": "synthetic-key"})
    with pytest.raises(ValueError, match="Error parsing ID Token") as failure:
        oidc_client.parse_id_token(token)
    assert type(failure.value.__context__) is error_type


def test_oidc_token_cannot_select_symmetric_algorithm(oidc_client, claims):
    token = jwt.encode(claims, "synthetic-test-secret-at-least-32-bytes", algorithm="HS256", headers={"kid": "synthetic-key"})
    with pytest.raises(ValueError, match="Error parsing ID Token") as failure:
        oidc_client.parse_id_token(token)
    assert type(failure.value.__context__) is jwt.InvalidAlgorithmError


def test_oidc_unknown_key_fails_closed(oidc_client, rsa_key, claims):
    token = jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": "unknown-key"})
    with pytest.raises(ValueError, match="Error parsing ID Token") as failure:
        oidc_client.parse_id_token(token)
    assert type(failure.value.__context__) is jwt.PyJWKClientError


def _ordinary_pdf():
    # Independent text fixture: reader correctness must not depend on PdfWriter
    # generating exactly the representation the same library expects to read.
    content = b"BT /F1 12 Tf 36 750 Td (Schloss Burgdorf caf\xe9) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(payload)


def test_pdf_normal_intake_and_text_extraction():
    payload = _ordinary_pdf()
    assert validate_intake(payload, "application/pdf", 5) == "VALID"
    reader = PdfReader(io.BytesIO(payload), strict=True)
    assert len(reader.pages) == 1
    assert reader.pages[0].extract_text().strip() == "Schloss Burgdorf café"


def test_pdf_encrypted_intake_remains_rejected():
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(_ordinary_pdf())))
    writer.encrypt("synthetic-password")
    stream = io.BytesIO()
    writer.write(stream)
    payload = stream.getvalue()
    assert PdfReader(io.BytesIO(payload)).is_encrypted
    assert validate_intake(payload, "application/pdf", 5) == "PDF_ENCRYPTED"


@pytest.mark.parametrize("payload", [b"%PDF-1.4\ncorrupt\n%%EOF\n", _ordinary_pdf()[:100]], ids=["corrupt", "truncated"])
def test_pdf_corrupt_intake_remains_rejected(payload):
    assert validate_intake(payload, "application/pdf", 5) == "PDF_INVALID"
