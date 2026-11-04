"""Characterization tests for the internet usage calculations in sensor.py.

The fixtures in tests/fixtures/ mimic the API data that ComponentData stores
(v1 `telemeter()` and v2 productUsage/dailyUsage), without credentials. They
pin today's state and attributes so TM-10/TM-11 can refactor safely.
Expected values are worked out by hand from the fixtures.

Known defects are recorded as strict xfails: fixing one makes its test pass,
which fails the run until the xfail marker is removed on purpose.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from freezegun import freeze_time

from custom_components.telenet_telemeter import sensor

FIXTURES = Path(__file__).parent / "fixtures"
MB = 1024 * 1024


def data_for(name):
    raw = json.loads((FIXTURES / name).read_text())

    async def update():
        return None

    return SimpleNamespace(_v2=raw["v2"], _telemeter=raw["telemeter"],
                           _product_details=raw["product_details"], update=update)


async def updated(entity_class, fixture, at="2026-11-04 12:00:00"):
    entity = entity_class(data_for(fixture), None)
    with freeze_time(at):
        await entity.async_update()
    return entity


# --- SensorInternet: state and attributes ------------------------------------

async def test_v2_fup_usage_is_peak_against_service_limit():
    entity = await updated(sensor.SensorInternet, "internet_v2_fup.json")
    attrs = entity.extra_state_attributes
    assert entity.state == 40.0                       # 300.0 GB of 750 GB
    assert attrs["included_volume"] == 750 * MB
    assert attrs["total_volume"] == 750.0
    assert attrs["peak_usage"] == 300.0               # 300.04 rounded to 1 decimal
    assert attrs["offpeak_usage"] == 120.1            # 120.06 rounded
    assert attrs["includedvolume_usage"] == 300.0
    assert attrs["extendedvolume_usage"] == 0
    assert attrs["squeezed"] is False
    assert attrs["product"] == "One Fiber"
    assert attrs["download_speed"] == "1000 Mbps"
    assert attrs["upload_speed"] == "100 Mbps"
    assert attrs["modemMac"] == "00:11:22:33:44:55"
    assert attrs["wifiEnabled"] is True
    assert entity.unit_of_measurement == "%"


async def test_v2_period_is_inclusive_of_end_date():
    # 2026-10-15 .. 2026-11-14 inclusive = 31 days; frozen at 2026-11-04 12:00.
    attrs = (await updated(sensor.SensorInternet, "internet_v2_fup.json")).extra_state_attributes
    assert attrs["period_end"].isoformat() == "2026-11-15T00:00:00"
    assert attrs["period_days_left"] == 10.5
    assert attrs["period_used_percentage"] == 66.1     # 20.5 of 31 days


async def test_v2_cap_usage_is_units_against_usage_limit():
    entity = await updated(sensor.SensorInternet, "internet_v2_cap.json")
    attrs = entity.extra_state_attributes
    assert entity.state == 50.0                       # 75 GB of 150 GB
    assert attrs["included_volume"] == 150 * MB       # from elementarycharacteristics
    assert attrs["total_volume"] == 150.0
    assert attrs["includedvolume_usage"] == 75
    assert attrs["peak_usage"] is None
    assert attrs["squeezed"] is False


async def test_v2_cap_over_limit_is_squeezed():
    entity = await updated(sensor.SensorInternet, "internet_v2_cap_over_limit.json")
    assert entity.state == 106.67                     # 160 / 150
    assert entity.extra_state_attributes["squeezed"] is True


async def test_v1_fup_usage_and_period_with_timezone():
    entity = await updated(sensor.SensorInternet, "internet_v1_fup.json")
    attrs = entity.extra_state_attributes
    assert entity.state == 50.0                       # 375 GB of 750 GB, in KB
    assert attrs["peak_usage"] == 393216000
    assert attrs["offpeak_usage"] == 104857600
    assert attrs["product"] == "FIBER_1000"
    assert attrs["last update"] == "2026-11-04T08:00:00.0+0100"
    # Frozen 12:00 UTC is 13:00 at +01:00: 20 days 13 h used of 31 days.
    assert attrs["period_days_left"] == 10.46
    assert attrs["period_used_percentage"] == 66.3


async def test_v1_cap_usage():
    entity = await updated(sensor.SensorInternet, "internet_v1_cap.json")
    assert entity.state == 75.0                       # 112.5 GB of 150 GB, in KB
    assert entity.extra_state_attributes["includedvolume_usage"] == 117964800


async def test_unique_id_uses_product_identifier():
    entity = await updated(sensor.SensorInternet, "internet_v2_fup.json")
    assert entity.unique_id == "Telenet Telemeter internet INT-0001"


# --- SensorPeak: peak hours -------------------------------------------------

@pytest.mark.parametrize("fixture, at, peak", [
    ("internet_v2_fup.json", "2026-11-04 16:59:59", False),   # FUP peak: 17:00-24:00
    ("internet_v2_fup.json", "2026-11-04 17:00:00", True),
    ("internet_v2_fup.json", "2026-11-04 23:59:59", True),
    ("internet_v2_fup.json", "2026-11-04 00:30:00", False),
    ("internet_v2_cap.json", "2026-11-04 09:59:59", False),   # CAP peak: 10:00-24:00
    ("internet_v2_cap.json", "2026-11-04 10:00:00", True),
    ("internet_v1_fup.json", "2026-11-04 18:00:00", True),
])
async def test_peak_hours(fixture, at, peak):
    assert (await updated(sensor.SensorPeak, fixture, at)).is_on is peak


async def test_v1_peak_sensor_matches_internet_sensor():
    peak = await updated(sensor.SensorPeak, "internet_v1_fup.json")
    attrs = peak.extra_state_attributes
    assert attrs["used_percentage"] == 50.0
    assert attrs["download_speed"] == "1000 Mbps"


# --- Known defects (strict xfail) ------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "SensorPeak divides v2 usage (GB) by included_volume in MB*1024 units "
    "without converting, unlike SensorInternet; its used_percentage is ~0"))
@pytest.mark.parametrize("fixture", ["internet_v2_fup.json", "internet_v2_cap.json"])
async def test_v2_peak_sensor_used_percentage_matches_internet_sensor(fixture):
    internet = await updated(sensor.SensorInternet, fixture)
    peak = await updated(sensor.SensorPeak, fixture)
    assert peak.extra_state_attributes["used_percentage"] == internet.state


@pytest.mark.xfail(strict=True, reason=(
    "Same unit mismatch: an over-limit v2 CAP account never gets the "
    "squeezed 1 Mbps / 256 Kbps speeds on the peak sensor"))
async def test_v2_cap_over_limit_peak_sensor_shows_squeezed_speeds():
    attrs = (await updated(sensor.SensorPeak, "internet_v2_cap_over_limit.json")).extra_state_attributes
    assert attrs["squeezed"] is True
    assert attrs["download_speed"] == "1 Mbps"


@pytest.mark.xfail(strict=True, reason=(
    "FUP branch reads 'squeezed' from the v1 API and then overwrites it with "
    "`not self._data._telemeter and ...`, which is always False"))
async def test_v1_fup_squeezed_flag_from_api_is_kept():
    entity = await updated(sensor.SensorInternet, "internet_v1_fup.json")
    assert entity.extra_state_attributes["squeezed"] is True


@pytest.mark.xfail(strict=True, reason=(
    "ComponentData.clear_session uses `self._session : None`, an annotation, "
    "so the session is never cleared"))
def test_clear_session_drops_session():
    data = sensor.ComponentData("u", "p", True, False, None, None)
    data.clear_session()
    assert data._session is None
