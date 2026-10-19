"""Meridian Commerce Pvt. Ltd. - a fictional demonstration organization.

    python -m app.scripts.seed_meridian seed                 # create / complete (idempotent)
    python -m app.scripts.seed_meridian users                # give the demo people logins (local only)
    python -m app.scripts.seed_meridian status               # what exists right now
    python -m app.scripts.seed_meridian export-sample FILE   # the statement-2 CSV for the import exercise

Everything is synthetic. The seed never deletes or modifies any other organization, and
never resets anything: ledger history is append-only. Re-running `seed` is a no-op.

`users` reads the password from the MERIDIAN_DEMO_PASSWORD environment variable, or asks for it
(hidden) when run in a terminal. It is never printed or logged. It refuses to run when
ENVIRONMENT=production.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import date
from pathlib import Path

from app.demo import meridian_scenario as scn
from app.demo import meridian_seed as seeder


def _cmd_seed(args: argparse.Namespace) -> int:
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    report = seeder.seed(as_of)
    print(f"created: {json.dumps(report.created)}")
    print(f"already present (skipped): {json.dumps(report.skipped)}")
    print("Next: `python -m app.scripts.seed_meridian users` to create logins, then sign in.")
    return 0


def _cmd_users(args: argparse.Namespace) -> int:
    password = os.environ.get("MERIDIAN_DEMO_PASSWORD", "")
    if not password:
        if not sys.stdin.isatty():
            print(
                "Set MERIDIAN_DEMO_PASSWORD, or run this in a terminal to be prompted.",
                file=sys.stderr,
            )
            return 2
        password = getpass.getpass("Password for the demo logins (local use only, min 10 chars): ")
        if getpass.getpass("Repeat: ") != password:
            print("The passwords do not match.", file=sys.stderr)
            return 2
    only = [p.strip() for p in args.only.split(",")] if args.only else None
    result = seeder.configure_users(password, only)
    for key, outcome in result.items():
        persona = scn.PERSONAS[key]
        print(f"{persona.email:42s} {persona.role:11s} {outcome}")
    print("These logins are for LOCAL demonstration only. Do not deploy them publicly.")
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    info = seeder.status()
    if info is None:
        print("The Meridian demo organization has not been created. Run: seed")
        return 1
    print(json.dumps(info, indent=2))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    org = seeder.find_organization()
    as_of = seeder.recorded_as_of(org.id) if org else None
    if args.as_of:
        as_of = date.fromisoformat(args.as_of)
    if as_of is None:
        print("No seeded organization found; pass --as-of YYYY-MM-DD.", file=sys.stderr)
        return 2
    sc = scn.build_scenario(as_of)
    Path(args.file).write_text(scn.statement_csv(sc.s2()), encoding="utf-8", newline="\n")
    print(f"wrote {len(sc.s2())} statement-2 records to {args.file}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("seed", help="create or complete the organization (idempotent)")
    s.add_argument("--as-of", help="anchor date YYYY-MM-DD (first run only; default today)")
    s.set_defaults(fn=_cmd_seed)
    u = sub.add_parser("users", help="activate the demo logins (local only)")
    u.add_argument("--only", help="comma-separated persona keys: owner,maker,checker,ops,auditor")
    u.set_defaults(fn=_cmd_users)
    sub.add_parser("status", help="show what exists").set_defaults(fn=_cmd_status)
    e = sub.add_parser("export-sample", help="write the statement-2 CSV")
    e.add_argument("file")
    e.add_argument("--as-of")
    e.set_defaults(fn=_cmd_export)
    args = parser.parse_args()
    try:
        return args.fn(args)
    except seeder.SeedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
