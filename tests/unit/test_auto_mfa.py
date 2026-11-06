"""Exercise renewal, failure recovery, concurrency and credential privacy."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydroqc.auto_mfa import AutomaticMfa, async_begin_manual, end_manual
from custom_components.hydroqc.const import (
    CONF_AUTO_MFA,
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_TRASH,
    CONF_GMAIL_USERNAME,
)
from custom_components.hydroqc.gmail import GmailCode, GmailError


@pytest.fixture
def entry(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> MockConfigEntry:
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_AUTO_MFA: True,
            CONF_GMAIL_USERNAME: "fake@gmail.com",
            CONF_GMAIL_APP_PASSWORD: "fake-app-password",
            CONF_GMAIL_TRASH: True,
        },
    )
    return mock_config_entry


@pytest.fixture
def inbox() -> MagicMock:
    client = MagicMock()
    client.poll.return_value = GmailCode(10, b"77", b"200", "123456")
    return client


async def test_success_persists_before_cleanup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
) -> None:
    order = []
    inbox.trash.side_effect = lambda _: order.append("trash")

    async def persist() -> bool:
        order.append("persist")
        return True

    with patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox):
        await AutomaticMfa(hass, entry).async_renew(mock_webuser, persist)
    assert order == ["persist", "trash"]
    mock_webuser.request_mfa_code.assert_awaited_once()
    mock_webuser.submit_mfa_code.assert_awaited_once_with("123456")
    inbox.close.assert_called_once()


@pytest.mark.parametrize("cleanup", [True, False])
async def test_cleanup_is_optional_and_nonfatal(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
    cleanup: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_GMAIL_TRASH: cleanup}
    )
    inbox.trash.side_effect = GmailError("gmail_cleanup_failed")
    with patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox):
        await AutomaticMfa(hass, entry).async_renew(mock_webuser, AsyncMock(return_value=True))
    assert inbox.trash.call_count == int(cleanup)
    assert "123456" not in caplog.text and "fake-app-password" not in caplog.text


async def test_unsaved_session_leaves_email_and_cooldown(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
) -> None:
    with (
        patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox),
        pytest.raises(GmailError, match="mfa_session_not_saved"),
    ):
        await AutomaticMfa(hass, entry).async_renew(mock_webuser, AsyncMock(return_value=False))
    inbox.trash.assert_not_called()


async def test_timeout_stops_and_cooldown_survives_new_instance(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.hydroqc.auto_mfa.WAIT_SECONDS", 0)
    with patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox):
        with pytest.raises(GmailError, match="gmail_timeout"):
            await AutomaticMfa(hass, entry).async_renew(mock_webuser, AsyncMock(return_value=True))
        with pytest.raises(GmailError, match="mfa_cooldown"):
            await AutomaticMfa(hass, entry).async_renew(mock_webuser, AsyncMock(return_value=True))
    mock_webuser.request_mfa_code.assert_awaited_once()
    mock_webuser.submit_mfa_code.assert_not_awaited()
    inbox.trash.assert_not_called()


@pytest.mark.parametrize("error", ["gmail_auth_failed", "gmail_unavailable", "gmail_ambiguous"])
async def test_mail_errors_never_submit_or_cleanup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
    error: str,
) -> None:
    inbox.connect.side_effect = GmailError(error)
    with (
        patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox),
        pytest.raises(GmailError, match=error),
    ):
        await AutomaticMfa(hass, entry).async_renew(mock_webuser, AsyncMock(return_value=True))
    mock_webuser.request_mfa_code.assert_not_awaited()
    mock_webuser.submit_mfa_code.assert_not_awaited()
    inbox.trash.assert_not_called()
    inbox.close.assert_called_once()


class HydroQcMFAExpiredError(Exception):
    """Mirror the wrapper's classified error."""


async def test_expired_code_is_resent_once(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
) -> None:
    mock_webuser.submit_mfa_code.side_effect = [HydroQcMFAExpiredError(), True]
    with patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox):
        await AutomaticMfa(hass, entry).async_renew(mock_webuser, AsyncMock(return_value=True))
    assert inbox.checkpoint.call_count == 2
    assert mock_webuser.request_mfa_code.await_count == 2


async def test_expired_code_cannot_loop(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
) -> None:
    mock_webuser.submit_mfa_code.side_effect = HydroQcMFAExpiredError()
    with (
        patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox),
        pytest.raises(GmailError, match="mfa_code_rejected"),
    ):
        await AutomaticMfa(hass, entry).async_renew(mock_webuser, AsyncMock(return_value=True))
    assert mock_webuser.request_mfa_code.await_count == 2
    inbox.trash.assert_not_called()


async def test_refused_code_does_not_retry_or_log_response(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_webuser.submit_mfa_code.side_effect = ValueError("123456 fake-app-password")
    with (
        patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox),
        pytest.raises(GmailError) as error,
    ):
        await AutomaticMfa(hass, entry).async_renew(mock_webuser, AsyncMock(return_value=True))
    assert str(error.value) == "mfa_code_rejected"
    assert "123456" not in caplog.text and "fake-app-password" not in caplog.text
    mock_webuser.request_mfa_code.assert_awaited_once()
    inbox.trash.assert_not_called()


async def test_concurrent_calls_share_one_challenge(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def persist() -> bool:
        started.set()
        await release.wait()
        return True

    manager = AutomaticMfa(hass, entry)
    with patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox):
        first = asyncio.create_task(manager.async_renew(mock_webuser, persist))
        await started.wait()
        second = asyncio.create_task(manager.async_renew(mock_webuser, persist))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
    mock_webuser.request_mfa_code.assert_awaited_once()
    inbox.trash.assert_called_once()


async def test_manual_flow_cancels_polling_and_blocks_automatic(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    mock_webuser: MagicMock,
    inbox: MagicMock,
) -> None:
    polled = asyncio.Event()
    loop = asyncio.get_running_loop()

    def poll() -> None:
        loop.call_soon_threadsafe(polled.set)

    inbox.poll.side_effect = poll
    manager = AutomaticMfa(hass, entry)
    with patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox):
        task = asyncio.create_task(manager.async_renew(mock_webuser, AsyncMock(return_value=True)))
        await polled.wait()
        await async_begin_manual(hass, entry.data["username"], "manual-flow")
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(GmailError, match="mfa_manual_active"):
            await manager.async_renew(mock_webuser, AsyncMock(return_value=True))
        end_manual(hass, entry.data["username"], "manual-flow")
    inbox.close.assert_called_once()
    inbox.trash.assert_not_called()
    mock_webuser.submit_mfa_code.assert_not_awaited()
