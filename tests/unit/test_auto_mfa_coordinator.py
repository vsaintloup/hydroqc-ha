"""End-to-end coordinator renewal with fake portal and mailbox transports."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydroqc.const import (
    CONF_AUTO_MFA,
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_USERNAME,
)
from custom_components.hydroqc.coordinator import HydroQcDataCoordinator
from custom_components.hydroqc.gmail import GmailCode, GmailError
from custom_components.hydroqc.session import async_load_session


def build(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    webuser: MagicMock,
    public: MagicMock,
) -> HydroQcDataCoordinator:
    with (
        patch("custom_components.hydroqc.coordinator.base.WebUser", return_value=webuser),
        patch("custom_components.hydroqc.coordinator.base.PublicDataClient", return_value=public),
    ):
        return HydroQcDataCoordinator(hass, entry)


def enable(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_AUTO_MFA: True,
            CONF_GMAIL_USERNAME: "fake@gmail.com",
            CONF_GMAIL_APP_PASSWORD: "fake-app-password",
        },
    )


async def test_renewal_uses_same_client_and_restart_reuses_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webuser: MagicMock,
    mock_public_client: MagicMock,
) -> None:
    enable(hass, mock_config_entry)
    mock_webuser.login.return_value = "mfa_required"
    mock_webuser.export_session_state.return_value = {"refresh_token": "fake-session"}
    mailbox = MagicMock()
    mailbox.poll.return_value = GmailCode(10, b"77", b"200", "123456")
    coordinator = build(hass, mock_config_entry, mock_webuser, mock_public_client)
    with patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=mailbox):
        await asyncio.gather(coordinator._async_login(), coordinator._async_login())
    assert coordinator._reauth_required is False
    mock_webuser.request_mfa_code.assert_awaited_once()
    mock_webuser.submit_mfa_code.assert_awaited_once_with("123456")
    saved = await async_load_session(hass, mock_config_entry)
    assert saved == {"refresh_token": "fake-session"}
    restarted = build(hass, mock_config_entry, mock_webuser, mock_public_client)
    mock_webuser.try_silent_login = AsyncMock(return_value=True)
    await restarted.async_restore_session()
    await restarted._async_login()
    mock_webuser.request_mfa_code.assert_awaited_once()
    mock_webuser.try_silent_login.assert_awaited_once()


async def test_failure_stops_background_and_shutdown_cancels_schedules(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_webuser: MagicMock,
    mock_public_client: MagicMock,
) -> None:
    enable(hass, mock_config_entry)
    mock_webuser.login.return_value = "mfa_required"
    mailbox = MagicMock()
    mailbox.poll.side_effect = GmailError("gmail_ambiguous")
    coordinator = build(hass, mock_config_entry, mock_webuser, mock_public_client)
    with patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=mailbox):
        with pytest.raises(ConfigEntryAuthFailed, match="gmail_ambiguous"):
            await coordinator._async_login()
        with pytest.raises(ConfigEntryAuthFailed, match="verification code"):
            await coordinator._async_login()
    mock_webuser.request_mfa_code.assert_awaited_once()
    mock_webuser.submit_mfa_code.assert_not_awaited()
    await coordinator.async_shutdown()
    assert coordinator._schedule_cancellations == []
    assert coordinator._shutdown_requested is True
