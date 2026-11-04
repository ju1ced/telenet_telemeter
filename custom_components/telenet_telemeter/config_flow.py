"""Adds config flow for component."""
import logging
import requests
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .utils import TelenetSession, check_settings
from .const import DOMAIN, NAME, PROVIDERS, PROVIDER_TELENET

_LOGGER = logging.getLogger(__name__)


def create_schema(entry=None, option=False):
    """Keep submitted choices when displaying the form again."""
    data = entry.data if option else (entry or {})
    return {
        vol.Required("provider", default=data.get("provider", PROVIDER_TELENET)): vol.In(PROVIDERS),
        vol.Required(CONF_USERNAME, default=data.get(CONF_USERNAME, "")): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional("internet", default=data.get("internet", True)): bool,
        vol.Optional("mobile", default=data.get("mobile", True)): bool,
    }


def account_id(data):
    """Use provider and normalized username to identify an account."""
    return f"{data.get('provider', PROVIDER_TELENET).casefold()}:{data.get(CONF_USERNAME, '').strip().casefold()}"


def validate_login(data):
    """Run blocking authentication entirely inside the executor."""
    session = TelenetSession(provider=data.get("provider", PROVIDER_TELENET))
    try:
        session.login(data[CONF_USERNAME], data[CONF_PASSWORD])
    finally:
        session.s.close()


class Mixin:
    async def test_setup(self, user_input):
        self._errors = {}
        try:
            check_settings(user_input, self.hass)
        except (ValueError, vol.Invalid):
            self._errors["base"] = "no_valid_settings"
            return False
        return True


class ComponentFlowHandler(Mixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for component."""

    VERSION = 1

    def __init__(self):
        """Initialize."""
        self._errors = {}

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""

        if user_input is not None:
            data = {**user_input, "provider": user_input.get("provider", PROVIDER_TELENET)}
            data[CONF_USERNAME] = data.get(CONF_USERNAME, "").strip()
            await self.test_setup(data)
            if self._errors:
                return await self._show_config_form(data)
            unique_id = account_id(data)
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            # Entries created before unique IDs were introduced must also match.
            if any(account_id(entry.data) == unique_id for entry in self._async_current_entries()):
                return self.async_abort(reason="already_configured")
            try:
                await self.hass.async_add_executor_job(validate_login, data)
            except requests.HTTPError as error:
                response = error.response
                self._errors["base"] = (
                    "invalid_auth" if response is not None and response.status_code in (401, 403)
                    else "cannot_connect"
                )
            except (requests.RequestException, AssertionError, KeyError, ValueError, AttributeError):
                self._errors["base"] = "cannot_connect"
            except Exception as error:
                # Exception text and tracebacks may contain login responses/tokens.
                _LOGGER.error("Unexpected login validation failure (%s)", type(error).__name__)
                self._errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"{NAME} {data['provider']} {data[CONF_USERNAME]}", data=data
                )
            return await self._show_config_form(data)

        return await self._show_config_form(user_input)

    async def _show_config_form(self, user_input):
        """Show the configuration form to edit location data."""
        data_schema = create_schema(user_input)
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(data_schema), errors=self._errors
        )

    async def async_step_import(self, user_input):
        """Reject legacy YAML instead of creating an unusable empty entry."""
        return self.async_abort(reason="yaml_not_supported")


class ComponentOptionsHandler(config_entries.OptionsFlow, Mixin):
    """Options flow handler."""

    def __init__(self, config_entry):
        self.config_entry = config_entry
        self.options = dict(config_entry.options)
        self._errors = {}

    async def async_step_init(self, user_input=None):
        return self.async_show_form(
            step_id="edit",
            data_schema=vol.Schema(create_schema(self.config_entry, option=True)),
            errors=self._errors,
        )

    async def async_step_edit(self, user_input):
        if user_input is not None:
            ok = await self.test_setup(user_input)
            if ok:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=user_input
                )
                return self.async_create_entry(title="", data={})
            else:
                return self.async_show_form(
                    step_id="edit",
                    data_schema=vol.Schema(
                        create_schema(self.config_entry, option=True)
                    ),
                    errors=self._errors,
                )
