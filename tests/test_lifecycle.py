"""Config entry lifecycle and reboot service tests (TM-09).

Needs requirements.test.txt. Run with: python -m pytest tests/test_lifecycle.py
"""

from unittest.mock import MagicMock, patch

import pytest
import requests
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import telenet_telemeter as integration
from custom_components.telenet_telemeter.const import DOMAIN

SERVICE = "reboot_internet"


@pytest.fixture(autouse=True)
def no_platforms():
    """Keep the sensor/switch platforms offline: they would log in to Telenet."""
    async def setup_platform(hass, entry, add_entities):
        return True

    with patch("custom_components.telenet_telemeter.sensor.async_setup_entry", setup_platform), \
         patch("custom_components.telenet_telemeter.switch.async_setup_entry", setup_platform):
        yield


def make_entry(hass, provider="Telenet", internet=True, username="user@example.invalid"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"{provider} {username}",
        data={"provider": provider, "username": username, "password": "pw",
              "internet": internet, "mobile": True},
    )
    entry.add_to_hass(hass)
    return entry


async def setup(hass, entry):
    # Setting up the first entry loads the integration, which sets up all its entries.
    if entry.state is ConfigEntryState.NOT_LOADED:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_service_registered_once_and_removed_with_last_entry(hass):
    telenet = make_entry(hass, "Telenet")
    base = make_entry(hass, "BASE")
    await setup(hass, telenet)
    await setup(hass, base)
    assert hass.services.has_service(DOMAIN, SERVICE)

    assert await hass.config_entries.async_unload(telenet.entry_id)
    assert hass.services.has_service(DOMAIN, SERVICE), "BASE entry still needs the service"

    assert await hass.config_entries.async_unload(base.entry_id)
    assert not hass.services.has_service(DOMAIN, SERVICE)


async def test_no_service_without_internet_tracking(hass):
    entry = make_entry(hass, internet=False)
    await setup(hass, entry)
    assert not hass.services.has_service(DOMAIN, SERVICE)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_mobile_only_entry_does_not_keep_service_alive(hass):
    internet = make_entry(hass, "Telenet")
    mobile_only = make_entry(hass, "BASE", internet=False)
    await setup(hass, internet)
    await setup(hass, mobile_only)
    assert await hass.config_entries.async_unload(internet.entry_id)
    assert not hass.services.has_service(DOMAIN, SERVICE)


async def test_service_targets_selected_entry(hass):
    telenet = make_entry(hass, "Telenet", username="t@example.invalid")
    base = make_entry(hass, "BASE", username="b@example.invalid")
    await setup(hass, telenet)
    await setup(hass, base)

    with patch.object(integration, "_reboot_modem", return_value=True) as reboot:
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, SERVICE, {}, blocking=True)
        assert err.value.translation_key == "entry_ambiguous"
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN, SERVICE, {"config_entry_id": "unknown"}, blocking=True)
        assert err.value.translation_key == "entry_not_found"
        reboot.assert_not_called()

        await hass.services.async_call(
            DOMAIN, SERVICE, {"config_entry_id": base.entry_id}, blocking=True)
        reboot.assert_called_once()
        assert reboot.call_args.args[0]["username"] == "b@example.invalid"

        # With one eligible entry left, the id may be omitted.
        assert await hass.config_entries.async_unload(base.entry_id)
        reboot.reset_mock()
        await hass.services.async_call(DOMAIN, SERVICE, {}, blocking=True)
        assert reboot.call_args.args[0]["username"] == "t@example.invalid"


async def test_service_reports_failures(hass):
    entry = make_entry(hass)
    await setup(hass, entry)
    cases = [
        (False, "reboot_unsupported", None),
        (requests.ConnectionError("offline"), "reboot_failed", "ConnectionError"),
        # The login flow asserts on status codes and indexes JSON responses.
        (AssertionError("status 500 body=secret"), "reboot_failed", "AssertionError"),
        (KeyError("stateHandle"), "reboot_failed", "KeyError"),
        (HomeAssistantError(translation_domain=DOMAIN, translation_key="no_modem"), "no_modem", None),
    ]
    for outcome, key, error in cases:
        with patch.object(integration, "_reboot_modem", side_effect=[outcome]), \
             pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(DOMAIN, SERVICE, {}, blocking=True)
        assert err.value.translation_key == key
        if error:
            assert err.value.translation_placeholders["error"] == error
            assert err.value.__cause__ is None  # no response details chained


async def test_reboot_does_not_log_username(hass, caplog):
    entry = make_entry(hass, username="secret-user@example.invalid")
    await setup(hass, entry)
    caplog.clear()
    with patch.object(integration, "_reboot_modem", return_value=True):
        await hass.services.async_call(DOMAIN, SERVICE, {}, blocking=True)
    assert entry.entry_id in caplog.text
    assert "secret-user" not in caplog.text


async def test_update_reloads_and_unload_removes_listener(hass):
    entry = make_entry(hass)
    await setup(hass, entry)
    assert len(entry.update_listeners) == 1

    with patch.object(hass.config_entries, "async_reload") as reload:
        hass.config_entries.async_update_entry(entry, options={"changed": True})
        await hass.async_block_till_done()
        reload.assert_called_once_with(entry.entry_id)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.update_listeners == []


async def test_reload_keeps_single_service_and_listener(hass):
    entry = make_entry(hass)
    await setup(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, SERVICE)
    assert len(entry.update_listeners) == 1


async def test_yaml_config_creates_no_entries(hass, caplog):
    with patch.object(hass.config_entries.flow, "async_init") as init:
        assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
        await hass.async_block_till_done()
        # A restart runs async_setup again; it must not accumulate entries.
        assert await integration.async_setup(hass, {DOMAIN: {}})
        await hass.async_block_till_done()
    init.assert_not_called()
    assert hass.config_entries.async_entries(DOMAIN) == []
    assert "configuration.yaml is not supported" in caplog.text


def test_reboot_modem_closes_session_on_error():
    session = MagicMock()
    session.login.side_effect = requests.HTTPError("401")
    with patch.object(integration, "TelenetSession", return_value=session), \
         pytest.raises(requests.HTTPError):
        integration._reboot_modem({"username": "u", "password": "p"})
    session.s.close.assert_called_once()


def test_reboot_modem_happy_path():
    session = MagicMock()
    session.apiVersion2.return_value = True
    session.productSubscriptions.return_value = [{"identifier": "INT1"}]
    session.modemdetails.return_value = {"mac": "AA:BB"}
    with patch.object(integration, "TelenetSession", return_value=session) as cls:
        assert integration._reboot_modem({"provider": "BASE", "username": "u", "password": "p"})
    cls.assert_called_once_with(provider="BASE")
    session.login.assert_called_once_with("u", "p")
    session.modemdetails.assert_called_once_with("INT1")
    session.reboot.assert_called_once_with("AA:BB")
    session.s.close.assert_called_once()
