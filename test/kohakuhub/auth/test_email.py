"""Tests for auth email helpers."""

from __future__ import annotations

from dataclasses import dataclass

import kohakuhub.auth.email as email_module


@dataclass
class DummyLogger:
    """Small logger spy used by email tests."""

    infos: list[str]
    exceptions: list[tuple[str, Exception]]
    successes: list[str] | None = None

    def info(self, message: str) -> None:
        self.infos.append(message)

    def success(self, message: str) -> None:
        if self.successes is not None:
            self.successes.append(message)

    def exception(self, message: str, exc: Exception) -> None:
        self.exceptions.append((message, exc))


class FakeSMTP:
    """SMTP stub capturing outgoing message details."""

    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in_with: tuple[str, str] | None = None
        self.messages = []
        self.fail_on_send = False
        type(self).instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in_with = (username, password)

    def send_message(self, message) -> None:
        if self.fail_on_send:
            raise RuntimeError("send failed")
        self.messages.append(message)


def _set_smtp_config(monkeypatch, *, enabled: bool, use_tls: bool = False) -> None:
    monkeypatch.setattr(email_module.cfg.app, "base_url", "https://hub.local")
    monkeypatch.setattr(email_module.cfg.smtp, "enabled", enabled)
    monkeypatch.setattr(email_module.cfg.smtp, "host", "smtp.local")
    monkeypatch.setattr(email_module.cfg.smtp, "port", 2525)
    monkeypatch.setattr(email_module.cfg.smtp, "use_tls", use_tls)
    monkeypatch.setattr(email_module.cfg.smtp, "username", "mailer")
    monkeypatch.setattr(email_module.cfg.smtp, "password", "secret")
    monkeypatch.setattr(email_module.cfg.smtp, "from_email", "noreply@hub.local")


def test_send_verification_email_returns_link_when_smtp_disabled(monkeypatch):
    logger = DummyLogger([], [])
    _set_smtp_config(monkeypatch, enabled=False)
    monkeypatch.setattr(email_module, "logger", logger)

    assert email_module.send_verification_email("user@example.com", "owner", "token-1") is True
    assert logger.exceptions == []
    assert logger.infos == [
        "SMTP disabled. Verification link: https://hub.local/api/auth/verify-email?token=token-1"
    ]


def test_send_verification_email_builds_multipart_message(monkeypatch):
    logger = DummyLogger([], [])
    FakeSMTP.instances = []
    _set_smtp_config(monkeypatch, enabled=True, use_tls=True)
    monkeypatch.setattr(email_module, "logger", logger)
    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSMTP)

    assert email_module.send_verification_email("user@example.com", "owner", "token-2") is True

    smtp = FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.local", 2525)
    assert smtp.started_tls is True
    assert smtp.logged_in_with == ("mailer", "secret")
    assert len(smtp.messages) == 1
    payloads = smtp.messages[0].get_payload()
    assert [part.get_content_subtype() for part in payloads] == ["plain", "html"]
    message_text = smtp.messages[0].as_string()
    assert "https://hub.local/api/auth/verify-email?token=token-2" in message_text
    assert "Verify your Kohaku Hub account" in message_text
    assert logger.exceptions == []


def test_send_verification_email_returns_false_on_failure(monkeypatch):
    logger = DummyLogger([], [])

    class FailingSMTP(FakeSMTP):
        def send_message(self, message) -> None:
            raise RuntimeError("send failed")

    _set_smtp_config(monkeypatch, enabled=True)
    monkeypatch.setattr(email_module, "logger", logger)
    monkeypatch.setattr(email_module.smtplib, "SMTP", FailingSMTP)

    assert email_module.send_verification_email("user@example.com", "owner", "token-3") is False
    assert logger.exceptions
    assert logger.exceptions[0][0] == "Failed to send verification email"


def test_send_org_invitation_email_supports_disabled_and_enabled_smtp(monkeypatch):
    disabled_logger = DummyLogger([], [])
    _set_smtp_config(monkeypatch, enabled=False)
    monkeypatch.setattr(email_module, "logger", disabled_logger)

    assert (
        email_module.send_org_invitation_email(
            "invitee@example.com",
            "acme-labs",
            "owner",
            "invite-token",
            "admin",
        )
        is True
    )
    assert disabled_logger.infos == [
        "SMTP disabled. Invitation link: https://hub.local/invite/invite-token"
    ]

    enabled_logger = DummyLogger([], [])
    FakeSMTP.instances = []
    _set_smtp_config(monkeypatch, enabled=True)
    monkeypatch.setattr(email_module, "logger", enabled_logger)
    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSMTP)

    assert (
        email_module.send_org_invitation_email(
            "invitee@example.com",
            "acme-labs",
            "owner",
            "invite-token",
            "admin",
        )
        is True
    )

    smtp = FakeSMTP.instances[-1]
    message_text = smtp.messages[0].as_string()
    assert "https://hub.local/invite/invite-token" in message_text
    assert "acme-labs" in message_text
    assert "owner" in message_text
    assert "admin" in message_text
