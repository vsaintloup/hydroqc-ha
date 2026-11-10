"""Exercise Gmail renewal and session portability with the real 4.4 wrapper."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from hydroqc.webuser import WebUser
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydroqc.auth import webuser_auth_kwargs
from custom_components.hydroqc.auto_mfa import AutomaticMfa
from custom_components.hydroqc.const import (
    CONF_AUTH_CLIENT,
    CONF_AUTO_MFA,
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_USERNAME,
)
from custom_components.hydroqc.gmail import GmailCode
from custom_components.hydroqc.session import async_load_session, async_save_session


@pytest.mark.parametrize("auth_client", ["web", "mobile"])
async def test_real_wrapper_gmail_session_round_trip(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    auth_client: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both clients delegate MFA on one instance and restore the saved tokens."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            CONF_AUTH_CLIENT: auth_client,
            CONF_AUTO_MFA: True,
            CONF_GMAIL_USERNAME: "fake@gmail.com",
            CONF_GMAIL_APP_PASSWORD: "fake-app-password",
        },
    )
    kwargs = webuser_auth_kwargs(mock_config_entry.options)
    user = WebUser("test@example.com", "fake-password", verify_ssl=True, **kwargs)
    restored = WebUser("test@example.com", "fake-password", verify_ssl=True, **kwargs)
    client = user._hydro_client
    inbox = MagicMock()
    inbox.poll.return_value = GmailCode(12, b"77", b"200", "123456")

    async def submit(code: str) -> bool:
        assert code == "123456"
        client.refresh_token = "fake-refreshed-token"
        client.refresh_token_expiry = datetime.now() + timedelta(days=1)
        return True

    async def persist() -> bool:
        return await async_save_session(hass, mock_config_entry, user.export_session_state())

    try:
        with (
            patch("custom_components.hydroqc.auto_mfa.GmailInbox", return_value=inbox),
            patch.object(client, "request_mfa_code", new=AsyncMock(return_value=True)) as request,
            patch.object(client, "submit_mfa_code", new=AsyncMock(side_effect=submit)) as verify,
        ):
            await AutomaticMfa(hass, mock_config_entry).async_renew(user, persist)
        request.assert_awaited_once()
        verify.assert_awaited_once_with("123456")
        saved = await async_load_session(hass, mock_config_entry)
        assert saved is not None
        restored.import_session_state(saved)
        state = restored.export_session_state()
        assert state["refresh_token"] == "fake-refreshed-token"
        assert state["client_id"] == saved["client_id"]
        inbox.trash.assert_called_once_with(inbox.poll.return_value)
        # HA's storage fixture logs its fake persisted data at DEBUG. Check the
        # integration and wrapper loggers, which are the production paths.
        runtime_logs = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name.startswith(("hydroqc", "custom_components.hydroqc"))
        )
        assert "123456" not in runtime_logs
        assert "fake-app-password" not in runtime_logs
        assert "fake-refreshed-token" not in runtime_logs
    finally:
        await user.close_session()
        await restored.close_session()


async def test_legacy_web_session_survives_wrapper_upgrade() -> None:
    """Sessions from 4.3.1 have no client_id and must still restore as web."""
    user = WebUser("test@example.com", "fake-password", verify_ssl=True)
    mobile = WebUser("test@example.com", "fake-password", verify_ssl=True, auth_client="mobile")
    try:
        user.import_session_state({"refresh_token": "fake-old-token", "cookies": []})
        assert user.export_session_state()["refresh_token"] == "fake-old-token"
        mobile.import_session_state(user.export_session_state())
        assert mobile.export_session_state()["refresh_token"] != "fake-old-token"
    finally:
        await user.close_session()
        await mobile.close_session()
