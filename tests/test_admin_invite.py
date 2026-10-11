"""Verify scoped account mutations without contacting a real auth service."""

import json

import pytest

from create_admin_invite import create_invite


class FakeAPI:
    def __init__(self, role="SU", available=True):
        self.role, self.available, self.calls = role, available, []

    def login(self, credentials):
        self.calls.append(("login", credentials["email"]))
        return {"user_role": self.role}

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/tenants":
            return {"data": {"tenant_id": "tenant-1", "default_group_id": 7}}
        if path.endswith("/available"):
            return {"data": {"available": self.available}}
        if path == "/invitations":
            return {"data": {"invitation_code": "TEST01"}}
        raise AssertionError(f"Unexpected account operation: {path}")


def prepare_runtime(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "super_admin": {"email": "bootstrap@example.com", "password": "fixture-only"}
    }))


def test_creates_only_one_admin_invitation_and_preserves_benchmark_state(tmp_path):
    prepare_runtime(tmp_path)
    api, states = FakeAPI(), []
    assert create_invite(api, tmp_path, states.append) == "TEST01"
    assert states == [{"accounts": {}, "models": {}, "tenant_id": "tenant-1", "group_id": 7}]
    assert api.calls == [
        ("login", "bootstrap@example.com"),
        ("POST", "/tenants", {"tenant_name": "ICT空白工程验证", "skill_ids": [], "skill_names": [], "locale": "zh"}),
        ("POST", "/invitations", {"tenant_id": "tenant-1", "code_type": "ADMIN_INVITE", "group_ids": [7], "capacity": 1})
    ]
    assert json.loads((tmp_path / "manual-admin-invitation.json").read_text())["capacity"] == 1


def test_non_super_admin_cannot_create_invitation(tmp_path):
    prepare_runtime(tmp_path)
    api = FakeAPI(role="ADMIN")
    with pytest.raises(RuntimeError, match="SU role"):
        create_invite(api, tmp_path)
    assert api.calls == [("login", "bootstrap@example.com")]


@pytest.mark.parametrize("available", [True, False])
def test_reuses_available_invitation_without_creating_another_tenant(tmp_path, available):
    prepare_runtime(tmp_path)
    (tmp_path / "nexent-state.json").write_text(json.dumps({"tenant_id": "tenant-1", "group_id": 7}))
    (tmp_path / "manual-admin-invitation.json").write_text(json.dumps({
        "tenant_id": "tenant-1", "code_type": "ADMIN_INVITE", "invitation_code": "OLD001"
    }))
    api = FakeAPI(available=available)
    assert create_invite(api, tmp_path) == ("OLD001" if available else "TEST01")
    assert not any(call[0] == "POST" and call[1] == "/tenants" for call in api.calls)
    assert sum(call[0] == "POST" for call in api.calls) == (0 if available else 1)
