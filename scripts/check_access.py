"""Make one small request per data source and print OK / FAILED and why.

Keys are read with gotime_data.keys and never printed; error texts are scrubbed of key values.
Run: uv run python scripts/check_access.py
"""

import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gotime_data import keys  # noqa: E402

AMS_AREA = [52.9, 4.3, 51.9, 5.5]  # N, W, S, E (ADS/CDS order)
CHN_AREA = [13.6, 79.7, 12.6, 80.8]
TIMEOUT = httpx.Timeout(60.0)


def scrub(text: str) -> str:
    """Remove any key value from a message before printing it."""
    for name in keys.KEY_NAMES:
        value = os.environ.get(name)
        if value and name.endswith("KEY"):
            text = text.replace(value, f"<{name}>")
    return text[:300]


def explain_status(code: int) -> str:
    """Turn 401/403 into a hint: key problem or licence not accepted."""
    if code == 401:
        return "401: key missing or wrong"
    if code == 403:
        return "403: key valid but no access (licence not accepted or dataset not allowed)"
    if code == 429:
        return "429: rate limited"
    return f"HTTP {code}"


def check_ecmwf_aws() -> str:
    """Latest 00z index file on the AWS mirror (no key)."""
    for days_back in range(0, 3):
        day = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y%m%d")
        url = (
            f"https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/{day}/00z/ifs/0p25/oper/"
            f"{day}000000-0h-oper-fc.index"
        )
        r = httpx.head(url, timeout=TIMEOUT)
        if r.status_code == 200:
            return f"index for {day} 00z found"
    raise RuntimeError("no 00z index in the last 3 days")


CDS_MAX_WAIT_S = 180


def _cds_retrieve(client_url: str, dataset: str, request: dict) -> str:
    """Submit a tiny request and wait at most CDS_MAX_WAIT_S.

    A wrong key (401) or a licence not accepted (403) fails at submit time, so a job that is
    accepted but still queued already proves access; we report that instead of blocking for hours.
    """
    import cdsapi

    client = cdsapi.Client(
        url=client_url,
        key=keys.get("COPERNICUS_KEY"),
        quiet=True,
        progress=False,
        wait_until_complete=False,
    )
    remote = client.retrieve(dataset, request)
    deadline = time.monotonic() + CDS_MAX_WAIT_S
    while time.monotonic() < deadline:
        status = remote.status
        if status == "successful":
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "check.nc"
                remote.download(str(target))
                return f"{target.stat().st_size} bytes"
        if status == "failed":
            raise RuntimeError("job failed on the server")
        time.sleep(5)
    return f"submitted and accepted (key + licence OK), still '{remote.status}' after {CDS_MAX_WAIT_S}s"


def check_cams_europe() -> str:
    day = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    return _cds_retrieve(
        keys.get("ADS_URL"),
        "cams-europe-air-quality-forecasts",
        {
            "variable": ["particulate_matter_2.5um"],
            "model": ["ensemble"],
            "level": ["0"],
            "date": [f"{day}/{day}"],
            "type": ["forecast"],
            "time": ["00:00"],
            "leadtime_hour": ["0"],
            "data_format": "netcdf",
            "area": AMS_AREA,
        },
    )


def check_cams_global() -> str:
    day = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    return _cds_retrieve(
        keys.get("ADS_URL"),
        "cams-global-atmospheric-composition-forecasts",
        {
            "variable": ["particulate_matter_2.5um"],
            "date": [f"{day}/{day}"],
            "time": ["00:00"],
            "leadtime_hour": ["0"],
            "type": ["forecast"],
            "data_format": "netcdf",
            "area": CHN_AREA,
        },
    )


def check_era5() -> str:
    day = datetime.now(UTC) - timedelta(days=10)
    return _cds_retrieve(
        keys.get("CDS_URL"),
        "reanalysis-era5-single-levels",
        {
            "product_type": ["reanalysis"],
            "variable": ["2m_temperature"],
            "year": [day.strftime("%Y")],
            "month": [day.strftime("%m")],
            "day": [day.strftime("%d")],
            "time": ["12:00"],
            "data_format": "netcdf",
            "download_format": "unarchived",
            "area": AMS_AREA,
        },
    )


def check_eea() -> str:
    url = "https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls"
    body = {
        "countries": ["NL"],
        "cities": ["Greater Amsterdam"],
        "pollutants": ["http://dd.eionet.europa.eu/vocabulary/aq/pollutant/8"],
        "dataset": 1,
        "aggregationType": "hour",
    }
    r = httpx.post(url, json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(explain_status(r.status_code))
    files = [line for line in r.text.splitlines() if line.startswith("http")]
    if not files:
        raise RuntimeError("no files listed")
    return f"{len(files)} NO2 sampling points in Greater Amsterdam"


def _knmi_get(url: str, key_name: str, params: dict | None = None) -> httpx.Response:
    r = httpx.get(url, headers={"Authorization": keys.get(key_name)}, params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(explain_status(r.status_code))
    return r


def check_knmi_edr() -> str:
    base = "https://api.dataplatform.knmi.nl/edr/v1/collections"
    r = _knmi_get(f"{base}/hourly-in-situ-meteorological-observations/locations", "KNMI_EDR_API_KEY")
    n = len(r.json().get("features", []))
    return f"hourly collection: {n} stations"


def check_knmi_open_data() -> str:
    url = (
        "https://api.dataplatform.knmi.nl/open-data/v1/datasets/"
        "10-minute-in-situ-meteorological-observations/versions/1.0/files"
    )
    r = _knmi_get(url, "KNMI_API_KEY", params={"maxKeys": 1, "orderBy": "created", "sorting": "desc"})
    files = r.json().get("files", [])
    return f"latest file {files[0]['filename']}" if files else "no files"


def check_luchtmeetnet() -> str:
    """Last 3 hours at Amsterdam-Einsteinweg. Without start/end the API answers 302 to a
    default 7-day window, so we always pass both."""
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    r = httpx.get(
        "https://api.luchtmeetnet.nl/open_api/measurements",
        params={
            "station_number": "NL49007",
            "formula": "NO2",
            "start": (end - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(explain_status(r.status_code))
    return f"{len(r.json()['data'])} rows"


def check_openaq() -> str:
    """OpenAQ v3 needs a free key (X-API-Key). We only test reachability here."""
    r = httpx.get(
        "https://api.openaq.org/v3/locations",
        params={"coordinates": "13.08,80.27", "radius": 25000, "limit": 1},
        headers={"X-API-Key": os.environ["OPENAQ_API_KEY"]} if os.environ.get("OPENAQ_API_KEY") else {},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        hint = "" if os.environ.get("OPENAQ_API_KEY") else " (OPENAQ_API_KEY not set)"
        raise RuntimeError(explain_status(r.status_code) + hint)
    return f"{r.json()['meta'].get('found')} locations within 25 km"


CHECKS = [
    ("ECMWF open data (AWS)", check_ecmwf_aws),
    ("CAMS Europe (ADS)", check_cams_europe),
    ("CAMS global (ADS)", check_cams_global),
    ("ERA5 (CDS)", check_era5),
    ("EEA E2a", check_eea),
    ("KNMI EDR", check_knmi_edr),
    ("KNMI Open Data API", check_knmi_open_data),
    ("Luchtmeetnet (fallback)", check_luchtmeetnet),
    ("OpenAQ (Chennai, not used yet)", check_openaq),
]


def main() -> int:
    for name, present in keys.presence().items():
        print(f"{name} set: {'yes' if present else 'no'}")
    print()
    failures = 0
    for label, check in CHECKS:
        start = time.monotonic()
        try:
            detail = check()
            status = "OK"
        except Exception as exc:  # report every failure and keep going
            detail = scrub(f"{type(exc).__name__}: {exc}")
            status = "FAILED"
            failures += 1
        print(f"{status:7} {label:32} {time.monotonic() - start:5.1f}s  {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
