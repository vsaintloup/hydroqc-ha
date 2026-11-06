"""Options validation must preserve unrelated settings and never send a Hydro code."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hydroqc.const import (
    CONF_AUTO_MFA,
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_TRASH,
    CONF_GMAIL_USERNAME,
)
from custom_components.hydroqc.gmail import GmailError


@pytest.fixture(autouse=True)
def enable_custom(enable_custom_integrations: None) -> None:
    """Allow the options flow to be discovered."""


async def test_gmail_options_preserve_values_and_keep_password_when_blank(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            "unrelated_future_option": 42,
            CONF_GMAIL_USERNAME: "fake@gmail.com",
            CONF_GMAIL_APP_PASSWORD: "stored-fake-secret",
        },
    )
    with (
        patch("custom_components.hydroqc.config_flow.options.validate_gmail") as validate,
        patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload,
    ):
        result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_AUTO_MFA: True, "preheat_duration_minutes": 75}
        )
        assert result["step_id"] == "gmail"
        assert "stored-fake-secret" not in repr(result)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_GMAIL_USERNAME: "fake@gmail.com", CONF_GMAIL_TRASH: False},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options["unrelated_future_option"] == 42
    assert mock_config_entry.options["preheat_duration_minutes"] == 75
    assert mock_config_entry.options[CONF_GMAIL_APP_PASSWORD] == "stored-fake-secret"
    assert mock_config_entry.options[CONF_GMAIL_TRASH] is False
    validate.assert_called_once_with("fake@gmail.com", "stored-fake-secret")
    reload.assert_awaited_once_with(mock_config_entry.entry_id)


async def test_gmail_error_keeps_form_and_does_not_store_secret(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_AUTO_MFA: True}
    )
    with patch(
        "custom_components.hydroqc.config_flow.options.validate_gmail",
        side_effect=GmailError("gmail_auth_failed"),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_GMAIL_USERNAME: "fake@gmail.com", CONF_GMAIL_APP_PASSWORD: "invalid-fake"},
        )
    assert result["step_id"] == "gmail"
    assert result["errors"] == {"base": "gmail_auth_failed"}
    assert "invalid-fake" not in repr(result)
    assert CONF_GMAIL_APP_PASSWORD not in mock_config_entry.options


async def test_opendata_does_not_offer_gmail(
    hass: HomeAssistant,
    mock_config_entry_opendata: MockConfigEntry,
) -> None:
    mock_config_entry_opendata.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry_opendata.entry_id)
    fields = [key.schema for key in result["data_schema"].schema]
    assert CONF_AUTO_MFA not in fields
