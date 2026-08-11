"""Qualified MuseMind RAGFlow provider service-principal reconciliation.

The command is intentionally schema-specific and content-free. It owns no
application state and must run as an infrastructure one-shot before proxy
readiness. Secret values are accepted only through mounted files and are never
included in results, logs, or exception messages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Protocol
from uuid import UUID

OUTCOME_CREATED = "CREATED"
OUTCOME_UNCHANGED = "UNCHANGED"
OUTCOME_REPAIRED = "REPAIRED"
OUTCOME_CONFLICT = "CONFLICT"

_ENVIRONMENT_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_PRINCIPAL_DOMAIN = b"musemind/ragflow-provider-principal/v1\0"
_MEMBERSHIP_DOMAIN = b"musemind/ragflow-provider-membership/v1\0"
_LOGIN_CHANNEL = "musemind_service_principal"
_MAX_SECRET_BYTES = 4096
_MIN_TOKEN_LENGTH = 32
_MAX_TOKEN_LENGTH = 255
_DEFAULT_MAX_DUAL_VALIDITY_SECONDS = 3600
_DEFAULT_PARSERS = "naive:General,qa:Q&A,resume:Resume,manual:Manual,table:Table,paper:Paper,book:Book,laws:Laws,presentation:Presentation,picture:Picture,one:One,audio:Audio,email:Email,tag:Tag"


class ReconciliationConflict(Exception):
    """Fail-closed, content-free provider identity conflict."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProviderIdentitySpec:
    rag_instance_id: str
    environment: str
    current_token: str = field(repr=False)
    previous_token: str | None = field(default=None, repr=False)
    operation: str = "reconcile"

    @property
    def principal_id(self) -> str:
        return derive_principal_id(self.rag_instance_id, self.environment)

    @property
    def email(self) -> str:
        return f"{self.principal_id}@service-principal.invalid"

    @property
    def nickname(self) -> str:
        return f"musemind-provider-{self.environment}"

    @property
    def membership_id(self) -> str:
        material = _MEMBERSHIP_DOMAIN + self.principal_id.encode("ascii")
        return hashlib.sha256(material).hexdigest()[:32]

    @property
    def desired_tokens(self) -> frozenset[str]:
        if self.operation == "reconcile" and self.previous_token:
            return frozenset((self.current_token, self.previous_token))
        return frozenset((self.current_token,))

    @property
    def inspected_tokens(self) -> frozenset[str]:
        tokens = {self.current_token}
        if self.previous_token:
            tokens.add(self.previous_token)
        return frozenset(tokens)


@dataclass(frozen=True)
class UserState:
    id: str
    email: str
    nickname: str
    password: str | None
    access_token: str | None
    is_authenticated: str
    is_active: str
    is_anonymous: str
    login_channel: str | None
    status: str | None
    is_superuser: bool | None


@dataclass(frozen=True)
class TenantState:
    id: str
    name: str | None
    status: str | None


@dataclass(frozen=True)
class MembershipState:
    id: str
    user_id: str
    tenant_id: str
    role: str
    invited_by: str
    status: str | None


@dataclass(frozen=True)
class TokenState:
    tenant_id: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class ProviderSnapshot:
    user: UserState | None
    email_user_id: str | None
    tenant: TenantState | None
    memberships: tuple[MembershipState, ...]
    tenant_tokens: tuple[TokenState, ...]
    desired_token_owners: tuple[TokenState, ...]


@dataclass(frozen=True)
class ReconciliationResult:
    outcome: str
    principal_id: str
    created_rows: int = 0
    repaired_rows: int = 0
    revoked_tokens: int = 0
    desired_token_count: int = 0
    reason_code: str | None = None

    def content_free_dict(self) -> dict[str, int | str]:
        result: dict[str, int | str] = {
            "outcome": self.outcome,
            "principal_id": self.principal_id,
            "created_rows": self.created_rows,
            "repaired_rows": self.repaired_rows,
            "revoked_tokens": self.revoked_tokens,
            "desired_token_count": self.desired_token_count,
        }
        if self.reason_code:
            result["reason_code"] = self.reason_code
        return result


class ProviderIdentityStore(Protocol):
    def reconciliation_scope(self, principal_id: str) -> AbstractContextManager[None]: ...

    def snapshot(self, spec: ProviderIdentitySpec) -> ProviderSnapshot: ...

    def create_user(self, spec: ProviderIdentitySpec) -> None: ...

    def repair_user_nickname(self, spec: ProviderIdentitySpec) -> None: ...

    def create_tenant(self, spec: ProviderIdentitySpec) -> None: ...

    def create_owner_membership(self, spec: ProviderIdentitySpec) -> None: ...

    def create_token(self, tenant_id: str, token: str) -> None: ...

    def delete_token(self, tenant_id: str, token: str) -> None: ...


def _normalize_inputs(rag_instance_id: str, environment: str) -> tuple[str, str]:
    try:
        normalized_instance = str(UUID(str(rag_instance_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReconciliationConflict("INVALID_RAG_INSTANCE_ID") from exc

    normalized_environment = str(environment).strip().lower()
    if not _ENVIRONMENT_RE.fullmatch(normalized_environment):
        raise ReconciliationConflict("INVALID_ENVIRONMENT")
    return normalized_instance, normalized_environment


def derive_principal_id(rag_instance_id: str, environment: str) -> str:
    normalized_instance, normalized_environment = _normalize_inputs(rag_instance_id, environment)
    material = _PRINCIPAL_DOMAIN + normalized_environment.encode("ascii") + b"\0" + normalized_instance.encode("ascii")
    return hashlib.sha256(material).hexdigest()[:32]


def _validate_token(token: str, reason_code: str) -> None:
    if not (_MIN_TOKEN_LENGTH <= len(token) <= _MAX_TOKEN_LENGTH):
        raise ReconciliationConflict(reason_code)
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in token):
        raise ReconciliationConflict(reason_code)


def validate_spec(spec: ProviderIdentitySpec) -> None:
    _normalize_inputs(spec.rag_instance_id, spec.environment)
    if spec.operation not in {"reconcile", "revoke-previous"}:
        raise ReconciliationConflict("INVALID_OPERATION")
    _validate_token(spec.current_token, "INVALID_CURRENT_TOKEN")
    if spec.previous_token is not None:
        _validate_token(spec.previous_token, "INVALID_PREVIOUS_TOKEN")
        if spec.previous_token == spec.current_token:
            raise ReconciliationConflict("DUPLICATE_TOKEN_INPUT")
    if spec.operation == "revoke-previous" and spec.previous_token is None:
        raise ReconciliationConflict("PREVIOUS_TOKEN_REQUIRED")


def _provider_tenant_defaults(llm_settings: object) -> dict[str, str]:
    if not isinstance(llm_settings, dict):
        raise ReconciliationConflict("PROVIDER_DEFAULTS_INVALID")
    factory = llm_settings.get("factory", "") or ""
    default_models = llm_settings.get("default_models", {}) or {}
    parsers = llm_settings.get("parsers", _DEFAULT_PARSERS)
    if not isinstance(factory, str) or not isinstance(default_models, dict):
        raise ReconciliationConflict("PROVIDER_DEFAULTS_INVALID")
    if not isinstance(parsers, str) or not parsers.strip():
        raise ReconciliationConflict("PROVIDER_DEFAULTS_INVALID")

    def model_name(key: str) -> str:
        entry = default_models.get(key, "")
        if isinstance(entry, str):
            name = entry.strip()
            model_factory = factory
        elif isinstance(entry, dict):
            raw_name = entry.get("name") or entry.get("model") or ""
            model_factory = entry.get("factory") or factory
            if not isinstance(raw_name, str) or not isinstance(model_factory, str):
                raise ReconciliationConflict("PROVIDER_DEFAULTS_INVALID")
            name = raw_name.strip()
        else:
            raise ReconciliationConflict("PROVIDER_DEFAULTS_INVALID")
        if name and "@" not in name and model_factory:
            return f"{name}@{model_factory}"
        return name

    return {
        "llm_id": model_name("chat_model"),
        "embd_id": model_name("embedding_model"),
        "asr_id": model_name("asr_model"),
        "parser_ids": parsers,
        "img2txt_id": model_name("image2text_model"),
        "rerank_id": model_name("rerank_model"),
    }


def _validate_snapshot(spec: ProviderIdentitySpec, snapshot: ProviderSnapshot) -> None:
    principal_id = spec.principal_id

    if snapshot.email_user_id is not None and snapshot.email_user_id != principal_id:
        raise ReconciliationConflict("EMAIL_OWNERSHIP_CONFLICT")

    if snapshot.user is not None:
        user = snapshot.user
        if user.email != spec.email:
            raise ReconciliationConflict("USER_IDENTITY_CONFLICT")
        if bool(user.is_superuser):
            raise ReconciliationConflict("SUPERUSER_CONFLICT")
        if user.password is not None:
            raise ReconciliationConflict("INTERACTIVE_LOGIN_CONFLICT")
        if user.access_token != f"INVALID_{principal_id}":
            raise ReconciliationConflict("SESSION_IDENTITY_CONFLICT")
        if user.is_authenticated != "0" or user.is_active != "0" or user.is_anonymous != "0" or user.login_channel != _LOGIN_CHANNEL or user.status != "1":
            raise ReconciliationConflict("USER_STATE_CONFLICT")

    if snapshot.tenant is not None and snapshot.tenant.status != "1":
        raise ReconciliationConflict("TENANT_STATE_CONFLICT")
    if snapshot.tenant is not None and snapshot.user is None and snapshot.tenant.name != spec.nickname:
        raise ReconciliationConflict("PARTIAL_TENANT_IDENTITY_CONFLICT")

    exact_memberships = []
    debug_memberships = []
    for membership in snapshot.memberships:
        if membership.user_id == principal_id and membership.tenant_id == principal_id:
            exact_memberships.append(membership)
        elif membership.user_id == principal_id:
            raise ReconciliationConflict("PRINCIPAL_OWNERSHIP_CONFLICT")
        elif membership.tenant_id == principal_id:
            debug_memberships.append(membership)

    if len(exact_memberships) > 1:
        raise ReconciliationConflict("DUPLICATE_OWNER_RELATION")
    if exact_memberships:
        membership = exact_memberships[0]
        if membership.id != spec.membership_id or membership.role != "owner" or membership.invited_by != principal_id or membership.status != "1":
            raise ReconciliationConflict("OWNER_RELATION_CONFLICT")

    for membership in debug_memberships:
        if membership.role != "normal" or membership.status != "1":
            raise ReconciliationConflict("DEBUG_MEMBERSHIP_CONFLICT")

    if debug_memberships and (snapshot.user is None or snapshot.tenant is None or not exact_memberships):
        raise ReconciliationConflict("PARTIAL_DEBUG_MEMBERSHIP_CONFLICT")

    for owner in snapshot.desired_token_owners:
        if owner.tenant_id != principal_id:
            raise ReconciliationConflict("TOKEN_OWNERSHIP_CONFLICT")

    tenant_token_values = {token.token for token in snapshot.tenant_tokens}
    if len(tenant_token_values) != len(snapshot.tenant_tokens):
        raise ReconciliationConflict("DUPLICATE_TOKEN_ROW_CONFLICT")

    if spec.operation == "reconcile":
        unexpected = tenant_token_values - spec.desired_tokens
    else:
        allowed = {spec.current_token, spec.previous_token}
        unexpected = tenant_token_values - allowed
        if spec.current_token not in tenant_token_values:
            raise ReconciliationConflict("CURRENT_TOKEN_MISSING")
    if unexpected:
        raise ReconciliationConflict("UNEXPECTED_TOKEN_CONFLICT")


def reconcile_provider_identity(
    store: ProviderIdentityStore,
    spec: ProviderIdentitySpec,
) -> ReconciliationResult:
    validate_spec(spec)
    principal_id = spec.principal_id
    created_rows = 0
    repaired_rows = 0
    revoked_tokens = 0

    with store.reconciliation_scope(principal_id):
        snapshot = store.snapshot(spec)
        _validate_snapshot(spec, snapshot)

        exact_membership = next(
            (membership for membership in snapshot.memberships if membership.user_id == principal_id and membership.tenant_id == principal_id),
            None,
        )
        existing_token_values = {token.token for token in snapshot.tenant_tokens}
        was_empty = snapshot.user is None and snapshot.tenant is None and exact_membership is None and not existing_token_values

        if snapshot.user is None:
            store.create_user(spec)
            created_rows += 1
        elif snapshot.user.nickname != spec.nickname:
            store.repair_user_nickname(spec)
            repaired_rows += 1

        if snapshot.tenant is None:
            store.create_tenant(spec)
            created_rows += 1

        if exact_membership is None:
            store.create_owner_membership(spec)
            created_rows += 1

        if spec.operation == "reconcile":
            for token in spec.desired_tokens - existing_token_values:
                store.create_token(principal_id, token)
                created_rows += 1
        else:
            previous_token = spec.previous_token
            if previous_token in existing_token_values:
                store.delete_token(principal_id, previous_token)
                revoked_tokens += 1

    changed_rows = created_rows + repaired_rows + revoked_tokens
    if was_empty and spec.operation == "reconcile":
        outcome = OUTCOME_CREATED
    elif changed_rows:
        outcome = OUTCOME_REPAIRED
    else:
        outcome = OUTCOME_UNCHANGED
    return ReconciliationResult(
        outcome=outcome,
        principal_id=principal_id,
        created_rows=created_rows,
        repaired_rows=repaired_rows,
        revoked_tokens=revoked_tokens,
        desired_token_count=len(spec.desired_tokens),
    )


class PeeweeProviderIdentityStore:
    """Exact pinned-schema implementation used by the one-shot command."""

    _REQUIRED_COLUMNS: ClassVar[dict[str, set[str]]] = {
        "user": {
            "id",
            "access_token",
            "nickname",
            "password",
            "email",
            "is_authenticated",
            "is_active",
            "is_anonymous",
            "login_channel",
            "status",
            "is_superuser",
        },
        "tenant": {
            "id",
            "name",
            "llm_id",
            "embd_id",
            "asr_id",
            "parser_ids",
            "img2txt_id",
            "rerank_id",
            "status",
        },
        "user_tenant": {
            "id",
            "user_id",
            "tenant_id",
            "role",
            "invited_by",
            "status",
        },
        "api_token": {"tenant_id", "token"},
    }

    def __init__(self, lock_timeout_seconds: int = 30):
        from api.db.db_models import DB, APIToken, Tenant, User, UserTenant
        from common import settings
        from common.time_utils import current_timestamp, datetime_format

        self.APIToken = APIToken
        self.DB = DB
        self.Tenant = Tenant
        self.User = User
        self.UserTenant = UserTenant
        self.settings = settings
        self.tenant_defaults = _provider_tenant_defaults(settings.get_base_config("user_default_llm", {}) or {})
        self.current_timestamp = current_timestamp
        self.datetime_format = datetime_format
        self.lock_timeout_seconds = lock_timeout_seconds

    def _timestamps(self) -> dict[str, int | str | None]:
        now = datetime.now(UTC)
        timestamp = self.current_timestamp()
        formatted = self.datetime_format(now)
        return {
            "create_time": timestamp,
            "create_date": formatted,
            "update_time": timestamp,
            "update_date": formatted,
        }

    def _assert_schema(self) -> None:
        for table_name, required_columns in self._REQUIRED_COLUMNS.items():
            try:
                actual_columns = {column.name for column in self.DB.get_columns(table_name)}
            except Exception as exc:
                raise ReconciliationConflict("SCHEMA_INSPECTION_FAILED") from exc
            if not required_columns.issubset(actual_columns):
                raise ReconciliationConflict("SCHEMA_MISMATCH")

    @contextmanager
    def reconciliation_scope(self, principal_id: str) -> Iterator[None]:
        lock_name = f"musemind-provider-{principal_id}"
        try:
            with (
                self.DB.connection_context(),
                self.DB.lock(
                    lock_name,
                    self.lock_timeout_seconds,
                    db=self.DB,
                ),
                self.DB.atomic(),
            ):
                self._assert_schema()
                yield
        except ReconciliationConflict:
            raise
        except Exception as exc:
            raise ReconciliationConflict("LOCK_OR_TRANSACTION_FAILURE") from exc

    def snapshot(self, spec: ProviderIdentitySpec) -> ProviderSnapshot:
        principal_id = spec.principal_id
        user_model = self.User
        tenant_model = self.Tenant
        membership_model = self.UserTenant
        token_model = self.APIToken

        user = user_model.get_or_none(user_model.id == principal_id)
        email_user = user_model.get_or_none(user_model.email == spec.email)
        tenant = tenant_model.get_or_none(tenant_model.id == principal_id)
        memberships = tuple(membership_model.select().where((membership_model.user_id == principal_id) | (membership_model.tenant_id == principal_id)))
        tenant_tokens = tuple(token_model.select().where(token_model.tenant_id == principal_id))
        desired_token_owners = tuple(token_model.select().where(token_model.token.in_(spec.inspected_tokens)))

        return ProviderSnapshot(
            user=UserState(**{field_name: getattr(user, field_name) for field_name in UserState.__dataclass_fields__}) if user else None,
            email_user_id=email_user.id if email_user else None,
            tenant=TenantState(
                id=tenant.id,
                name=tenant.name,
                status=tenant.status,
            )
            if tenant
            else None,
            memberships=tuple(
                MembershipState(
                    id=membership.id,
                    user_id=membership.user_id,
                    tenant_id=membership.tenant_id,
                    role=membership.role,
                    invited_by=membership.invited_by,
                    status=membership.status,
                )
                for membership in memberships
            ),
            tenant_tokens=tuple(TokenState(tenant_id=token.tenant_id, token=token.token) for token in tenant_tokens),
            desired_token_owners=tuple(TokenState(tenant_id=token.tenant_id, token=token.token) for token in desired_token_owners),
        )

    def create_user(self, spec: ProviderIdentitySpec) -> None:
        self.User.insert(
            id=spec.principal_id,
            access_token=f"INVALID_{spec.principal_id}",
            nickname=spec.nickname,
            password=None,
            email=spec.email,
            is_authenticated="0",
            is_active="0",
            is_anonymous="0",
            login_channel=_LOGIN_CHANNEL,
            status="1",
            is_superuser=False,
            **self._timestamps(),
        ).execute()

    def repair_user_nickname(self, spec: ProviderIdentitySpec) -> None:
        timestamps = self._timestamps()
        self.User.update(
            nickname=spec.nickname,
            update_time=timestamps["update_time"],
            update_date=timestamps["update_date"],
        ).where(self.User.id == spec.principal_id).execute()

    def create_tenant(self, spec: ProviderIdentitySpec) -> None:
        self.Tenant.insert(
            id=spec.principal_id,
            name=spec.nickname,
            **self.tenant_defaults,
            status="1",
            **self._timestamps(),
        ).execute()

    def create_owner_membership(self, spec: ProviderIdentitySpec) -> None:
        self.UserTenant.insert(
            id=spec.membership_id,
            user_id=spec.principal_id,
            tenant_id=spec.principal_id,
            role="owner",
            invited_by=spec.principal_id,
            status="1",
            **self._timestamps(),
        ).execute()

    def create_token(self, tenant_id: str, token: str) -> None:
        self.APIToken.insert(
            tenant_id=tenant_id,
            token=token,
            **self._timestamps(),
        ).execute()

    def delete_token(self, tenant_id: str, token: str) -> None:
        deleted = self.APIToken.delete().where((self.APIToken.tenant_id == tenant_id) & (self.APIToken.token == token)).execute()
        if deleted != 1:
            raise ReconciliationConflict("PREVIOUS_TOKEN_REVOKE_CONFLICT")


def read_secret_file(path: str) -> str:
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise ReconciliationConflict("SECRET_FILE_UNREADABLE") from exc
    if not payload or len(payload) > _MAX_SECRET_BYTES:
        raise ReconciliationConflict("SECRET_FILE_INVALID")
    if payload.endswith(b"\r\n"):
        payload = payload[:-2]
    elif payload.endswith(b"\n"):
        payload = payload[:-1]
    if b"\n" in payload or b"\r" in payload:
        raise ReconciliationConflict("SECRET_FILE_INVALID")
    try:
        token = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ReconciliationConflict("SECRET_FILE_INVALID") from exc
    _validate_token(token, "SECRET_FILE_INVALID")
    return token


def _parse_deadline(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReconciliationConflict("INVALID_ROTATION_DEADLINE") from exc
    if parsed.tzinfo is None:
        raise ReconciliationConflict("INVALID_ROTATION_DEADLINE")
    return parsed.astimezone(UTC)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile the MuseMind RAGFlow provider service principal.")
    parser.add_argument("operation", choices=("reconcile", "revoke-previous"))
    parser.add_argument("--rag-instance-id", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--current-token-file", required=True)
    parser.add_argument("--previous-token-file")
    parser.add_argument("--previous-valid-until")
    parser.add_argument(
        "--max-dual-validity-seconds",
        type=int,
        default=_DEFAULT_MAX_DUAL_VALIDITY_SECONDS,
    )
    parser.add_argument("--lock-timeout-seconds", type=int, default=30)
    return parser


def _spec_from_args(args: argparse.Namespace) -> ProviderIdentitySpec:
    current_token = read_secret_file(args.current_token_file)
    previous_token = read_secret_file(args.previous_token_file) if args.previous_token_file else None

    if args.operation == "reconcile":
        if bool(previous_token) != bool(args.previous_valid_until):
            raise ReconciliationConflict("ROTATION_WINDOW_INCOMPLETE")
        if previous_token:
            deadline = _parse_deadline(args.previous_valid_until)
            now = datetime.now(UTC)
            remaining = (deadline - now).total_seconds()
            if args.max_dual_validity_seconds <= 0 or remaining <= 0 or remaining > args.max_dual_validity_seconds:
                raise ReconciliationConflict("ROTATION_WINDOW_OUT_OF_BOUNDS")
    elif args.previous_valid_until:
        raise ReconciliationConflict("ROTATION_DEADLINE_NOT_ALLOWED")

    if args.lock_timeout_seconds <= 0 or args.lock_timeout_seconds > 300:
        raise ReconciliationConflict("LOCK_TIMEOUT_OUT_OF_BOUNDS")

    normalized_instance, normalized_environment = _normalize_inputs(args.rag_instance_id, args.environment)
    return ProviderIdentitySpec(
        rag_instance_id=normalized_instance,
        environment=normalized_environment,
        current_token=current_token,
        previous_token=previous_token,
        operation=args.operation,
    )


def _conflict_result(
    rag_instance_id: str | None,
    environment: str | None,
    reason_code: str,
) -> ReconciliationResult:
    try:
        principal_id = derive_principal_id(rag_instance_id or "", environment or "")
    except ReconciliationConflict:
        principal_id = "UNRESOLVED"
    return ReconciliationResult(
        outcome=OUTCOME_CONFLICT,
        principal_id=principal_id,
        reason_code=reason_code,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        spec = _spec_from_args(args)
        store = PeeweeProviderIdentityStore(args.lock_timeout_seconds)
        result = reconcile_provider_identity(store, spec)
        exit_code = 0
    except ReconciliationConflict as exc:
        result = _conflict_result(
            getattr(args, "rag_instance_id", None),
            getattr(args, "environment", None),
            exc.reason_code,
        )
        exit_code = 2
    # The CLI boundary must remain content-free even for unexpected ORM/import
    # failures, so it deliberately converts every unclassified exception.
    except Exception:  # noqa: BLE001
        result = _conflict_result(
            getattr(args, "rag_instance_id", None),
            getattr(args, "environment", None),
            "INTERNAL_FAILURE",
        )
        exit_code = 2
    print(json.dumps(result.content_free_dict(), sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
