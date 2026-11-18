"""Schema validation: the rules every stored row must follow."""

import pandas as pd

from gotime_data.schema import normalise, validate


def good_rows() -> pd.DataFrame:
    t = pd.date_range("2026-11-16 00:00", periods=3, freq="h", tz="UTC")
    return normalise(
        pd.DataFrame(
            {
                "location_id": "amsterdam",
                "valid_time_utc": t,
                "variable": "t2m_c",
                "value": [5.0, None, 6.0],
                "source": "ecmwf_ifs",
                "kind": "forecast",
                "issue_time_utc": pd.Timestamp("2026-11-16 00:00", tz="UTC"),
                "lead_h": [0, 1, 2],
                "status": ["ok", "missing", "interpolated"],
            }
        )
    )


def test_valid_rows_pass():
    assert validate(good_rows()) == []


def test_missing_value_must_be_null_with_status_missing():
    df = good_rows()
    df.loc[1, "status"] = "ok"
    assert any("null value" in p for p in validate(df))
    df = good_rows()
    df.loc[1, "value"] = 0.0  # 0 standing in for missing is not allowed
    assert any("status 'missing' with a value" in p for p in validate(df))


def test_unknown_variable_source_and_status_are_rejected():
    df = good_rows()
    df.loc[0, "variable"] = "temp"
    df.loc[1, "source"] = "somewhere"
    df.loc[2, "status"] = "estimated"
    problems = " ".join(validate(df))
    assert "variable" in problems and "source" in problems and "status" in problems


def test_forecast_needs_lead_and_observation_must_not_have_one():
    df = good_rows()
    df["lead_h"] = pd.array([None, None, None], dtype="Int64")
    assert any("lead_h" in p for p in validate(df))
    df = good_rows().assign(kind="observation")
    assert any("observation rows" in p for p in validate(df))


def test_duplicate_keys_are_rejected():
    df = good_rows()
    df = normalise(pd.concat([df, df.iloc[[0]]]))
    assert any("duplicate" in p for p in validate(df))


def test_times_are_utc_aware():
    df = good_rows()
    assert str(df["valid_time_utc"].dt.tz) == "UTC"
    assert validate(df.assign(valid_time_utc=df["valid_time_utc"].dt.tz_convert("Europe/Amsterdam")))
