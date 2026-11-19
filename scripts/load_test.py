"""Simple local load test for POST /v1/guidance (stage D2, item 9).

Sends requests with mixed locations, dates, activities and time ranges from a number of concurrent
clients, then reports p50/p95/p99 latency (client side, HTTP included) and requests per second.

    uv run python scripts/load_test.py --url http://127.0.0.1:8000 --key-file <file with one API key>

The key is read from a file (e.g. the new_key_<id>.txt written by `gotime-api keys create`) and never printed.
"""

import argparse
import asyncio
import random
import statistics
import time
from pathlib import Path

import httpx

POINTS = {
    "amsterdam": [(52.37, 4.90), (52.35, 4.92), (52.30, 4.80)],
    "chennai": [(13.08, 80.27), (13.05, 80.21)],
}
ACTIVITIES = ["walk", "run", "cycle", "outdoor_workout"]


def make_bodies(coverage: dict, n: int, seed: int = 1) -> list[dict]:
    rnd = random.Random(seed)
    bodies = []
    for _ in range(n):
        loc = rnd.choice(coverage["locations"])
        lat, lon = rnd.choice(POINTS[loc["aliases"][0]])
        start, minute = rnd.randint(5, 16), rnd.choice(["00", "15", "30", "45"])
        bodies.append(
            {
                "location": {"lat": lat, "lon": lon},
                "date": rnd.choice(loc["dates_available"][:-1] or loc["dates_available"]),
                "earliest_start": f"{start:02d}:{minute}",
                "latest_start": f"{start + rnd.randint(0, 5):02d}:{minute}",
                "activity_type": rnd.choice(ACTIVITIES),
                "duration_min": rnd.choice([20, 30, 45, 60, 90, 120]),
                "heat_adapted": rnd.choice(["yes", "no", "unknown"]),
                "hay_fever": rnd.choice(["yes", "no", "unknown"]),
            }
        )
    return bodies


async def worker(client: httpx.AsyncClient, queue: asyncio.Queue, latencies: list, statuses: dict) -> None:
    while True:
        try:
            body = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        t0 = time.perf_counter()
        r = await client.post("/v1/guidance", json=body)
        latencies.append((time.perf_counter() - t0) * 1000)
        statuses[r.status_code] = statuses.get(r.status_code, 0) + 1


async def main(url: str, key: str, n: int, concurrency: int, warmup: int) -> None:
    headers = {"Authorization": f"Bearer {key}"}
    limits = httpx.Limits(max_connections=concurrency)
    async with httpx.AsyncClient(base_url=url, headers=headers, limits=limits, timeout=10) as client:
        coverage = (await client.get("/v1/coverage")).raise_for_status().json()
        for body in make_bodies(coverage, warmup, seed=0):
            await client.post("/v1/guidance", json=body)
        queue: asyncio.Queue = asyncio.Queue()
        for body in make_bodies(coverage, n):
            queue.put_nowait(body)
        latencies: list[float] = []
        statuses: dict[int, int] = {}
        t0 = time.perf_counter()
        await asyncio.gather(*(worker(client, queue, latencies, statuses) for _ in range(concurrency)))
        wall = time.perf_counter() - t0
    q = statistics.quantiles(latencies, n=100)
    print(f"requests {len(latencies)}  concurrency {concurrency}  statuses {statuses}")
    print(f"latency ms  p50 {q[49]:.1f}  p95 {q[94]:.1f}  p99 {q[98]:.1f}  max {max(latencies):.1f}")
    print(f"throughput  {len(latencies) / wall:.0f} requests/s over {wall:.1f} s")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--key-file", type=Path, required=True)
    p.add_argument("--requests", type=int, default=5000)
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--warmup", type=int, default=200)
    a = p.parse_args()
    asyncio.run(main(a.url, a.key_file.read_text().strip(), a.requests, a.concurrency, a.warmup))
