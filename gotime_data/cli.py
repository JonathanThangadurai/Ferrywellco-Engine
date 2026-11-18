"""Command line: gotime-data check | fetch <source> | ingest <source> | clean | read

check            one small request per source, OK / FAILED and why (no keys printed)
fetch <source>   download the newest data into raw/<source>/<batch>/
ingest <source>  turn every raw batch not yet ingested into rows in the stored table
clean            retention: raw > 2 days deleted, live > 10 days moved to the archive
read             print hourly rows for a location (for a quick look)
"""

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

import pandas as pd

from .config import raw_dir
from .sources import SOURCES


def ingest(name: str) -> int:
    """Run to_points + write for each raw batch without an .ingested marker. Returns batches ingested."""
    from .store import write_batch

    module = SOURCES[name]
    done = 0
    for batch in sorted((raw_dir() / module.NAME).glob("*")):
        if not batch.is_dir() or (batch / ".ingested").exists() or not (batch / "manifest.json").exists():
            continue
        result = module.to_points(batch)
        added = write_batch(result, f"{batch.name}")
        (batch / ".ingested").write_text(f"{datetime.now(UTC).isoformat()} {added} new rows\n")
        print(f"{name} {batch.name}: {len(result.hourly)} rows, {added} new")
        done += 1
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gotime-data", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    for cmd in ("fetch", "ingest"):
        sub.add_parser(cmd).add_argument("source", choices=list(SOURCES))
    sub.add_parser("clean")
    rd = sub.add_parser("read")
    rd.add_argument("location")
    rd.add_argument("--hours", type=int, default=48, help="hours from now (default 48)")
    rd.add_argument("--variables", nargs="*")
    rd.add_argument("--kind", default="forecast")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "check":
        from .check import main as check_main

        return check_main()
    if args.command == "fetch":
        print(SOURCES[args.source].fetch())
    elif args.command == "ingest":
        print(f"{ingest(args.source)} batch(es) ingested")
    elif args.command == "clean":
        from .store import clean

        print(clean())
    elif args.command == "read":
        from .read import get_hourly

        start = pd.Timestamp.now(tz="UTC").floor("h")
        df = get_hourly(args.location, start, start + timedelta(hours=args.hours), args.variables, args.kind)
        with pd.option_context("display.max_rows", 200, "display.width", 200):
            print(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
