"""Command line: gotime-ops scheduler | monitor | quality <source> | backup | restore | source-check <source>

scheduler            run the forecast schedule forever (fetch, ingest, quality, precompute; hourly
                     observations; daily clean and backup)
monitor [--every S]  synthetic check of the running API; pings HC_PING_MONITOR
quality <source>     run the data quality checks for the newest cycle of a forecast source
backup | restore     copy archive/, meta/ and climatology/ to or from GOTIME_BACKUP_TARGET
source-check <src>   live access and format check of one source (prints OK/FAILED and a reason)
"""

import argparse
import sys
from datetime import UTC, datetime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gotime-ops", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scheduler")
    mon = sub.add_parser("monitor")
    mon.add_argument("--url")
    mon.add_argument("--every", type=float, default=0, help="seconds between checks (0 = once)")
    sub.add_parser("quality").add_argument("source", choices=["ecmwf_ifs", "cams_eu", "cams_global"])
    sub.add_parser("backup")
    restore = sub.add_parser("restore")
    restore.add_argument("--climatology-to", help="also restore climatology/ into this folder")
    sub.add_parser("source-check").add_argument("source")
    args = parser.parse_args(argv)

    from gotime_api.logs import setup_logging

    setup_logging()
    if args.command == "scheduler":
        from .scheduler import run_forever

        run_forever()
    elif args.command == "monitor":
        from .monitor import run

        return run(args.url, args.every)
    elif args.command == "quality":
        import pandas as pd

        from gotime_data.store import read_cycles

        from .quality import run_checks

        c = read_cycles()
        runs = pd.to_datetime(c.loc[c["source"] == args.source, "issue_time_utc"], utc=True)
        if runs.empty:
            print(f"no cycles for {args.source}")
            return 1
        result = run_checks(args.source, runs.max().to_pydatetime(), datetime.now(UTC))
        print(result["status"], *result["problems"], sep="\n  ")
        return 0 if result["status"] == "ok" else 1
    elif args.command == "backup":
        from .backup import backup

        print(backup())
    elif args.command == "restore":
        from .backup import restore

        print(restore(args.climatology_to))
    elif args.command == "source-check":
        from .source_check import check

        ok, reason = check(args.source)
        print(f"{'OK' if ok else 'FAILED'} {args.source}: {reason}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
