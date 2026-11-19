"""Command line: gotime-api serve | precompute | keys create|revoke|list

serve        run the API (uvicorn); JSON logs to stdout
precompute   build a new serving snapshot when the forecast cycles changed (run after each ingest)
keys         manage partner API keys in the keys file (GOTIME_API_KEYS_FILE, outside the repo)

A new key is written once to a file readable by you only, next to the keys file; it is never printed.
Hand it to the partner over a secure channel and delete the file.
"""

import argparse
import os
import sys

from .settings import keys_file


def _keys(args) -> int:
    from .auth import create_key, read_keys, revoke_key

    path = keys_file()
    if args.action == "create":
        key_id, full = create_key(path, args.partner, args.mode, args.rate_per_minute)
        out = path.parent / f"new_key_{key_id}.txt"
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(full + "\n")
        print(f"created key_id {key_id} for {args.partner} ({args.mode}); the key is in {out} (mode 600)")
    elif args.action == "revoke":
        print("revoked" if revoke_key(path, args.key_id) else "no such active key")
    else:
        for k in read_keys(path).values():
            state = f"revoked {k.revoked_utc}" if k.revoked else "active"
            print(f"{k.key_id}  {k.partner:<16} {k.mode:<8} created {k.created_utc}  {state}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gotime-api", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--workers", type=int, default=1)
    pre = sub.add_parser("precompute")
    pre.add_argument("--engine", default="sandbox")
    pre.add_argument("--force", action="store_true", help="write a snapshot even if nothing changed")
    keys = sub.add_parser("keys")
    ksub = keys.add_subparsers(dest="action", required=True)
    create = ksub.add_parser("create")
    create.add_argument("--partner", required=True)
    create.add_argument("--mode", choices=["sandbox", "live"], default="sandbox")
    create.add_argument("--rate-per-minute", type=int)
    ksub.add_parser("revoke").add_argument("key_id")
    ksub.add_parser("list")
    args = parser.parse_args(argv)

    from .logs import setup_logging

    setup_logging()
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "gotime_api.app:serve_app",
            factory=True,
            host=args.host,
            port=args.port,
            workers=args.workers,
            access_log=False,
            log_config=None,
            server_header=False,
        )
    elif args.command == "precompute":
        from gotime_engine import ENGINES
        from gotime_engine.precompute import run

        sid = run(ENGINES[args.engine](), force=args.force)
        print(sid or "unchanged")
    else:
        return _keys(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
