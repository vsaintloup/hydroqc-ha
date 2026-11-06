"""Verify mailbox boundaries, MIME parsing and targeted cleanup using fake mail."""

from __future__ import annotations

import imaplib
import time
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from custom_components.hydroqc.gmail import (
    SENDER,
    SUBJECT,
    GmailCode,
    GmailError,
    GmailInbox,
    extract_code,
    validate_gmail,
)


def mail(
    text: str = "Votre code de vérification est 123456.",
    sender: str = SENDER,
    subject: str = SUBJECT,
    html: bool = False,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(text, subtype="html" if html else "plain")
    return message.as_bytes()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (mail(), "123456"),
        (mail("Code 123456. Code 123456."), "123456"),
        (mail("<style>.x{color:#654321}</style><p>Code : <b>123456</b></p>", html=True), "123456"),
        (mail(sender=f"Hydro-Québec <{SENDER.upper()}>"), "123456"),
        (mail(subject=f"  {SUBJECT.upper()}  "), "123456"),
        (mail(sender="attacker@example.com"), None),
        (mail(subject="Votre facture Hydro-Québec"), None),
        (mail(text="1234567 ABC123456 123456ABC"), None),
        (mail(text="Aucun code dans ce message"), None),
    ],
)
def test_extract_expected_message(raw: bytes, expected: str | None) -> None:
    assert extract_code(raw) == expected


def test_multiple_different_codes_are_rejected() -> None:
    with pytest.raises(GmailError, match="gmail_ambiguous"):
        extract_code(mail("Code 123456 ou 654321"))


def test_multipart_repetition_is_not_ambiguous() -> None:
    message = EmailMessage()
    message["From"], message["Subject"] = SENDER, SUBJECT
    message.set_content("Votre code : 123456")
    message.add_alternative("<p>Votre code : <b>123456</b></p>", subtype="html")
    assert extract_code(message.as_bytes()) == "123456"


def test_attachment_is_not_treated_as_a_code() -> None:
    message = EmailMessage()
    message["From"], message["Subject"] = SENDER, SUBJECT
    message.set_content("Votre code : 123456")
    message.add_attachment(b"654321", maintype="text", subtype="plain", filename="ignored.txt")
    assert extract_code(message.as_bytes()) == "123456"


def inbox_with_client() -> tuple[GmailInbox, MagicMock]:
    inbox = GmailInbox("fake@gmail.com", "fake-app-password")
    client = MagicMock()
    client.select.return_value = ("OK", [b"2"])
    client.noop.return_value = ("OK", [])
    client.response.side_effect = lambda key: (key, [b"77" if key == "UIDVALIDITY" else b"10"])
    inbox._client = client
    inbox.checkpoint()
    return inbox, client


def metadata(received: float | None = None) -> bytes:
    date = time.strftime("%d-%b-%Y %H:%M:%S +0000", time.gmtime(received or time.time()))
    return f'1 (UID 10 X-GM-MSGID 200 INTERNALDATE "{date}" BODY[] {{200}}'.encode()


def test_poll_excludes_old_uids_and_uses_peek_without_unseen_filter() -> None:
    inbox, client = inbox_with_client()
    client.uid.side_effect = [("OK", [b"9 10"]), ("OK", [(metadata(), mail()), b")"])]
    receipt = inbox.poll()
    assert receipt is not None
    assert receipt.uid == 10 and receipt.code == "123456"
    calls = str(client.uid.call_args_list)
    assert "BODY.PEEK[]" in calls
    assert "UNSEEN" not in calls
    assert client.uid.call_args_list[1].args[1] == "10"


def test_poll_ignores_old_internal_date_even_with_new_uid() -> None:
    inbox, client = inbox_with_client()
    client.uid.side_effect = [
        ("OK", [b"10"]),
        ("OK", [(metadata(time.time() - 600), mail()), b")"]),
    ]
    assert inbox.poll() is None


def test_poll_rejects_different_new_codes() -> None:
    inbox, client = inbox_with_client()
    client.uid.side_effect = [
        ("OK", [b"10 11"]),
        ("OK", [(metadata(), mail()), b")"]),
        ("OK", [(metadata(), mail("Votre code : 654321")), b")"]),
    ]
    with pytest.raises(GmailError, match="gmail_ambiguous"):
        inbox.poll()


def test_cleanup_checks_identity_and_never_expunges() -> None:
    inbox, client = inbox_with_client()
    client.uid.side_effect = [
        ("OK", [b"1 (UID 10 X-GM-MSGID 200)"]),
        ("OK", []),
    ]
    inbox.trash(GmailCode(10, b"77", b"200", "123456"))
    assert client.uid.call_args.args == ("STORE", "10", "+X-GM-LABELS", r"(\Trash)")
    client.expunge.assert_not_called()
    assert "123456" not in repr(GmailCode(10, b"77", b"200", "123456"))


@pytest.mark.parametrize("wrong", [b"20", b"2000"])
def test_cleanup_rejects_another_message(wrong: bytes) -> None:
    inbox, client = inbox_with_client()
    client.uid.return_value = ("OK", [b"1 (UID 10 X-GM-MSGID " + wrong + b")"])
    with pytest.raises(GmailError, match="gmail_mailbox_changed"):
        inbox.trash(GmailCode(10, b"77", b"200", "123456"))
    assert client.uid.call_count == 1


def test_validation_only_authenticates_and_checkpoints() -> None:
    _, client = inbox_with_client()
    client.login.return_value = ("OK", [])
    with patch("custom_components.hydroqc.gmail.imaplib.IMAP4_SSL", return_value=client) as ctor:
        validate_gmail("fake@gmail.com", "fake-app-password")
    assert ctor.call_args.args == ("imap.gmail.com", 993)
    assert ctor.call_args.kwargs["ssl_context"].check_hostname is True
    client.uid.assert_not_called()
    client.logout.assert_called_once()


def test_auth_failure_does_not_expose_credentials_or_server_response() -> None:
    client = MagicMock()
    client.login.side_effect = imaplib.IMAP4.error("fake-app-password 123456")
    with (
        patch("custom_components.hydroqc.gmail.imaplib.IMAP4_SSL", return_value=client),
        pytest.raises(GmailError) as err,
    ):
        validate_gmail("fake@gmail.com", "fake-app-password")
    assert str(err.value) == "gmail_auth_failed"
    assert err.value.__suppress_context__
