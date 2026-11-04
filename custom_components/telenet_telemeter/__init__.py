import logging
import json
from pathlib import Path

import requests
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from .utils import TelenetSession
from .const import PROVIDER_TELENET, PROVIDER_NAMES
from homeassistant.const import (
    CONF_NAME,
    CONF_PASSWORD,
    CONF_RESOURCES,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME
)

manifestfile = Path(__file__).parent / 'manifest.json'
with open(manifestfile, 'r') as json_file:
    manifest_data = json.load(json_file)
    
DOMAIN = manifest_data.get("domain")
NAME = manifest_data.get("name")
VERSION = manifest_data.get("version")
ISSUEURL = manifest_data.get("issue_tracker")
PLATFORMS = [Platform.SENSOR, Platform.SWITCH]

STARTUP = """
-------------------------------------------------------------------
{name}
Version: {version}
This is a custom component
If you have any issues with this you need to open an issue here:
{issueurl}
-------------------------------------------------------------------
""".format(
    name=NAME, version=VERSION, issueurl=ISSUEURL
)


_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Set up this component. YAML configuration is not supported."""
    _LOGGER.info(STARTUP)
    if DOMAIN in config:
        # The old import step created an empty, credential-less entry on every
        # start; accounts are configured through the UI instead.
        _LOGGER.warning(
            "Configuring %s in configuration.yaml is not supported; remove the "
            "'%s:' section and add the integration via Settings > Devices & services",
            NAME, DOMAIN,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up component as config entry."""
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    config_entry.async_on_unload(config_entry.add_update_listener(async_reload_entry))
    if config_entry.data.get("internet") and not hass.services.has_service(DOMAIN, SERVICE_REBOOT_INTERNET):
        hass.services.async_register(
            DOMAIN, SERVICE_REBOOT_INTERNET, _make_reboot_handler(hass), schema=REBOOT_SCHEMA
        )
    return True


async def async_reload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Reload the entry when its data or options change."""
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
    if unload_ok and not _reboot_entries(hass, exclude=config_entry.entry_id):
        hass.services.async_remove(DOMAIN, SERVICE_REBOOT_INTERNET)
    return unload_ok


SERVICE_REBOOT_INTERNET = "reboot_internet"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
REBOOT_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


def _reboot_entries(hass: HomeAssistant, exclude: str | None = None) -> list[ConfigEntry]:
    """Loaded entries that track internet and can therefore reboot a modem."""
    return [
        entry for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id != exclude
        and entry.state is ConfigEntryState.LOADED
        and entry.data.get("internet")
    ]


def _select_reboot_entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry:
    entries = _reboot_entries(hass)
    if entry_id is not None:
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if len(entries) == 1:
        return entries[0]
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="entry_ambiguous",
        translation_placeholders={"count": str(len(entries))},
    )


def _reboot_modem(config) -> bool:
    """Blocking: log in and reboot the modem. Returns False when unsupported."""
    session = TelenetSession(provider=config.get("provider", PROVIDER_TELENET))
    try:
        session.login(config.get(CONF_USERNAME), config.get(CONF_PASSWORD))
        if not session.apiVersion2():
            return False
        products = session.productSubscriptions("INTERNET")
        identifier = products[0].get("identifier") if products else None
        if not identifier:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_internet_product")
        modem_mac = (session.modemdetails(identifier) or {}).get("mac")
        if not modem_mac:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_modem")
        session.reboot(modem_mac)
        return True
    finally:
        session.s.close()


def _make_reboot_handler(hass: HomeAssistant):
    async def handle_reboot_internet(call: ServiceCall) -> None:
        """Reboot the modem of the selected (or only) entry."""
        entry = _select_reboot_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        provider_name = PROVIDER_NAMES.get(entry.data.get("provider", PROVIDER_TELENET), NAME)
        try:
            rebooted = await hass.async_add_executor_job(_reboot_modem, entry.data)
        except HomeAssistantError:
            raise
        except (requests.RequestException, AssertionError, KeyError, IndexError, TypeError, ValueError) as err:
            # The login flow signals unexpected responses with asserts and
            # lookups; report only the error type so no response data leaks.
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="reboot_failed",
                translation_placeholders={"provider": provider_name, "error": type(err).__name__},
            ) from None
        if not rebooted:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="reboot_unsupported",
                translation_placeholders={"provider": provider_name},
            )
        # entry_id only: the entry title contains the account username.
        _LOGGER.info("%s modem reboot requested for entry %s", provider_name, entry.entry_id)

    return handle_reboot_internet
