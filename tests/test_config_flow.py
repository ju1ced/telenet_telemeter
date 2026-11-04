"""Exercise the config flow through Home Assistant's real flow manager."""

import threading
from unittest.mock import patch

import pytest
import requests
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.telenet_telemeter.const import DOMAIN


pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("enable_custom_integrations")]
DATA = {
    "provider": "Telenet", "username": "account@example.invalid", "password": " secret ",
    "internet": True, "mobile": False,
}


@pytest.fixture
def session():
    with patch("custom_components.telenet_telemeter.config_flow.TelenetSession") as factory:
        yield factory.return_value


@pytest.fixture(autouse=True)
def mock_entry_setup():
    with patch("custom_components.telenet_telemeter.async_setup_entry", return_value=True):
        yield


async def start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_success_runs_login_in_executor_and_closes_session(hass, session):
    threads = []
    session.login.side_effect = lambda *args: threads.append(threading.get_ident())
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA
    assert result["result"].unique_id == "telenet:account@example.invalid"
    session.login.assert_called_once_with(DATA["username"], DATA["password"])
    session.s.close.assert_called_once()
    assert threads and threads[0] != threading.get_ident()


@pytest.mark.parametrize("status,expected", [(401, "invalid_auth"), (403, "invalid_auth"),
                                             (429, "cannot_connect"), (500, "cannot_connect")])
async def test_http_errors_and_valid_retry(hass, session, status, expected):
    response = requests.Response()
    response.status_code = status
    session.login.side_effect = requests.HTTPError(response=response)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    session.s.close.assert_called_once()
    session.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert session.s.close.call_count == 2


@pytest.mark.parametrize("error", [requests.Timeout(), requests.ConnectionError(),
                                   KeyError("stateHandle"), ValueError("invalid response"),
                                   AttributeError("missing stateToken"), AssertionError()])
async def test_transport_and_portal_parse_errors(hass, session, error):
    session.login.side_effect = error
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "cannot_connect"}
    session.s.close.assert_called_once()


async def test_unknown_error_does_not_log_credentials(hass, session, caplog):
    secret = "DO-NOT-LOG-THIS-PASSWORD"
    session.login.side_effect = RuntimeError(secret)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "unknown"}
    assert secret not in caplog.text
    session.s.close.assert_called_once()


@pytest.mark.parametrize("unique_id", ["telenet:account@example.invalid", None])
async def test_duplicate_account_including_legacy_entry(hass, session, unique_id):
    MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=unique_id).add_to_hass(hass)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "username": " Account@Example.Invalid "}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    session.login.assert_not_called()


async def test_same_username_on_different_provider_is_allowed(hass, session):
    MockConfigEntry(domain=DOMAIN, data=DATA,
                    unique_id="telenet:account@example.invalid").add_to_hass(hass)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "provider": "BASE"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "base:account@example.invalid"


async def test_legacy_entry_without_provider_defaults_to_telenet(hass, session):
    legacy_data = {key: value for key, value in DATA.items() if key != "provider"}
    MockConfigEntry(domain=DOMAIN, data=legacy_data, unique_id=None).add_to_hass(hass)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    session.login.assert_not_called()


async def test_concurrent_account_flow_is_aborted(hass, session):
    session.login.side_effect = requests.ConnectionError()
    first = await start(hass)
    first = await hass.config_entries.flow.async_configure(first["flow_id"], DATA)
    assert first["errors"] == {"base": "cannot_connect"}
    second = await start(hass)
    second = await hass.config_entries.flow.async_configure(second["flow_id"], DATA)
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"
    session.login.assert_called_once()


async def test_retry_preserves_provider_and_usage_choices(hass, session):
    session.login.side_effect = requests.ConnectionError()
    data = {**DATA, "provider": "BASE", "internet": False, "mobile": True}
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    defaults = result["data_schema"]({"username": DATA["username"], "password": DATA["password"]})
    assert defaults == data


@pytest.mark.parametrize("changes", [{"username": ""}, {"password": ""},
                                     {"internet": False, "mobile": False}])
async def test_invalid_settings_do_not_attempt_login(hass, session, changes):
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**DATA, **changes})
    assert result["errors"] == {"base": "no_valid_settings"}
    session.login.assert_not_called()


async def test_yaml_import_aborts_without_creating_entry(hass, session):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data={}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "yaml_not_supported"
    assert not hass.config_entries.async_entries(DOMAIN)
    session.login.assert_not_called()
