import json
import logging
from datetime import date, datetime, timedelta
import requests
from enum import Enum
import re
from ratelimit import limits, sleep_and_retry

import voluptuous as vol

from .const import PROVIDER_TELENET, PROVIDER_CONFIG

_LOGGER = logging.getLogger(__name__)

TELENET_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S.0%z"

def check_settings(config, hass):
    """Require credentials and at least one enabled usage type."""
    if not config.get("username"):
        raise vol.Invalid("Username is required")
    if not config.get("password"):
        raise vol.Invalid("Password is required")
    if not (config.get("internet") or config.get("mobile")):
        raise vol.Invalid("At least one of internet or mobile must be enabled")
    return True

class HttpMethod(Enum):
    GET = 'GET'
    POST = 'POST'
    PUT = 'PUT'
    PATCH = 'PATCH'
    DELETE = 'DELETE'
    HEAD = 'HEAD'
    OPTIONS = 'OPTIONS'
    

class TelenetSession(object):
    def __init__(self, provider=PROVIDER_TELENET):
        self.provider = provider
        self.cfg = PROVIDER_CONFIG.get(provider, PROVIDER_CONFIG[PROVIDER_TELENET])
        self.api_url = self.cfg["api_url"]
        self.secure_url = self.cfg["secure_url"]

        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0"
        self.s.headers["x-alt-referer"] = self.cfg["alt_referer"]
        self.s.headers["X-Requested-With"] = "XMLHttpRequest"
        self.s.headers["Origin"] = self.cfg["origin"]
        self.s.headers["Referrer"] = self.cfg["referrer"]

    def callTelenet(self, url, caller = "Not set", expectedStatusCode = 200, data = None, printResponse = False, method : HttpMethod  = HttpMethod.GET, allowRedirects = True):
        if method not in (HttpMethod.GET, HttpMethod.POST, HttpMethod.PATCH, HttpMethod.OPTIONS):
            raise ValueError(f"Unsupported HTTP method: {method!r}")
        try:
            if method == HttpMethod.GET:
                _LOGGER.debug(f"[{caller}] Calling GET {url}")
                response = self.s.get(url,timeout=30, allow_redirects=allowRedirects)
            elif method == HttpMethod.POST:
                self.s.headers["Content-Type"] = "application/json;charset=UTF-8"
                _LOGGER.debug(f"[{caller}] Calling POST {url}")
                response = self.s.post(url,data,timeout=30, allow_redirects=allowRedirects)
            elif method == HttpMethod.PATCH:
                self.s.headers["Content-Type"] = "application/json;charset=UTF-8"
                _LOGGER.debug(f"[{caller}] Calling PATCH {url}")
                response = self.s.patch(url,json=data,timeout=60, allow_redirects=allowRedirects)
            elif method == HttpMethod.OPTIONS:
                self.s.headers["Content-Type"] = "application/json;charset=UTF-8"
                _LOGGER.debug(f"[{caller}] Calling OPTIONS {url}")
                response = self.s.options(url,timeout=60, allow_redirects=allowRedirects)
            _LOGGER.debug(f"[{caller}] http status code = {response.status_code} (expecting {expectedStatusCode})")
            if printResponse:
                _LOGGER.debug(f"[{caller}] Response: {response.text}")
            if expectedStatusCode is not None and response.status_code != expectedStatusCode:
                raise requests.HTTPError(
                    f"Unexpected HTTP status {response.status_code}; expected {expectedStatusCode}",
                    response=response,
                )
        except requests.RequestException as err:
            _LOGGER.error("[%s] HTTP %s request failed (%s)", caller, method.value, type(err).__name__)
            raise
        return response

    
    @sleep_and_retry
    @limits(calls=1, period=5)
    def login(self, username, password):
        _LOGGER.info(f"Trying to login to My {self.provider}")

        # Check maintenance — skip for BASE if endpoint doesn't exist
        try:
            maintenance = self.callTelenet(url=self.cfg["maintenance_url"], caller="login").json()
            assert not maintenance["enabled"]
        except (KeyError, AssertionError):
            _LOGGER.debug(f"[login] Maintenance check skipped for {self.provider}")
        except Exception as e:
            _LOGGER.warning(f"[login] Maintenance check failed for {self.provider}: {e}")

        response = self.callTelenet(url=f"{self.api_url}/ocapi/oauth/userdetails", caller="login", expectedStatusCode=None)
        if response.status_code == 200:
            # Return if already authenticated
            return
        assert response.status_code == 401
        _LOGGER.debug(f"Login response to split state, nonce: {response.text} - ({response.status_code}) - {response.headers}")
        _state, _nonce = response.text.split(",", maxsplit=2)

        # Fetch the initial state token
        # BASE returns 200 directly (follows redirect internally), Telenet returns 302
        state_token_response = self.callTelenet(
            url=self.cfg["authorization_url"],
            caller="login",
            expectedStatusCode=None,
            allowRedirects=True
        )
        _LOGGER.debug(f"Authorization URL status: {state_token_response.status_code}")
        assert state_token_response.status_code in [200, 302], f"Unexpected status {state_token_response.status_code}"
        _LOGGER.debug(f"State token response: ({state_token_response.status_code}) - {state_token_response.headers}")

        state_token_matcher = re.search('"stateToken":"(.*)","helpLinks"', state_token_response.text) 
        state_token_encoded = state_token_matcher.group(1)
        state_token_decoded = state_token_encoded.encode('latin1').decode('unicode_escape')
        _LOGGER.debug(f"Initial state token {state_token_decoded}")
        
        introspection_response = self.callTelenet(
            url=f"{self.secure_url}/idp/idx/introspect",
            method=HttpMethod.POST,
            data=json.dumps({"stateToken": state_token_decoded}),
            caller="login"
        )
        state_handle = introspection_response.json()['stateHandle']

        self.callTelenet(url=f"{self.secure_url}/auth/services/devicefingerprint", caller="login")
        self.callTelenet(url=f"{self.secure_url}/api/v1/internal/device/nonce", method=HttpMethod.POST, caller="login")
        identify_response = self.callTelenet(
            url=f"{self.secure_url}/idp/idx/identify", 
            data=json.dumps({'identifier': username, 'stateHandle': state_handle}),
            method=HttpMethod.POST,
            caller="login"
        )
        state_handle = identify_response.json()['stateHandle']

        password_id = None
        for auth in identify_response.json()["authenticators"]["value"]:
            if auth.get("type") == "password":
                password_id = auth.get("id")
                break

        challenge_response = self.callTelenet(
            url=f"{self.secure_url}/idp/idx/challenge",
            data=json.dumps({
                "authenticator": {"id": password_id},
                "stateHandle": state_handle
            }),
            method=HttpMethod.POST,
            caller="login"
        )
        state_handle = challenge_response.json()['stateHandle']

        answer_response = self.callTelenet(
            url=f"{self.secure_url}/idp/idx/challenge/answer",
            data=json.dumps({
                "credentials": {"passcode": password},
                "stateHandle": state_handle
            }),
            method=HttpMethod.POST,
            caller="login"
        )
        _LOGGER.debug(f"Answer response: {answer_response.text}")

        self.callTelenet(url=answer_response.json()["success"]["href"], caller="login")


    def userdetails(self):
        response = self.callTelenet(f"{self.api_url}/ocapi/oauth/userdetails", "userdetails", None)
        return response.json()
    
    def customerdetails(self):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/customer-service/v1/customers", "customerdetails")
        return response.json()

    def telemeter(self):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/?p=internetusage,internetusagereminder", "telemeter")
        return response.json()

    def telemeter_product_details(self, url):
        response = self.callTelenet(url, "telemeter_product_details")
        return response.json()

    def modemdetails(self, productIdentifier):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/resource-service/v1/modems?productIdentifier={productIdentifier}", "modemdetails")
        return response.json()
    
    def wifidetails(self, productIdentifier, modemMac):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/resource-service/v1/modems/{modemMac}/wireless-settings?withmetadata=true&withwirelessservice=true&productidentifier={productIdentifier}", "wifidetails")
        return response.json()
    
    def wifiStatus(self, productIdentifier, modemMac):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/resource-service/v1/modems/{modemMac}/wireless-status?productidentifier={productIdentifier}", "wifiStatus")
        return response.json()
    
    def urldetails(self, url):
        response = self.callTelenet(url, "urldetails")
        return response.json()
    
    def switchWifi(self, wirelessEnabled: bool, productIdentifier: bool, modemMac, locationId):
        if wirelessEnabled:
            data = {"cos": "WSO_SHARING"}
        else:
            data = {"cos": "WSO_OFF"}
        self.callTelenet(f"{self.api_url}/ocapi/public/api/resource-service/v1/modems/{modemMac}/wireless-status", "patchwifi", 200, data, True, HttpMethod.PATCH)
        return
    
    def reboot(self, modemMac):
        self.callTelenet(f"{self.api_url}/ocapi/public/api/resource-service/v1/modems/{modemMac}/reboot", "modem_general reboot", 200, None, True, HttpMethod.POST)
        return
    
    def mobile(self):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/?p=mobileusage", "mobile")
        return response.json()
        
    def planInfo(self):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/product-service/v1/product-subscriptions?producttypes=PLAN", "planInfo")
        return response.json()
    
    def billCycles(self, productType, productIdentifier):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/billing-service/v1/account/products/{productIdentifier}/billcycle-details?producttype={productType}&count=3", "billCycles")
        return response.json()
    
    def productUsage(self, productType, productIdentifier, startDate, endDate):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/product-service/v1/products/{productType}/{productIdentifier}/usage?fromDate={startDate}&toDate={endDate}", "productUsage")
        return response.json()
    
    def productDailyUsage(self, productType, productIdentifier, startDate, endDate):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/product-service/v1/products/{productType}/{productIdentifier}/dailyusage?billcycle=CURRENT&fromDate={startDate}&toDate={endDate}", "productUsage")
        return response.json()

    def productSubscriptions(self, productType):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/product-service/v1/product-subscriptions?producttypes={productType}", "productSubscriptions")
        return response.json()

    def productService(self, productIdentifier, productType):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/product-service/v1/products/{productIdentifier}?producttype={productType.lower()}", "productService")
        return response.json()

    def mobileUsage(self, productIdentifier):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/mobile-service/v3/mobilesubscriptions/{productIdentifier}/usages", "mobileUsage")
        return response.json()

    def mobileBundleUsage(self, bundleIdentifier, lineIdentifier=None):
        if lineIdentifier is not None:
            response = self.callTelenet(f"{self.api_url}/ocapi/public/api/mobile-service/v3/mobilesubscriptions/{bundleIdentifier}/usages?type=bundle&lineIdentifier={lineIdentifier}", "mobileBundleUsage lineIdentifier")
        else:
            response = self.callTelenet(f"{self.api_url}/ocapi/public/api/mobile-service/v3/mobilesubscriptions/{bundleIdentifier}/usages?type=bundle", "mobileBundleUsage bundle")
        return response.json()

    def apiVersion2(self):
        response = self.callTelenet(f"{self.api_url}/ocapi/public/api/product-service/v1/product-subscriptions?producttypes=PLAN", "apiVersion2", None)
        if response.status_code == 200:
            return True
        return False
