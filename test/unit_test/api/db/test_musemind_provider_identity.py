from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from api.db.musemind_provider_identity import (
    MembershipState,
    ProviderIdentitySpec,
    ProviderSnapshot,
    ReconciliationConflict,
    TenantState,
    TokenState,
    UserState,
    _build_parser,
    _provider_tenant_defaults,
    _spec_from_args,
    derive_principal_id,
    read_secret_file,
    reconcile_provider_identity,
)

INSTANCE_ID = "11111111-2222-4333-8444-555555555555"
CURRENT_TOKEN = "c" * 64
PREVIOUS_TOKEN = "p" * 64


class MemoryStore:
    def __init__(self):
        self.users: dict[str, UserState] = {}
        self.tenants: dict[str, TenantState] = {}
        self.memberships: dict[str, MembershipState] = {}
        self.tokens: set[tuple[str, str]] = set()
        self.lock = threading.Lock()

    @contextmanager
    def reconciliation_scope(self, _principal_id: str):
        with self.lock:
            yield

    def snapshot(self, spec: ProviderIdentitySpec) -> ProviderSnapshot:
        principal_id = spec.principal_id
        email_user = next((user for user in self.users.values() if user.email == spec.email), None)
        memberships = tuple(membership for membership in self.memberships.values() if membership.user_id == principal_id or membership.tenant_id == principal_id)
        tenant_tokens = tuple(TokenState(tenant_id=tenant_id, token=token) for tenant_id, token in sorted(self.tokens) if tenant_id == principal_id)
        desired_token_owners = tuple(TokenState(tenant_id=tenant_id, token=token) for tenant_id, token in sorted(self.tokens) if token in spec.inspected_tokens)
        return ProviderSnapshot(
            user=self.users.get(principal_id),
            email_user_id=email_user.id if email_user else None,
            tenant=self.tenants.get(principal_id),
            memberships=memberships,
            tenant_tokens=tenant_tokens,
            desired_token_owners=desired_token_owners,
        )

    def create_user(self, spec: ProviderIdentitySpec) -> None:
        self.users[spec.principal_id] = UserState(
            id=spec.principal_id,
            email=spec.email,
            nickname=spec.nickname,
            password=None,
            access_token=f"INVALID_{spec.principal_id}",
            is_authenticated="0",
            is_active="0",
            is_anonymous="0",
            login_channel="musemind_service_principal",
            status="1",
            is_superuser=False,
        )

    def repair_user_nickname(self, spec: ProviderIdentitySpec) -> None:
        self.users[spec.principal_id] = replace(self.users[spec.principal_id], nickname=spec.nickname)

    def create_tenant(self, spec: ProviderIdentitySpec) -> None:
        self.tenants[spec.principal_id] = TenantState(id=spec.principal_id, name=spec.nickname, status="1")

    def create_owner_membership(self, spec: ProviderIdentitySpec) -> None:
        self.memberships[spec.membership_id] = MembershipState(
            id=spec.membership_id,
            user_id=spec.principal_id,
            tenant_id=spec.principal_id,
            role="owner",
            invited_by=spec.principal_id,
            status="1",
        )

    def create_token(self, tenant_id: str, token: str) -> None:
        self.tokens.add((tenant_id, token))

    def delete_token(self, tenant_id: str, token: str) -> None:
        self.tokens.remove((tenant_id, token))


def make_spec(
    *,
    current: str = CURRENT_TOKEN,
    previous: str | None = None,
    operation: str = "reconcile",
) -> ProviderIdentitySpec:
    return ProviderIdentitySpec(
        rag_instance_id=INSTANCE_ID,
        environment="develop",
        current_token=current,
        previous_token=previous,
        operation=operation,
    )


def test_principal_id_is_stable_and_domain_scoped():
    first = derive_principal_id(INSTANCE_ID, "develop")
    assert first == derive_principal_id(INSTANCE_ID.upper(), "DEVELOP")
    assert len(first) == 32
    assert first != derive_principal_id(INSTANCE_ID, "production")


def test_provider_tenant_defaults_are_loaded_without_full_server_initialization():
    defaults = _provider_tenant_defaults(
        {
            "factory": "OpenAI",
            "default_models": {
                "chat_model": "gpt-test",
                "embedding_model": {
                    "name": "embedding-test",
                    "factory": "Local",
                },
            },
        }
    )

    assert defaults["llm_id"] == "gpt-test@OpenAI"
    assert defaults["embd_id"] == "embedding-test@Local"
    assert defaults["asr_id"] == ""
    assert defaults["parser_ids"]


@pytest.mark.parametrize(
    "settings",
    [None, {"parsers": None}, {"default_models": {"chat_model": 42}}],
)
def test_provider_tenant_defaults_fail_closed_on_malformed_config(settings):
    with pytest.raises(ReconciliationConflict, match="PROVIDER_DEFAULTS_INVALID"):
        _provider_tenant_defaults(settings)


def test_clean_create_then_second_run_is_unchanged():
    store = MemoryStore()
    spec = make_spec()

    created = reconcile_provider_identity(store, spec)
    unchanged = reconcile_provider_identity(store, spec)

    assert created.outcome == "CREATED"
    assert created.created_rows == 4
    assert unchanged.outcome == "UNCHANGED"
    assert len(store.users) == len(store.tenants) == 1
    assert len(store.memberships) == len(store.tokens) == 1


def test_compatible_partial_state_is_repaired():
    store = MemoryStore()
    spec = make_spec()
    reconcile_provider_identity(store, spec)
    store.memberships.clear()
    store.tokens.clear()

    repaired = reconcile_provider_identity(store, spec)
    unchanged = reconcile_provider_identity(store, spec)

    assert repaired.outcome == "REPAIRED"
    assert repaired.created_rows == 2
    assert unchanged.outcome == "UNCHANGED"


def test_compatible_nickname_drift_is_repaired():
    store = MemoryStore()
    spec = make_spec()
    reconcile_provider_identity(store, spec)
    store.users[spec.principal_id] = replace(store.users[spec.principal_id], nickname="stale-label")

    result = reconcile_provider_identity(store, spec)

    assert result.outcome == "REPAIRED"
    assert result.repaired_rows == 1
    assert store.users[spec.principal_id].nickname == spec.nickname


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        (
            lambda store, spec: store.users.__setitem__(
                spec.principal_id,
                replace(store.users[spec.principal_id], password="enabled"),
            ),
            "INTERACTIVE_LOGIN_CONFLICT",
        ),
        (
            lambda store, spec: store.users.__setitem__(
                spec.principal_id,
                replace(store.users[spec.principal_id], is_superuser=True),
            ),
            "SUPERUSER_CONFLICT",
        ),
        (
            lambda store, spec: store.tokens.add((spec.principal_id, "x" * 64)),
            "UNEXPECTED_TOKEN_CONFLICT",
        ),
        (
            lambda store, spec: store.memberships.__setitem__(
                "elevated-human",
                MembershipState(
                    id="elevated-human",
                    user_id="human-user",
                    tenant_id=spec.principal_id,
                    role="admin",
                    invited_by=spec.principal_id,
                    status="1",
                ),
            ),
            "DEBUG_MEMBERSHIP_CONFLICT",
        ),
    ],
)
def test_ambiguous_or_privileged_state_fails_closed(mutation, reason_code):
    store = MemoryStore()
    spec = make_spec()
    reconcile_provider_identity(store, spec)
    mutation(store, spec)

    with pytest.raises(ReconciliationConflict, match=reason_code):
        reconcile_provider_identity(store, spec)


def test_cross_tenant_desired_token_fails_closed():
    store = MemoryStore()
    spec = make_spec()
    store.tokens.add(("other-tenant", CURRENT_TOKEN))

    with pytest.raises(ReconciliationConflict, match="TOKEN_OWNERSHIP_CONFLICT"):
        reconcile_provider_identity(store, spec)


def test_normal_debug_membership_is_compatible_only_after_full_bootstrap():
    store = MemoryStore()
    spec = make_spec()
    reconcile_provider_identity(store, spec)
    store.memberships["debug-user"] = MembershipState(
        id="debug-user",
        user_id="human-user",
        tenant_id=spec.principal_id,
        role="normal",
        invited_by=spec.principal_id,
        status="1",
    )

    assert reconcile_provider_identity(store, spec).outcome == "UNCHANGED"


def test_rotation_add_smoke_revoke_and_rollback_contract():
    store = MemoryStore()
    reconcile_provider_identity(store, make_spec(current=PREVIOUS_TOKEN))

    add = reconcile_provider_identity(
        store,
        make_spec(current=CURRENT_TOKEN, previous=PREVIOUS_TOKEN),
    )
    revoke = reconcile_provider_identity(
        store,
        make_spec(
            current=CURRENT_TOKEN,
            previous=PREVIOUS_TOKEN,
            operation="revoke-previous",
        ),
    )
    repeated_revoke = reconcile_provider_identity(
        store,
        make_spec(
            current=CURRENT_TOKEN,
            previous=PREVIOUS_TOKEN,
            operation="revoke-previous",
        ),
    )
    rollback_add = reconcile_provider_identity(
        store,
        make_spec(current=PREVIOUS_TOKEN, previous=CURRENT_TOKEN),
    )
    rollback_revoke = reconcile_provider_identity(
        store,
        make_spec(
            current=PREVIOUS_TOKEN,
            previous=CURRENT_TOKEN,
            operation="revoke-previous",
        ),
    )

    assert add.outcome == "REPAIRED"
    assert revoke.outcome == "REPAIRED" and revoke.revoked_tokens == 1
    assert repeated_revoke.outcome == "UNCHANGED"
    assert rollback_add.outcome == "REPAIRED"
    assert rollback_revoke.outcome == "REPAIRED"
    assert store.tokens == {(make_spec().principal_id, PREVIOUS_TOKEN)}


def test_two_concurrent_runs_create_once_and_remain_exact():
    store = MemoryStore()
    spec = make_spec()
    outcomes: list[str] = []

    def invoke():
        outcomes.append(reconcile_provider_identity(store, spec).outcome)

    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["CREATED", "UNCHANGED"]
    assert len(store.users) == len(store.tenants) == 1
    assert len(store.memberships) == len(store.tokens) == 1


def test_content_free_result_does_not_expose_token_values():
    result = reconcile_provider_identity(MemoryStore(), make_spec())
    serialized = json.dumps(result.content_free_dict(), sort_keys=True)

    assert CURRENT_TOKEN not in serialized


def test_secret_file_accepts_one_transport_newline_and_rejects_multiline(tmp_path):
    secret_file = tmp_path / "current"
    secret_file.write_bytes(CURRENT_TOKEN.encode("ascii") + b"\n")
    assert read_secret_file(str(secret_file)) == CURRENT_TOKEN

    secret_file.write_bytes(CURRENT_TOKEN.encode("ascii") + b"\n" + PREVIOUS_TOKEN.encode("ascii"))
    with pytest.raises(ReconciliationConflict, match="SECRET_FILE_INVALID"):
        read_secret_file(str(secret_file))


def test_rotation_window_is_required_and_bounded(tmp_path):
    current_file = tmp_path / "current"
    previous_file = tmp_path / "previous"
    current_file.write_text(CURRENT_TOKEN, encoding="ascii")
    previous_file.write_text(PREVIOUS_TOKEN, encoding="ascii")
    parser = _build_parser()

    incomplete = parser.parse_args(
        [
            "reconcile",
            "--rag-instance-id",
            INSTANCE_ID,
            "--environment",
            "develop",
            "--current-token-file",
            str(current_file),
            "--previous-token-file",
            str(previous_file),
        ]
    )
    with pytest.raises(ReconciliationConflict, match="ROTATION_WINDOW_INCOMPLETE"):
        _spec_from_args(incomplete)

    deadline = datetime.now(UTC) + timedelta(minutes=10)
    bounded = parser.parse_args(
        [
            "reconcile",
            "--rag-instance-id",
            INSTANCE_ID,
            "--environment",
            "develop",
            "--current-token-file",
            str(current_file),
            "--previous-token-file",
            str(previous_file),
            "--previous-valid-until",
            deadline.isoformat(),
        ]
    )
    assert _spec_from_args(bounded).previous_token == PREVIOUS_TOKEN


def test_invalid_logical_identity_is_content_free_conflict():
    with pytest.raises(ReconciliationConflict, match="INVALID_RAG_INSTANCE_ID"):
        derive_principal_id("not-a-uuid", "develop")
