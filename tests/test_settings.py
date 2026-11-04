"""Offline settings validation tests; real flow tests are in test_config_flow.py.

Run with: python -m unittest discover -s tests -p test_settings.py
"""

import importlib.util
from itertools import product
from pathlib import Path
import sys
import types
import unittest

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


if __name__ == "__main__":
    unittest.main()
