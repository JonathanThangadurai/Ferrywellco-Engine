"""Helpers for the API and engine tests: a synthetic forecast store and an OpenAPI contract checker.

The forecast values are synthetic (smooth daily curves); they only need to vary by hour.
"""

import math
from pathlib import Path

import pandas as pd
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from gotime_data.schema import SITE_COLUMNS, Batch, normalise
from gotime_data.store import write_batch

SPEC_FILE = Path(__file__).resolve().parent.parent / "docs" / "openapi.yaml"
CELLS = {"amsterdam": (52.4, 4.9), "chennai": (13.1, 80.2)}
AIR = {"amsterdam": "cams_eu", "chennai": "cams_global"}


def write_forecast(issue: str, hours: int = 97, locations=("amsterdam", "chennai")) -> None:
    """One complete synthetic cycle per location: ECMWF t2m_c, CAMS pm25_ugm3 (+ birch pollen, Amsterdam)."""
    issue_t = pd.Timestamp(issue, tz="UTC")
    t = pd.date_range(issue_t, periods=hours, freq="h")
    for loc in locations:
        series = {
            ("ecmwf_ifs", "t2m_c"): [10 + 5 * math.sin(2 * math.pi * (x.hour - 9) / 24) for x in t],
            (AIR[loc], "pm25_ugm3"): [12 + 6 * math.cos(2 * math.pi * x.hour / 24) for x in t],
        }
        if loc == "amsterdam":
            series[("cams_eu", "pollen_birch_gm3")] = [float(x.hour % 6) for x in t]
        rows, cycles = [], []
        for (source, var), values in series.items():
            rows.append(
                pd.DataFrame(
                    {
                        "location_id": loc,
                        "valid_time_utc": t,
                        "variable": var,
                        "value": values,
                        "source": source,
                        "kind": "forecast",
                        "issue_time_utc": issue_t,
                        "lead_h": range(hours),
                        "status": "ok",
                        "site_id": f"grid:{source}:{loc}",
                    }
                )
            )
            cycles.append(
                {
                    "source": source,
                    "location_id": loc,
                    "variable": var,
                    "issue_time_utc": issue_t,
                    "publish_time_utc": issue_t + pd.Timedelta(hours=9),
                    "fetched_time_utc": issue_t + pd.Timedelta(hours=10),
                    "n_expected": hours,
                    "n_missing": 0,
                    "complete": True,
                }
            )
        lat, lon = CELLS[loc]
        sites = pd.DataFrame(
            [[f"grid:ecmwf_ifs:{loc}", "nearest land cell", lat, lon, "grid", "ecmwf_ifs"]],
            columns=SITE_COLUMNS,
        )
        batch = Batch(normalise(pd.concat(rows, ignore_index=True)), sites=sites, cycles=pd.DataFrame(cycles))
        write_batch(batch, f"{issue_t:%Y%m%d%H}_{loc}")


class Contract:
    """Validates responses against docs/openapi.yaml (status listed, content type, body schema, headers)."""

    def __init__(self):
        self.spec = yaml.safe_load(SPEC_FILE.read_text())
        self.registry = Registry().with_resource("urn:spec", Resource(self.spec, specification=DRAFT202012))
        self.checked = 0

    def _validator(self, pointer: str) -> Draft202012Validator:
        return Draft202012Validator(
            {"$ref": f"urn:spec#{pointer}"}, registry=self.registry, format_checker=FormatChecker()
        )

    def _deref(self, obj: dict) -> tuple[dict, str | None]:
        ref = obj.get("$ref")
        if not ref:
            return obj, None
        node = self.spec
        for part in ref.removeprefix("#/").split("/"):
            node = node[part]
        return node, ref.removeprefix("#")

    def check(self, method: str, path: str, response) -> None:
        status = response.status_code
        assert response.headers.get("x-request-id"), "every response carries X-Request-ID"
        op = self.spec["paths"].get(path.removeprefix("/v1"), {}).get(method.lower())
        if op is None:  # unknown path or method: still a problem+json
            assert response.headers["content-type"].startswith("application/problem+json")
            self._validator("/components/schemas/Problem").validate(response.json())
            self.checked += 1
            return
        assert str(status) in op["responses"], f"{method} {path}: status {status} not in the spec"
        if status == 304:
            assert not response.content
            self.checked += 1
            return
        resp, ref = self._deref(op["responses"][str(status)])
        base = (
            ref or f"/paths/{path.removeprefix('/v1').replace('/', '~1')}/{method.lower()}/responses/{status}"
        )
        ctype = response.headers["content-type"].split(";")[0]
        assert ctype in resp["content"], f"{method} {path} {status}: content type {ctype} not in the spec"
        self._validator(f"{base}/content/{ctype.replace('/', '~1')}/schema").validate(response.json())
        for name in resp.get("headers", {}):
            if name == "Retry-After":
                assert response.headers.get("retry-after"), f"{status} needs Retry-After"
        self.checked += 1


class ContractClient(TestClient):
    """A TestClient that checks every response against the OpenAPI contract."""

    def __init__(self, app, contract: Contract, **kw):
        super().__init__(app, **kw)
        self.contract = contract

    def request(self, method, url, *args, **kwargs):
        response = super().request(method, url, *args, **kwargs)
        self.contract.check(method, str(url).split("?")[0], response)
        return response
