"""Options flow for Hydro-Québec integration."""

from __future__ import annotations

from typing import Any

import probatio as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from ..const import (
    AUTH_CLIENTS,
    CONF_AUTH_CLIENT,
    CONF_AUTO_MFA,
    CONF_CALENDAR_ENTITY_ID,
    CONF_ENABLE_CONSUMPTION_SYNC,
    CONF_GMAIL_APP_PASSWORD,
    CONF_GMAIL_TRASH,
    CONF_GMAIL_USERNAME,
    CONF_PREHEAT_DURATION,
    CONF_RATE,
    CONF_RATE_OPTION,
    DEFAULT_AUTH_CLIENT,
    DEFAULT_PREHEAT_DURATION,
    SECTION_ADVANCED,
)
from ..gmail import GmailError, validate_gmail


class HydroQcOptionsFlow(config_entries.OptionsFlowWithReload):
    """Handle options flow for Hydro-Québec integration."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check if rate supports calendar configuration
            rate = self.config_entry.data.get(CONF_RATE, "")
            rate_option = self.config_entry.data.get(CONF_RATE_OPTION, "")
            rate_with_option = f"{rate}{rate_option}"
            supports_calendar = rate_with_option in ["DPC", "DCPC"]

            # Validate calendar for DPC/DCPC rates
            if supports_calendar:
                calendar_id = user_input.get(CONF_CALENDAR_ENTITY_ID, "").strip()
                if not calendar_id:
                    errors["base"] = "calendar_required"
                elif not self.hass.states.get(calendar_id):
                    errors[CONF_CALENDAR_ENTITY_ID] = "calendar_not_found"

            if not errors:
                data = dict(user_input)
                data.update(data.pop(SECTION_ADVANCED, {}))
                self._pending_options = {**self.config_entry.options, **data}
                if self.config_entry.data.get(
                    "auth_mode", "portal"
                ) == "portal" and self._pending_options.get(CONF_AUTO_MFA, False):
                    return await self.async_step_gmail()
                return self.async_create_entry(title="", data=self._pending_options)

        # Check if rate supports calendar configuration
        rate = self.config_entry.data.get(CONF_RATE, "")
        rate_option = self.config_entry.data.get(CONF_RATE_OPTION, "")
        rate_with_option = f"{rate}{rate_option}"
        supports_calendar = rate_with_option in ["DPC", "DCPC"]
        is_portal_mode = self.config_entry.data.get("auth_mode", "portal") == "portal"

        # Build schema based on rate capabilities
        schema_dict: dict[Any, Any] = {
            vol.Optional(
                CONF_PREHEAT_DURATION,
                default=self.config_entry.options.get(
                    CONF_PREHEAT_DURATION,
                    self.config_entry.data.get(CONF_PREHEAT_DURATION, DEFAULT_PREHEAT_DURATION),
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=240,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="minutes",
                )
            ),
        }

        # Add consumption sync option for Portal mode only
        if is_portal_mode:
            schema_dict[
                vol.Optional(
                    CONF_AUTO_MFA, default=self.config_entry.options.get(CONF_AUTO_MFA, False)
                )
            ] = bool
            schema_dict[
                vol.Optional(
                    CONF_ENABLE_CONSUMPTION_SYNC,
                    default=self.config_entry.options.get(
                        CONF_ENABLE_CONSUMPTION_SYNC,
                        self.config_entry.data.get(CONF_ENABLE_CONSUMPTION_SYNC, True),
                    ),
                )
            ] = bool

        # Add calendar options for DPC/DCPC rates (required)
        if supports_calendar:
            current_calendar = self.config_entry.options.get(
                CONF_CALENDAR_ENTITY_ID,
                self.config_entry.data.get(CONF_CALENDAR_ENTITY_ID, ""),
            )
            # Calendar is required for DPC/DCPC rates
            if current_calendar:
                schema_dict[vol.Required(CONF_CALENDAR_ENTITY_ID, default=current_calendar)] = (
                    EntitySelector(EntitySelectorConfig(domain="calendar"))
                )
            else:
                schema_dict[vol.Required(CONF_CALENDAR_ENTITY_ID)] = EntitySelector(
                    EntitySelectorConfig(domain="calendar")
                )

        if is_portal_mode:
            schema_dict[vol.Optional(SECTION_ADVANCED)] = section(
                vol.Schema(
                    {
                        vol.Required(
                            CONF_AUTH_CLIENT,
                            default=self.config_entry.options.get(
                                CONF_AUTH_CLIENT, DEFAULT_AUTH_CLIENT
                            ),
                        ): SelectSelector(
                            SelectSelectorConfig(
                                options=AUTH_CLIENTS,
                                translation_key=CONF_AUTH_CLIENT,
                                mode=SelectSelectorMode.LIST,
                            )
                        ),
                    }
                ),
                {"collapsed": True},
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_gmail(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Validate Gmail without reading messages or sending a Hydro code."""
        errors: dict[str, str] = {}
        saved = self.config_entry.options
        if user_input is not None:
            username = user_input[CONF_GMAIL_USERNAME].strip()
            password = user_input.get(CONF_GMAIL_APP_PASSWORD, "").replace(" ", "") or saved.get(
                CONF_GMAIL_APP_PASSWORD, ""
            )
            if not username.casefold().endswith(("@gmail.com", "@googlemail.com")):
                errors[CONF_GMAIL_USERNAME] = "gmail_address_required"
            elif not password:
                errors[CONF_GMAIL_APP_PASSWORD] = "gmail_password_required"
            else:
                try:
                    await self.hass.async_add_executor_job(validate_gmail, username, password)
                except GmailError as err:
                    errors["base"] = (
                        "gmail_auth_failed"
                        if str(err) == "gmail_auth_failed"
                        else "gmail_cannot_connect"
                    )
                else:
                    self._pending_options.update(
                        {
                            CONF_GMAIL_USERNAME: username,
                            CONF_GMAIL_APP_PASSWORD: password,
                            CONF_GMAIL_TRASH: user_input.get(CONF_GMAIL_TRASH, True),
                        }
                    )
                    return self.async_create_entry(title="", data=self._pending_options)
        return self.async_show_form(
            step_id="gmail",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GMAIL_USERNAME,
                        default=saved.get(CONF_GMAIL_USERNAME, ""),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                    vol.Optional(CONF_GMAIL_APP_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_GMAIL_TRASH, default=saved.get(CONF_GMAIL_TRASH, True)): bool,
                }
            ),
            errors=errors,
        )
