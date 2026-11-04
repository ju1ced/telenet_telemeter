"""Shared pytest configuration."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    """Let Home Assistant load this custom integration in tests that use `hass`.

    Only tests that request `hass` get it, so the plain unit tests stay free of
    a Home Assistant instance.
    """
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")
    yield
