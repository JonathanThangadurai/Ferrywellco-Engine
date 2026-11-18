"""KNMI field traps and the end-of-hour convention, on a saved Schiphol day (16 Nov 2026)."""

import json

import pandas as pd

from gotime_data.convert import jcm2_per_hour_to_wm2
from gotime_data.schema import validate
from gotime_data.sources import knmi

from .conftest import FIXTURES

RAW = json.loads(next((FIXTURES / "knmi_hourly").glob("*/amsterdam.json")).read_text())
ROWS, SITE = knmi.coverage_to_rows(RAW, "amsterdam")


def series(var):
    return ROWS[ROWS["variable"] == var].set_index("valid_time_utc")["value"]


def raw(param):
    cov = RAW["coverages"][0]
    t = pd.to_datetime(cov["domain"]["axes"]["t"]["values"], utc=True)
    return pd.Series(cov["ranges"][param]["values"], index=t, dtype="float64")


def test_rh_is_precipitation_not_humidity():
    assert knmi.FIELDS["RH"][0] == "precip_mm"
    assert knmi.FIELDS["U"][0] == "rh2m_pct"
    # humidity values come from U (around 90 % on a wet November day), not from RH (mm)
    assert series("rh2m_pct").min() > 50
    assert (series("rh2m_pct").to_numpy() == raw("U").to_numpy()).all()
    assert series("precip_mm").max() < 20


def test_radiation_is_converted_from_j_per_cm2():
    assert jcm2_per_hour_to_wm2(36.0) == 100.0  # 36 J/cm2 in one hour = 100 W/m2
    q = raw("Q")
    ssrd = series("ssrd_wm2")
    assert ssrd.max() == jcm2_per_hour_to_wm2(q.max())


def test_temperature_is_marked_as_1p5_m():
    t = ROWS[ROWS["variable"] == "t2m_c"]
    assert (t["method"] == "KNMI 1.5 m").all()
    assert "1.5 m" in knmi.__doc__


def test_hourly_sums_are_end_of_hour_and_instants_are_not_shifted():
    """Q stamped 16:00 covers 15:00-16:00 -> stored at valid_time 15:00. T stamped 16:00 stays at 16:00."""
    q, t = raw("Q"), raw("T")
    stamp = pd.Timestamp("2026-11-16 16:00", tz="UTC")
    assert series("ssrd_wm2")[stamp - pd.Timedelta(hours=1)] == jcm2_per_hour_to_wm2(q[stamp])
    assert series("t2m_c")[stamp] == t[stamp]
    # after sunset (~15:40 UTC) the hour 16:00-17:00 has no sun
    assert series("ssrd_wm2")[stamp] == 0


def test_rows_are_valid_observations_with_site():
    assert validate(ROWS) == []
    assert ROWS["lead_h"].isna().all()
    assert SITE[0] == "knmi:06240" and SITE[4] == "weather"
