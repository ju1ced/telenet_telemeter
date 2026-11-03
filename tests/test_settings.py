"""Offline validation tests; Home Assistant UI plumbing is stubbed.

Run with: python -m unittest discover -s tests -p test_settings.py
"""

import importlib.util
from itertools import product
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import voluptuous as vol


SOURCE = Path(__file__).resolve().parents[1] / "custom_components/telenet_telemeter"
PACKAGE = "_telemeter_settings_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(SOURCE)]
package.DOMAIN = "telenet_telemeter"
package.NAME = "Telenet Telemeter"
sys.modules[PACKAGE] = package


def load_module(name):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.{name}", SOURCE / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


utils = load_module("utils")


class FlowStub:
    """Provide only the form/entry interface used by these flow steps."""

    def __init_subclass__(cls, **kwargs):
        pass

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


ha = types.ModuleType("homeassistant")
ha.config_entries = types.SimpleNamespace(
    ConfigFlow=FlowStub, OptionsFlow=FlowStub, CONN_CLASS_CLOUD_POLL="cloud_poll"
)
ha_const = types.ModuleType("homeassistant.const")
for key in ("NAME", "PASSWORD", "RESOURCES", "SCAN_INTERVAL", "USERNAME"):
    setattr(ha_const, f"CONF_{key}", key.lower())
with patch.dict(sys.modules, {"homeassistant": ha, "homeassistant.const": ha_const}):
    flow = load_module("config_flow")


class TestSettings(unittest.TestCase):
    def test_credentials_and_usage_combinations(self):
        for username, password, internet, mobile in product(
            (None, "", "account"), (None, "", "password"),
            (None, False, True), (None, False, True),
        ):
            config = dict(username=username, password=password,
                          internet=internet, mobile=mobile)
            valid = bool(username and password and (internet or mobile))
            with self.subTest(config=config):
                if valid:
                    self.assertIs(utils.check_settings(config, None), True)
                else:
                    with self.assertRaises(vol.Invalid):
                        utils.check_settings(config, None)

    def test_missing_keys_are_rejected(self):
        for config in ({}, {"username": "account"},
                       {"username": "account", "password": "password"}):
            with self.subTest(config=config), self.assertRaises(vol.Invalid):
                utils.check_settings(config, None)


class TestSettingsFlow(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_submission_and_valid_retry(self):
        handler = flow.ComponentFlowHandler()
        handler.hass = Mock()
        for invalid in ({}, {"username": "account"},
                        dict(username="account", password="password",
                             internet=False, mobile=False)):
            with self.subTest(invalid=invalid):
                result = await handler.async_step_user(invalid)
                self.assertEqual(result["type"], "form")
                self.assertEqual(result["errors"], {"base": "no_valid_settings"})

        valid = dict(username="account", password="password",
                     internet=True, mobile=False)
        result = await handler.async_step_user(valid)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], valid)
        self.assertEqual(handler._errors, {})

    async def test_options_keep_validation_error_and_accept_retry(self):
        entry = Mock(data={}, options={})
        handler = flow.ComponentOptionsHandler(entry)
        handler.hass = Mock()
        invalid = dict(username="account", password="password",
                       internet=False, mobile=False)
        result = await handler.async_step_edit(invalid)
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "no_valid_settings"})
        handler.hass.config_entries.async_update_entry.assert_not_called()

        valid = {**invalid, "mobile": True}
        result = await handler.async_step_edit(valid)
        self.assertEqual(result["type"], "create_entry")
        handler.hass.config_entries.async_update_entry.assert_called_once_with(
            entry, data=valid,
        )
        self.assertEqual(handler._errors, {})


if __name__ == "__main__":
    unittest.main()
