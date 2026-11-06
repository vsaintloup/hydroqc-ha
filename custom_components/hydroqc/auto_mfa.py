"""Opt-in, bounded MFA renewal using the existing HydroQC client session."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .auth import (
    LOGIN_MFA_REQUIRED,
    LOGIN_SUCCESS,
    async_login,
    async_request_mfa_code,
    async_submit_mfa_code,
    login_outcome,
    mfa_error_kind,
)
from .const import (
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_TRASH,
    CONF_GMAIL_USERNAME,
    DOMAIN,
)
from .gmail import GmailCode, GmailError, GmailInbox

_LOGGER = logging.getLogger(__name__)
_RUNTIME_KEY = f"{DOMAIN}_auto_mfa"
POLL_SECONDS = 5
WAIT_SECONDS = 120
COOLDOWN_SECONDS = 900
_SENSITIVE_REQUEST: ContextVar[bool] = ContextVar("hydroqc_sensitive_request", default=False)


class _SensitiveRequestFilter(logging.Filter):
    """Keep the wrapper from logging POST payloads or echoed OTPs in this task."""

    def filter(self, _record: logging.LogRecord) -> bool:
        return not _SENSITIVE_REQUEST.get()


_wrapper_logger = logging.getLogger("hydroqc.httpclient")
if not any(isinstance(item, _SensitiveRequestFilter) for item in _wrapper_logger.filters):
    _wrapper_logger.addFilter(_SensitiveRequestFilter())


async def _private_request(operation: Awaitable[Any]) -> Any:
    token = _SENSITIVE_REQUEST.set(True)
    try:
        return await operation
    finally:
        _SENSITIVE_REQUEST.reset(token)


@dataclass
class _Account:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    manual_flows: set[str] = field(default_factory=set)


def _account(hass: HomeAssistant, username: str) -> _Account:
    accounts: dict[str, _Account] = hass.data.setdefault(_RUNTIME_KEY, {})
    key = hashlib.sha256(username.strip().casefold().encode()).hexdigest()
    if key not in accounts:
        accounts[key] = _Account()
    return accounts[key]


async def async_begin_manual(hass: HomeAssistant, username: str, flow_id: str) -> None:
    """Give manual authentication priority over all automatic requests."""
    account = _account(hass, username)
    account.manual_flows.add(flow_id)
    tasks = list(account.tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def end_manual(hass: HomeAssistant, username: str, flow_id: str) -> None:
    """Release a completed or abandoned manual flow."""
    _account(hass, username).manual_flows.discard(flow_id)


class AutomaticMfa:
    """Manage one entry's renewal without exposing OTPs to HA's event bus."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.account = _account(hass, entry.data["username"])
        account_id = hashlib.sha256(entry.data["username"].strip().casefold().encode()).hexdigest()
        self.store: Store[dict[str, float]] = Store(hass, 1, f"{DOMAIN}.mfa.{account_id}")
        self._task: asyncio.Task[None] | None = None

    async def async_renew(
        self, webuser: Any, persist_session: Callable[[], Awaitable[bool]]
    ) -> None:
        """Share concurrent requests for this entry and serialize account access."""
        if self._task is None:
            self._task = self.hass.async_create_task(
                self._async_renew(webuser, persist_session), "HydroQC Gmail MFA"
            )
            self.account.tasks.add(self._task)
        task = self._task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            if task.done():
                self.account.tasks.discard(task)
                if self._task is task:
                    self._task = None

    async def async_cancel(self) -> None:
        """Cancel pending renewal before unloading or closing the Hydro client."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _async_io(self, operation: Callable[[], Any]) -> Any:
        """Finish an in-flight bounded IMAP call before closing its connection."""
        future = self.hass.async_add_executor_job(operation)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Cancelling a Future cannot stop its worker thread. Await it before
            # logout to avoid races, then propagate cancellation to the caller.
            await asyncio.gather(future, return_exceptions=True)
            raise

    async def _async_renew(
        self, webuser: Any, persist_session: Callable[[], Awaitable[bool]]
    ) -> None:
        async with self.account.lock:
            if self.account.manual_flows:
                raise GmailError("mfa_manual_active")
            try:
                state = await self.store.async_load()
                if state and state.get("retry_after", 0) > time.time():
                    raise GmailError("mfa_cooldown")
                # Save before any request: a crash/reload must not send again.
                await self.store.async_save({"retry_after": time.time() + COOLDOWN_SECONDS})
            except GmailError:
                raise
            except Exception:
                raise GmailError("mfa_storage_unavailable") from None

            username = self.entry.options.get(CONF_GMAIL_USERNAME, "")
            password = self.entry.options.get(CONF_GMAIL_APP_PASSWORD, "")
            if not username or not password:
                raise GmailError("gmail_not_configured")
            inbox = GmailInbox(username, password)
            try:
                await self._async_io(inbox.connect)
                receipt = await self._async_challenge(inbox, webuser)
                if not await persist_session():
                    raise GmailError("mfa_session_not_saved")
                try:
                    await self.store.async_save({"retry_after": 0})
                except Exception:
                    _LOGGER.warning(
                        "HydroQC session renewed; the automatic verification cooldown "
                        "could not be reset"
                    )
                if receipt is not None and self.entry.options.get(CONF_GMAIL_TRASH, True):
                    try:
                        await self._async_io(lambda: inbox.trash(receipt))
                    except GmailError:
                        _LOGGER.warning(
                            "HydroQC session renewed; the used verification email could not "
                            "be moved to Trash"
                        )
                _LOGGER.info("HydroQC portal session renewed automatically through Gmail")
            except asyncio.CancelledError:
                raise
            except GmailError:
                raise
            except Exception:
                # Never propagate server responses, OTPs, credentials or HTML.
                raise GmailError("mfa_automatic_failed") from None
            finally:
                await self._async_io(inbox.close)

    async def _async_challenge(self, inbox: GmailInbox, webuser: Any) -> GmailCode | None:
        for attempt in range(2):
            await self._async_io(inbox.checkpoint)
            if not await _private_request(async_request_mfa_code(webuser)):
                raise GmailError("mfa_request_failed")
            deadline = asyncio.get_running_loop().time() + WAIT_SECONDS
            receipt: GmailCode | None = None
            while asyncio.get_running_loop().time() < deadline:
                receipt = await self._async_io(inbox.poll)
                if receipt is not None:
                    break
                await asyncio.sleep(POLL_SECONDS)
            if receipt is None:
                raise GmailError("gmail_timeout")
            try:
                status = await _private_request(async_submit_mfa_code(webuser, receipt.code))
            except Exception as err:
                if mfa_error_kind(err) == "expired" and attempt == 0:
                    # The wrapper clears an expired challenge. Resending alone
                    # cannot work: restart login without sending, on this client.
                    restarted = login_outcome(
                        await _private_request(async_login(webuser, send_mfa_code=False))
                    )
                    if restarted == LOGIN_SUCCESS:
                        return None
                    if restarted != LOGIN_MFA_REQUIRED:
                        raise GmailError("mfa_restart_failed") from None
                    continue
                raise GmailError("mfa_code_rejected") from None
            if login_outcome(status) != LOGIN_SUCCESS:
                raise GmailError("mfa_code_rejected")
            return receipt
        raise GmailError("mfa_code_expired")
