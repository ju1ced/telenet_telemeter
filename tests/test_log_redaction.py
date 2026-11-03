"""Offline tests that login secrets never reach the debug log.

Run with: python -m unittest discover -s tests -p test_log_redaction.py
"""

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock

import requests


# Load the client in a separate package so HA's integration setup is not imported.
PACKAGE = "_telemeter_log_tests"
SOURCE = Path(__file__).resolve().parents[1] / "custom_components/telenet_telemeter"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(SOURCE)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(f"{PACKAGE}.utils", SOURCE / "utils.py")
utils = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = utils
spec.loader.exec_module(utils)

USERNAME = "user-secret@example.invalid"
PASSWORD = "pw-SECRET-123"
SECRETS = {
    "username": USERNAME,
    "password": PASSWORD,
    "state": "STATE-SECRET",
    "nonce": "NONCE-SECRET",
    "stateToken": "STATETOKEN-SECRET",
    "stateHandle 1": "HANDLE-SECRET-1",
    "stateHandle 2": "HANDLE-SECRET-2",
    "stateHandle 3": "HANDLE-SECRET-3",
    "success token": "SUCCESS-TOKEN-SECRET",
    "session cookie": "COOKIE-SECRET",
}


def _response(status=200, text="", headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = text.encode()
    response.headers.update(headers or {"Set-Cookie": f"sid={SECRETS['session cookie']}"})
    return response


def _json(status=200, **body):
    return _response(status, json.dumps(body))


class FakeOkta:
    """Routes the requests made by TelenetSession.login to canned responses."""

    def get(self, url, **kwargs):
        if url.endswith("/userdetails"):
            return _response(401, f"{SECRETS['state']},{SECRETS['nonce']}")
        if "authorization" in url or "authorize" in url:
            return _response(200, f'{{"stateToken":"{SECRETS["stateToken"]}","helpLinks":{{}}}}')
        if "maintenance" in url:
            return _json(enabled=False)
        return _response(200, "ok")

    def post(self, url, data=None, **kwargs):
        if url.endswith("/introspect"):
            return _json(stateHandle=SECRETS["stateHandle 1"])
        if url.endswith("/identify"):
            return _json(stateHandle=SECRETS["stateHandle 2"],
                         authenticators={"value": [{"type": "password", "id": "pwd-id"}]})
        if url.endswith("/challenge"):
            return _json(stateHandle=SECRETS["stateHandle 3"])
        if url.endswith("/challenge/answer"):
            return _json(success={"href": "https://login.example.invalid/login/token/redirect"
                                          f"?stateToken={SECRETS['success token']}&lang=nl"})
        return _response(200, "{}")


class TestLogRedaction(unittest.TestCase):
    def test_login_debug_log_contains_no_secrets(self):
        client = utils.TelenetSession()
        client.s.close()
        fake = FakeOkta()
        client.s = Mock(spec=requests.Session)
        client.s.headers = {}
        client.s.get.side_effect = fake.get
        client.s.post.side_effect = fake.post

        with self.assertLogs(utils._LOGGER, level="DEBUG") as logs:
            client.login(USERNAME, PASSWORD)

        # The fake really drove the whole flow, up to the success redirect.
        last_get = client.s.get.call_args.args[0]
        self.assertIn(SECRETS["success token"], last_get)
        self.assertIn("[login] Challenge answer accepted (200)", "\n".join(logs.output))

        output = "\n".join(logs.output)
        for name, secret in SECRETS.items():
            with self.subTest(secret=name):
                self.assertNotIn(secret, output)
        self.assertIn("stateToken=***", output)
        self.assertIn("lang=nl", output)

    def test_print_response_does_not_log_body(self):
        client = utils.TelenetSession()
        client.s.close()
        client.s = Mock(spec=requests.Session)
        client.s.headers = {}
        body = json.dumps({"password": PASSWORD, "passcode": "PASSCODE-SECRET",
                           "stateToken": SECRETS["stateToken"],
                           "stateHandle": SECRETS["stateHandle 1"]})
        markers = [PASSWORD, "PASSCODE-SECRET", SECRETS["stateToken"], SECRETS["stateHandle 1"]]
        for method in (utils.HttpMethod.GET, utils.HttpMethod.POST,
                       utils.HttpMethod.PATCH, utils.HttpMethod.OPTIONS):
            with self.subTest(method=method):
                getattr(client.s, method.value.lower()).return_value = _response(200, body)
                with self.assertLogs(utils._LOGGER, level="DEBUG") as logs:
                    client.callTelenet("https://x.invalid/api", caller="t",
                                       printResponse=True, method=method)
                output = "\n".join(logs.output)
                self.assertIn("http status code = 200", output)
                self.assertIn("not logged", output)
                for marker in markers:
                    self.assertNotIn(marker, output)

    def test_redact_url(self):
        cases = {
            "https://x.invalid/a?stateToken=abc&lang=nl": "https://x.invalid/a?stateToken=***&lang=nl",
            "https://x.invalid/a?code=1&STATE=2&p=3": "https://x.invalid/a?code=***&STATE=***&p=3",
            "https://x.invalid/a?productIdentifier=42": "https://x.invalid/a?productIdentifier=42",
            "https://x.invalid/a": "https://x.invalid/a",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(utils.redact_url(url), expected)


if __name__ == "__main__":
    unittest.main()
