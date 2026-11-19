# GOTime API: partner guide (sandbox, version 0.1.0)

> **Draft.** Request and response fields are still to agree with the partner (`openapi.yaml`, questions A1–A6 in
> `open-questions.md`). In the sandbox every answer is a **synthetic example, not guidance**: `mode` is
> `sandbox` and every guidance reason starts with `sandbox_`.

GOTime gives wellness guidance on when and how to be active outside. It is not a health service.

## Access

- **Base URL:** `https://<api domain>/v1` (HTTPS only).
- **Key:** one API key per partner, sent in a header. Keep it on your server; do not ship it inside a
  mobile app.
  ```
  Authorization: Bearer gt_<key_id>_<secret>
  ```
- **Mode:** a sandbox key always gets sandbox answers. Live keys come once the guidance engine is ready.
- **Rate limit:** 60 requests per minute per key, short bursts up to 20. Above that: `429` with `Retry-After`.
- **Request id:** send `X-Request-ID` (letters, digits, `-`, `_`, up to 64) to trace a call; otherwise we
  create one. It comes back in every response; quote it when you contact us.

## Privacy

- Send only what the request lists. Unknown fields are rejected, so no names, ids or e-mail addresses can
  come in by accident.
- Coordinates are rounded to 2 decimals (~1 km) as soon as they arrive. They are never logged or stored.
  You may round them yourself before sending.

## One request

```
POST /v1/guidance
Content-Type: application/json
Authorization: Bearer gt_...

{
  "location": {"lat": 52.37, "lon": 4.90},
  "date": "2026-11-19",
  "earliest_start": "07:00",
  "latest_start": "19:00",
  "activity_type": "run",
  "duration_min": 45,
  "hay_fever": "yes"
}
```

- `date`, `earliest_start` and `latest_start` are local to the place (today to today + 3). The window
  always fits the whole activity.
- `activity_type` is one of `walk`, `run`, `cycle` or `outdoor_workout`.
- `heat_adapted` and `hay_fever` are optional: `yes`, `no` or `unknown` (default).

Response (sandbox, shortened):

```json
{
  "mode": "sandbox",
  "request_id": "6c1f...",
  "snapshot_id": "20261119T00Z-2829b8d8",
  "location": {"id": "ecmwf0p1_n52.40_e4.90", "alias": "amsterdam", "timezone": "Europe/Amsterdam", "distance_km": 3.3},
  "date": "2026-11-19",
  "window": {"status": "found", "start": "2026-11-19T07:00:00+01:00", "end": "2026-11-19T07:45:00+01:00",
             "latest_start": "2026-11-19T10:15:00+01:00"},
  "intensity_hint": {"status": "given", "code": "as_planned"},
  "indoor_alternative": {"status": "not_suggested"},
  "reasons": [{"code": "sandbox_example", "params": {}, "source": "sandbox", "text": "Sandbox example, not guidance."}],
  "data": {
    "status": "ok",
    "sources": [{"source": "ecmwf_ifs", "required": true, "status": "ok", "issue_time": "2026-11-19T00:00:00Z", "forecast_age_h": 10.0},
                {"source": "cams_eu", "required": true, "status": "ok", "issue_time": "2026-11-19T00:00:00Z", "forecast_age_h": 10.0}],
    "attribution": ["Contains modified ECMWF open data (CC BY 4.0)",
                    "Generated using modified Copernicus Atmosphere Monitoring Service information (CC BY 4.0)"]
  }
}
```

How to read the response:
- Every optional part has an explicit `status`. Read it instead of testing for missing fields:
  - `window.status` is `found` or `none`. With `none`, nothing fits the options; that is a normal 200 answer.
  - `intensity_hint.status` is `given` or `none`.
- Times are local, with the offset of that moment. Daylight-saving days are handled.
- `data.status` is `ok` or `degraded`. `degraded` means the forecast is older than usual, or a data check
  failed; the answer is still given.
- A reason with source `data_layer` is a note about the data, e.g. `pollen_not_assessed`: there is no
  pollen forecast for that place.
- Codes are stable; texts may change. Translate by `code`.
- `GET /v1/coverage` lists the supported places, their radius, time zone and available dates. Outside a
  radius: `422 not-covered`.

## Errors

Errors are `application/problem+json` (RFC 9457) with a stable `code`, and they never repeat your input.

| HTTP | code | what to do |
|---|---|---|
| 400 / 422 | `invalid-request` | fix the fields listed in `errors` (field and reason) |
| 422 | `not-covered` | the place is outside every supported area (see `/v1/coverage`) |
| 422 | `date-out-of-range` | use a date from today to today + 3 (local) |
| 422 | `beyond-forecast-horizon` | the activity would end after the forecast; choose an earlier time or date |
| 401 | `unauthorized` | missing, wrong or revoked key |
| 404 / 405 | `not-found` / `method-not-allowed` | wrong path or method |
| 413 | `payload-too-large` | the body is over 4 KB |
| 429 | `rate-limited` | wait `Retry-After` seconds |
| 500 | `engine-error` / `internal-error` | retry later; send us the `request_id` |
| 503 | `data-unavailable` | the forecast is too old to give guidance; retry after `Retry-After` |
| 503 | `snapshot-unavailable` | the service is starting or updating; retry after `Retry-After` |

## Caching

- Responses carry `ETag` and `Cache-Control: private, max-age=N`, where N is the time until the next
  forecast update (60–900 s).
- Send `If-None-Match` with the ETag to get `304 Not Modified` when nothing changed.

## Attribution (required)

The data comes from ECMWF and the Copernicus Atmosphere Monitoring Service under CC BY 4.0. That licence
requires visible credit.
- Show the strings in `data.attribution` to your users in a visible place, e.g. a "Data sources" screen
  in your app reachable from the guidance view.
- Keep the wording; you may add "via GOTime".
- The list can change per place (CAMS Europe for Amsterdam, CAMS global for Chennai). Take it from the
  response rather than hard-coding it.

## Postman

- **Collection:** `docs/postman/gotime.postman_collection.json`.
- **Environment template:** `docs/postman/gotime.postman_environment.json`. Set `baseUrl` and `apiKey`;
  leave `date` empty to use tomorrow.
- **What to run:** the **Sandbox** folder runs against the sandbox. The **Test environment only** folder
  (revoked key, degraded, unavailable) needs our test setup.
