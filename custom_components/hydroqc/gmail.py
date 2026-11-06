"""Bounded, synchronous Gmail IMAP operations; call only from HA's executor."""

from __future__ import annotations

import imaplib
import re
import ssl
import time
import unicodedata
from contextlib import suppress
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser

SENDER = "hydro-quebec@communications.hydro.qc.ca"
SUBJECT = "Votre code de vérification pour votre identifiant Hydro-Québec"
NETWORK_TIMEOUT = 10
MAX_MESSAGE_BYTES = 262144
MAX_CANDIDATES = 50


class GmailError(Exception):
    """Expose a fixed reason, never an IMAP response or message content."""


@dataclass(frozen=True)
class GmailCode:
    """An in-memory receipt; secrets must not appear in its representation."""

    uid: int
    validity: bytes
    message_id: bytes
    code: str = field(repr=False)


class _VisibleText(HTMLParser):
    """Extract text without attributes, scripts or styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "head"):
            self._hidden += 1
        elif tag in ("br", "p", "div", "td"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head"):
            self._hidden = max(0, self._hidden - 1)
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).replace("\u2019", "'").casefold().split())


def extract_code(raw: bytes) -> str | None:
    """Accept only the expected sender/subject and one distinct six-digit code."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        assert isinstance(message, EmailMessage)
        if len(message.get_all("From", [])) != 1 or len(message.get_all("Subject", [])) != 1:
            return None
        addresses = getaddresses([str(message["From"])])
        if len(addresses) != 1 or addresses[0][1].casefold() != SENDER:
            return None
        if _normalize(str(message["Subject"])) != _normalize(SUBJECT):
            return None
        codes: set[str] = set()
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() not in ("text/plain", "text/html"):
                continue
            text = part.get_content()
            if not isinstance(text, str):
                continue
            if part.get_content_type() == "text/html":
                parser = _VisibleText()
                parser.feed(text)
                text = "".join(parser.parts)
            codes.update(re.findall(r"(?<!\w)[0-9]{6}(?!\w)", text))
    except (ValueError, LookupError, UnicodeError, AssertionError):
        raise GmailError("gmail_invalid_message") from None
    if len(codes) > 1:
        raise GmailError("gmail_ambiguous")
    return next(iter(codes), None)


class GmailInbox:
    """One short-lived IMAP connection, used serially during one MFA challenge."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._client: imaplib.IMAP4_SSL | None = None
        self._validity = b""
        self._minimum_uid = 0
        self._started_at = 0.0
        self._examined: set[int] = set()

    def connect(self) -> None:
        """Authenticate with an app password over verified TLS."""
        try:
            self._client = imaplib.IMAP4_SSL(
                "imap.gmail.com",
                993,
                ssl_context=ssl.create_default_context(),
                timeout=NETWORK_TIMEOUT,
            )
            status, _ = self._client.login(self._username, self._password)
            if status != "OK":
                raise GmailError("gmail_auth_failed")
        except imaplib.IMAP4.error:
            self.close()
            raise GmailError("gmail_auth_failed") from None
        except (OSError, TimeoutError):
            self.close()
            raise GmailError("gmail_unavailable") from None

    def checkpoint(self) -> None:
        """Capture UIDVALIDITY/UIDNEXT before requesting a new Hydro code."""
        client = self._connection()
        try:
            self._check(client.select("INBOX", readonly=True)[0])
            validity = client.response("UIDVALIDITY")[1]
            next_uid = client.response("UIDNEXT")[1]
            if not validity or not validity[0] or not next_uid or not next_uid[0]:
                raise GmailError("gmail_checkpoint_failed")
            self._validity = validity[0]
            self._minimum_uid = int(next_uid[0])
            self._started_at = time.time()
            self._examined.clear()
        except (imaplib.IMAP4.error, OSError, ValueError, TypeError):
            raise GmailError("gmail_unavailable") from None

    def poll(self) -> GmailCode | None:
        """Read only new candidate messages, including ones already marked seen."""
        client = self._connection()
        try:
            self._check(client.noop()[0])
            status, data = client.uid(
                "SEARCH",
                "UID",
                f"{self._minimum_uid}:*",
                "FROM",
                f'"{SENDER}"',
            )
            self._check(status)
            uids = [
                int(value)
                for value in (data[0] or b"").split()
                if int(value) >= self._minimum_uid and int(value) not in self._examined
            ]
            if len(uids) > MAX_CANDIDATES:
                raise GmailError("gmail_too_many_messages")
            matches: list[GmailCode] = []
            for uid in uids:
                self._examined.add(uid)
                status, fetched = client.uid(
                    "FETCH",
                    str(uid),
                    f"(INTERNALDATE X-GM-MSGID BODY.PEEK[]<0.{MAX_MESSAGE_BYTES + 1}>)",
                )
                self._check(status)
                for item in fetched:
                    if not isinstance(item, tuple):
                        continue
                    metadata, raw = item
                    if len(raw) > MAX_MESSAGE_BYTES:
                        raise GmailError("gmail_message_too_large")
                    received = imaplib.Internaldate2tuple(metadata)
                    message_id = re.search(rb"X-GM-MSGID ([0-9]+)", metadata)
                    if (
                        received is None
                        or time.mktime(received) < int(self._started_at)
                        or message_id is None
                    ):
                        continue
                    code = extract_code(raw)
                    if code:
                        matches.append(GmailCode(uid, self._validity, message_id[1], code))
            if len({match.code for match in matches}) > 1:
                raise GmailError("gmail_ambiguous")
            return matches[-1] if matches else None
        except (imaplib.IMAP4.error, OSError, ValueError, TypeError, IndexError):
            raise GmailError("gmail_unavailable") from None

    def trash(self, receipt: GmailCode) -> None:
        """Trash exactly the accepted Gmail message; never expunge a mailbox."""
        client = self._connection()
        try:
            self._check(client.select("INBOX", readonly=False)[0])
            validity = client.response("UIDVALIDITY")[1]
            if not validity or validity[0] != receipt.validity:
                raise GmailError("gmail_mailbox_changed")
            status, data = client.uid("FETCH", str(receipt.uid), "(X-GM-MSGID)")
            self._check(status)
            message_ids = [
                match[1]
                for item in data
                if isinstance(item, bytes)
                if (match := re.search(rb"X-GM-MSGID ([0-9]+)", item))
            ]
            if message_ids != [receipt.message_id]:
                raise GmailError("gmail_mailbox_changed")
            # Gmail system labels are language-independent. No DELETE/EXPUNGE,
            # no guessed localized trash folder and no other message is touched.
            status, _ = client.uid("STORE", str(receipt.uid), "+X-GM-LABELS", r"(\Trash)")
            self._check(status)
        except (imaplib.IMAP4.error, OSError, ValueError, TypeError):
            raise GmailError("gmail_cleanup_failed") from None

    def close(self) -> None:
        """Close without expunging; never include server responses in errors."""
        client, self._client = self._client, None
        if client is not None:
            with suppress(imaplib.IMAP4.error, OSError):
                client.logout()

    def _connection(self) -> imaplib.IMAP4_SSL:
        if self._client is None:
            raise GmailError("gmail_unavailable")
        return self._client

    @staticmethod
    def _check(status: str) -> None:
        if status != "OK":
            raise GmailError("gmail_unavailable")


def validate_gmail(username: str, password: str) -> None:
    """Test credentials and INBOX without reading messages or requesting a code."""
    inbox = GmailInbox(username, password)
    try:
        inbox.connect()
        inbox.checkpoint()
    finally:
        inbox.close()
