"""Unit tests for authentication dependencies."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import kohakuhub.auth.dependencies as auth_deps


class _Expr:
    def __and__(self, other):
        return self


class _Field:
    def __eq__(self, other):
        return _Expr()

    def __gt__(self, other):
        return _Expr()


class _UpdateQuery:
    def where(self, *args, **kwargs):
        return self

    def execute(self):
        return 1


class _FakeSessionModel:
    session_id = _Field()
    expires_at = _Field()
    result = None

    @classmethod
    def get_or_none(cls, expr):
        return cls.result


class _FakeTokenModel:
    token_hash = _Field()
    id = _Field()
    result = None
    update_calls = []

    @classmethod
    def get_or_none(cls, expr):
        return cls.result

    @classmethod
    def update(cls, **kwargs):
        cls.update_calls.append(kwargs)
        return _UpdateQuery()


@pytest.fixture(autouse=True)
def _patch_models(monkeypatch):
    _FakeSessionModel.result = None
    _FakeTokenModel.result = None
    _FakeTokenModel.update_calls = []
    monkeypatch.setattr(auth_deps, "Session", _FakeSessionModel)
    monkeypatch.setattr(auth_deps, "Token", _FakeTokenModel)
    monkeypatch.setattr(auth_deps, "hash_token", lambda token: f"hashed:{token}")


def test_get_current_user_rejects_inactive_session_and_token(monkeypatch):
    request = SimpleNamespace(state=SimpleNamespace())
    _FakeSessionModel.result = SimpleNamespace(
        user=SimpleNamespace(username="alice", is_active=False)
    )
    _FakeTokenModel.result = SimpleNamespace(
        id=9,
        user=SimpleNamespace(username="alice", is_active=False),
    )
    monkeypatch.setattr(
        auth_deps,
        "parse_auth_header",
        lambda authorization: ("plain-token", {"https://hf.local": "hf_token"}),
    )

    with pytest.raises(HTTPException) as exc:
        auth_deps.get_current_user(request, session_id="session-1", authorization="Bearer plain-token")

    assert exc.value.status_code == 401
    assert request.state.external_tokens == {"https://hf.local": "hf_token"}
    assert len(_FakeTokenModel.update_calls) == 1


def test_get_current_user_handles_missing_session_and_invalid_token(monkeypatch):
    request = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(
        auth_deps,
        "parse_auth_header",
        lambda authorization: ("plain-token", {}),
    )

    with pytest.raises(HTTPException) as exc:
        auth_deps.get_current_user(request, session_id="missing-session", authorization="Bearer plain-token")

    assert exc.value.status_code == 401


def test_get_current_user_accepts_active_token_user(monkeypatch):
    request = SimpleNamespace(state=SimpleNamespace())
    _FakeTokenModel.result = SimpleNamespace(
        id=10,
        user=SimpleNamespace(username="token-user", is_active=True),
    )
    monkeypatch.setattr(
        auth_deps,
        "parse_auth_header",
        lambda authorization: ("plain-token", {}),
    )

    user = auth_deps.get_current_user(request, session_id=None, authorization="Bearer plain-token")

    assert user.username == "token-user"


def test_get_optional_user_and_get_external_tokens_cover_fallback_state(monkeypatch):
    request = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(
        auth_deps,
        "get_current_user",
        lambda request, session_id=None, authorization=None: (_ for _ in ()).throw(
            HTTPException(status_code=401, detail="missing")
        ),
    )
    monkeypatch.setattr(
        auth_deps,
        "parse_auth_header",
        lambda authorization: (None, {"https://mirror.local": "mirror-token"}),
    )

    assert auth_deps.get_optional_user(request, authorization="Bearer ignored") is None
    assert request.state.external_tokens == {"https://mirror.local": "mirror-token"}
    assert auth_deps.get_external_tokens(SimpleNamespace(state=SimpleNamespace())) == {}


def test_get_current_user_or_admin_covers_admin_user_and_failure_paths(monkeypatch):
    monkeypatch.setattr(auth_deps.cfg.admin, "enabled", True)
    monkeypatch.setattr(auth_deps.cfg.admin, "secret_token", "expected-secret")

    admin_request = SimpleNamespace(state=SimpleNamespace())
    assert auth_deps.get_current_user_or_admin(admin_request, x_admin_token="expected-secret") == (None, True)
    assert admin_request.state.is_admin is True

    user = SimpleNamespace(username="alice")
    user_request = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(auth_deps, "get_current_user", lambda *args, **kwargs: user)
    assert auth_deps.get_current_user_or_admin(user_request, x_admin_token=None) == (user, False)
    assert user_request.state.is_admin is False

    monkeypatch.setattr(
        auth_deps,
        "get_current_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=401, detail="missing")),
    )
    with pytest.raises(HTTPException) as exc:
        auth_deps.get_current_user_or_admin(
            SimpleNamespace(state=SimpleNamespace()), x_admin_token=None
        )
    assert exc.value.status_code == 401
