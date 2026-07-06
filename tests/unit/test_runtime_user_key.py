from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from latticeai.runtime.user_key_runtime import build_user_key_runtime


class Logger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args):
        self.warnings.append(message % args if args else message)


class Keyring:
    def __init__(self, *, fail_write=False, fail_read=False):
        self.fail_write = fail_write
        self.fail_read = fail_read
        self.values = {}

    def get_password(self, service, key):
        if self.fail_read:
            raise RuntimeError("read failed")
        return self.values.get((service, key))

    def set_password(self, service, key, value):
        if self.fail_write:
            raise RuntimeError("write failed")
        self.values[(service, key)] = value


def build_runtime(*, users=None, keyring=None, allow_plaintext=False):
    state = SimpleNamespace(users=dict(users or {}), saved=[])

    def load_users():
        return state.users

    def save_users(users):
        state.users = users
        state.saved.append(users.copy())

    def ensure_identity(email, user):
        user["identity_uuid"] = user.get("identity_uuid") or f"id:{email}"

    runtime = build_user_key_runtime(
        load_users=load_users,
        save_users=save_users,
        ensure_user_identity=ensure_identity,
        keyring=keyring,
        allow_plaintext_api_keys=allow_plaintext,
        logging=Logger(),
        http_exception=HTTPException,
    )
    return runtime, state


def test_get_history_user_prefers_nickname_then_name_then_email():
    runtime, _state = build_runtime(
        users={
            "a@example.com": {"nickname": "Alice", "name": "Fallback"},
            "b@example.com": {"name": "Bob"},
        }
    )

    assert runtime["get_history_user"]("a@example.com") == {
        "user_email": "a@example.com",
        "user_nickname": "Alice",
    }
    assert runtime["get_history_user"]("b@example.com")["user_nickname"] == "Bob"
    assert runtime["get_history_user"]("c@example.com")["user_nickname"] == "c@example.com"
    assert runtime["get_history_user"](None, "Guest") == {
        "user_email": None,
        "user_nickname": "Guest",
    }


def test_api_key_read_prefers_keyring_over_plaintext_store():
    keyring = Keyring()
    keyring.values[("LatticeAI", "a@example.com:openai")] = " keyring-key "
    runtime, _state = build_runtime(
        users={"a@example.com": {"api_keys": {"openai": "plaintext-key"}}},
        keyring=keyring,
        allow_plaintext=True,
    )

    assert runtime["get_user_api_key"]("a@example.com", "openai") == "keyring-key"


def test_plaintext_api_key_read_requires_explicit_policy():
    users = {"a@example.com": {"api_keys": {"openai": " plaintext-key "}}}
    denied_runtime, _ = build_runtime(users=users, allow_plaintext=False)
    allowed_runtime, _ = build_runtime(users=users, allow_plaintext=True)

    assert denied_runtime["get_user_api_key"]("a@example.com", "openai") is None
    assert allowed_runtime["get_user_api_key"]("a@example.com", "openai") == "plaintext-key"


def test_keyring_write_removes_legacy_plaintext_copy():
    keyring = Keyring()
    runtime, state = build_runtime(
        users={"a@example.com": {"api_keys": {"openai": "old"}}},
        keyring=keyring,
        allow_plaintext=False,
    )

    runtime["set_user_api_key"]("a@example.com", "openai", "new")

    assert keyring.values[("LatticeAI", "a@example.com:openai")] == "new"
    assert "api_keys" not in state.users["a@example.com"]
    assert state.saved


def test_plaintext_write_creates_identity_when_policy_allows_fallback():
    runtime, state = build_runtime(keyring=None, allow_plaintext=True)

    runtime["set_user_api_key"]("new@example.com", "openai", "secret")

    user = state.users["new@example.com"]
    assert user["api_keys"]["openai"] == "secret"
    assert user["identity_uuid"] == "id:new@example.com"


def test_plaintext_write_is_blocked_without_policy():
    runtime, _state = build_runtime(keyring=None, allow_plaintext=False)

    with pytest.raises(HTTPException) as exc:
        runtime["set_user_api_key"]("a@example.com", "openai", "secret")

    assert exc.value.status_code == 500
