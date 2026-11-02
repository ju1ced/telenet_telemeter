"""Offline tests for the HTTP client without starting Home Assistant.

Run with: python -m unittest discover -s tests -p test_http_session.py
"""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

import requests


# Load the client in a separate package so HA's integration setup is not imported.
PACKAGE = "_telemeter_http_tests"
SOURCE = Path(__file__).resolve().parents[1] / "custom_components/telenet_telemeter"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(SOURCE)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(f"{PACKAGE}.utils", SOURCE / "utils.py")
utils = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = utils
spec.loader.exec_module(utils)


class TestHttpSession(unittest.TestCase):
    def setUp(self):
        self.client = utils.TelenetSession()
        self.client.s.close()
        self.client.s = Mock(spec=requests.Session)
        self.client.s.headers = {}
        self.url = "https://example.invalid/api"

    def test_transport_errors_propagate(self):
        for error_type in (requests.Timeout, requests.ConnectionError):
            with self.subTest(error=error_type):
                error = error_type("offline")
                self.client.s.get.side_effect = error
                with self.assertRaises(error_type) as caught:
                    self.client.callTelenet(self.url)
                self.assertIs(caught.exception, error)

    def test_unsupported_methods_do_not_send_requests(self):
        for method in (utils.HttpMethod.PUT, utils.HttpMethod.DELETE,
                       utils.HttpMethod.HEAD, "GET", None):
            with self.subTest(method=method):
                with self.assertRaises(ValueError):
                    self.client.callTelenet(self.url, method=method)
        self.assertEqual(self.client.s.mock_calls, [])

    def test_supported_methods_preserve_payloads_and_redirect_control(self):
        for method in (utils.HttpMethod.GET, utils.HttpMethod.POST,
                       utils.HttpMethod.PATCH, utils.HttpMethod.OPTIONS):
            with self.subTest(method=method):
                request = getattr(self.client.s, method.value.lower())
                response = requests.Response()
                response.status_code = 200
                request.return_value = response
                data = {"enabled": True}
                self.assertIs(self.client.callTelenet(
                    self.url, method=method, data=data, allowRedirects=False,
                ), response)
                if method == utils.HttpMethod.POST:
                    request.assert_called_once_with(
                        self.url, data, timeout=30, allow_redirects=False,
                    )
                elif method == utils.HttpMethod.PATCH:
                    request.assert_called_once_with(
                        self.url, json=data, timeout=60, allow_redirects=False,
                    )
                else:
                    request.assert_called_once_with(
                        self.url, timeout=30 if method == utils.HttpMethod.GET else 60,
                        allow_redirects=False,
                    )

    def test_unexpected_status_raises_http_error_with_response(self):
        for status in (201, 302, 401, 500):
            with self.subTest(status=status):
                response = requests.Response()
                response.status_code = status
                self.client.s.get.return_value = response
                with self.assertRaises(requests.HTTPError) as caught:
                    self.client.callTelenet(self.url)
                self.assertIs(caught.exception.response, response)

    def test_custom_expected_status_and_disabled_status_check(self):
        response = requests.Response()
        response.status_code = 401
        self.client.s.get.return_value = response
        for expected in (401, None):
            with self.subTest(expected=expected):
                self.assertIs(self.client.callTelenet(
                    self.url, expectedStatusCode=expected,
                ), response)


if __name__ == "__main__":
    unittest.main()
