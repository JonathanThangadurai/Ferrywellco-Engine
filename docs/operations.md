# Operations: running GOTime on the Hetzner server

One server (4 GB RAM, 40 GB disk, Ubuntu 24.04), four containers from `docker-compose.yml`:

| container | what it does | memory limit |
|---|---|---|
| `api` | `gotime-api serve`, 2 uvicorn workers, serves the snapshot from memory | 512 MB |
| `scheduler` | fetch + ingest each forecast cycle when published, data quality check, precompute; hourly observations; daily retention (03:15 UTC) and backup (03:45 UTC) | 1.5 GB |
| `monitor` | every 15 min: `/v1/ready` + one sandbox `/v1/guidance` with the monitor key | 256 MB |
| `caddy` | HTTPS (automatic certificates), reverse proxy for `/v1/*`, 8 KB request body limit | 128 MB |

That is about 2.4 GB in limits, leaving about 1.6 GB for the OS and file cache. The scheduler runs one job
at a time, so its peak is the ECMWF decode (~570 MB).

Secrets are never in the repo. They live in:
- `/etc/gotime/gotime.env` (chmod 600): source keys, heartbeat URLs, monitor key, domain.
- `/etc/gotime/keys/`: the hashed partner API keys.

## Schedule (UTC, from the measured publish delays)

Each job starts 10 min after the expected publish time and retries every 15 min until its deadline.

| forecast | run | published | first try | retried until |
|---|---|---|---|---|
| ECMWF 0.1° | 00 / 12 | + 9 h 34 | 09:44 / 21:44 | + 3 h |
| ECMWF 0.1° | 06 / 18 | + 8 h 27 | 14:37 / 02:37 | + 3 h |
| CAMS Europe | 00 | ~08:30 | 08:40 | + 3 h |
| CAMS global | 00 / 12 | not measured; asked at + 12 h | 12:10 / 00:10 | + 6 h |

When a cycle is complete, the scheduler:
1. Runs the data quality check (plausible ranges, completeness, missing cycles in the last 24 h).
2. Pings `HC_PING_INGEST` on success, or `/fail` with the reason.
3. Runs precompute and pings `HC_PING_PRECOMPUTE` (or `/fail`).

A source that fails its quality check is shown as `degraded` in API responses until a later cycle passes.

Observations (EEA, KNMI, OpenAQ) are fetched hourly at :25. They are for verification only: a failure is
logged, and the nightly source check in GitHub opens an issue.

CAMS global: check the `publish_time_utc` column in the cycles table after a week. Then set the real delay
in `gotime_ops/schedule.py`.

## First deployment, step by step

1. **Server:** create the Hetzner server (Ubuntu 24.04) with your SSH key. Log in as root.
2. **DNS:** point an A record (and AAAA for IPv6) for the API domain, e.g. `api.<your domain>`, at the server.
3. **Firewall:** allow only 22, 80 and 443, both in the Hetzner Cloud firewall and on the server:
   ```
   ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
   ```
   Disable SSH password login (`PasswordAuthentication no` in `/etc/ssh/sshd_config`).
4. **Docker:** install Docker Engine and the compose plugin from Docker's apt repository
   (docs.docker.com/engine/install/ubuntu).
5. **Code:** `git clone https://github.com/JonathanThangadurai/Ferrywellco-Engine.git /opt/gotime && cd /opt/gotime`.
6. **Folders** (uid 10001 is the user inside the containers):
   ```
   mkdir -p /etc/gotime/keys /srv/gotime/data /srv/gotime/backup
   chown 10001:10001 /etc/gotime/keys /srv/gotime/data /srv/gotime/backup
   chmod 700 /etc/gotime/keys
   ```
7. **Environment:** `cp deploy/gotime.env.example /etc/gotime/gotime.env && chmod 600 /etc/gotime/gotime.env`.
   Fill in the source keys, the three heartbeat URLs, `GOTIME_DOMAIN` and `ACME_EMAIL`.
8. **Heartbeats** (healthchecks.io), three checks:

   | check | period | grace |
   |---|---|---|
   | ingest | 8 h | 3 h |
   | precompute | 8 h | 3 h |
   | monitor | 15 min | 10 min |

   The longest normal gap between ECMWF cycles is about 7 h (14:37 to 21:44 UTC).
9. **Build:** `docker compose build`.
10. **First keys:** the monitor key and AEIVA's sandbox key. Each new key is written once to a file only root
    and uid 10001 can read:
    ```
    docker run --rm -v /etc/gotime/keys:/run/gotime/keys gotime:latest gotime-api keys create --partner monitor --mode sandbox
    docker run --rm -v /etc/gotime/keys:/run/gotime/keys gotime:latest gotime-api keys create --partner aeiva --mode sandbox
    ```
    - Put the monitor key into `GOTIME_MONITOR_KEY` in `/etc/gotime/gotime.env`.
    - Send AEIVA's key over a secure channel (not e-mail in plain text).
    - Then delete both `new_key_*.txt` files.
11. **Start:** `docker compose up -d`. The scheduler fetches the newest cycle of each forecast at once, then
    precomputes. Follow it with `docker compose logs -f scheduler`. `/v1/ready` turns 200 after the first
    precompute (a few minutes).
12. **Check:** `curl https://<domain>/v1/ready`. Then run the Postman collection with the partner environment
    (`docs/postman/`) and a sandbox key; the "Sandbox" folder must pass.

## Updating

```
cd /opt/gotime && git pull && docker compose build && docker compose up -d
```

The API reloads the snapshot by itself. A new SandboxEngine version makes the API refuse snapshots built
by the old version, until the next precompute. To avoid the gap, run
`docker compose exec scheduler gotime-api precompute --force` right after the update.

## Rotating keys

- **Partner API key:** create a new key (step 10), give it to the partner, and once they have switched,
  revoke the old one:
  ```
  docker run --rm -v /etc/gotime/keys:/run/gotime/keys gotime:latest gotime-api keys revoke <key_id>
  ```
  The API picks up the change within 5 s, with no restart. List keys with `... gotime-api keys list`.
- **Monitor key:** create a new one, put it in `gotime.env`, `docker compose up -d monitor`, then revoke
  the old one.
- **Source keys** (KNMI, Copernicus, OpenAQ): change them in `gotime.env`, then `docker compose up -d`. Also
  update the GitHub repository secrets with the same names, which the nightly source checks use.
- **Heartbeat URLs:** change them in `gotime.env`, then `docker compose up -d`. Treat them like secrets.

## What each alert means

| alert | meaning | first step |
|---|---|---|
| ingest `/fail`: `<source>\|<run> not complete by HH:MM` | the cycle was not published or not fully downloaded within its retry window | `docker compose logs scheduler`; check the source's status page; the API keeps serving the previous cycle (it becomes `degraded`, later `unavailable`) |
| ingest `/fail`: `<source>\|<run> quality: ...` | the data arrived but failed a check (values out of range, cycle incomplete, missing earlier cycles) | read the problems in the message; `docker compose exec scheduler gotime-ops quality <source>` |
| ingest ping missing (no `/fail`) | the scheduler is not running or is stuck | `docker compose ps`; `docker compose restart scheduler` |
| precompute `/fail` or missing | no new snapshot; the API serves the old one until it is too old | `docker compose exec scheduler gotime-api precompute --force` and read the error |
| monitor `/fail`: `/v1/ready 503 snapshot-unavailable` | no snapshot, or the newest is older than 18 h | see precompute; check disk space (`df -h`) |
| monitor `/fail`: `/v1/guidance 503 data-unavailable` | the served forecast is older than the freshness limit | see ingest |
| monitor `/fail`: `401` | the monitor key was revoked or mistyped | rotate the monitor key |
| monitor `/fail`: `request failed` or ping missing | the API or the monitor is down | `docker compose ps`, `docker compose logs api` |
| GitHub issue "Source check failing: X" | the nightly live check of source X failed (access, key, licence or format change) | read the issue; it closes itself when the source passes again |
| CI red on a pull request | lint, tests, contract, golden files, audit, Docker build or Newman failed | open the run; do not merge until green |

## Backups and restore

- **What:** `archive/` and `meta/` from the storage root, plus `climatology/`. `live/` and `raw/` are
  rebuilt from the sources; snapshots are rebuilt by precompute.
- **When:** daily at 03:45 UTC, incrementally, to `/srv/gotime/backup` (`GOTIME_BACKUP_TARGET`). Off-site
  later: set the target to `s3://bucket/prefix` and add `s3fs` to the dependencies.
- **Restore:**
  ```
  docker compose stop scheduler
  docker compose run --rm scheduler gotime-ops restore
  docker compose run --rm scheduler gotime-api precompute --force
  docker compose up -d
  ```
  `climatology/` is also in the image. Restore it to a folder with `gotime-ops restore --climatology-to <dir>`.
- **Roll back a snapshot:** write an older id (`ls /srv/gotime/data/serving/sandbox/`) into
  `/srv/gotime/data/serving/sandbox/CURRENT`. The API switches within 30 s.

## Logs and metrics

- All containers log JSON lines to stdout (`docker compose logs`). Each is limited to 5 × 20 MB per container.
- **API:**
  - One `request` line per request: request_id, key_id, route, status, problem code, latency_ms,
    location_id, distance_km, snapshot_id. Never coordinates, keys or IPs.
  - Every 60 s, a `metrics` line per worker: requests, by status class, 5xx count, p50/p95 latency, data
    age per source.
- **Monitor:** a `monitor ok` line with the guidance latency and data age per source.
- **Example:**
  ```
  docker compose logs api --no-log-prefix | grep '"msg": "metrics"' | tail -5
  ```
