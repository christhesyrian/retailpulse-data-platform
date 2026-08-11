import argparse
import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

from pydantic import ValidationError

from retailpulse.config import Settings
from retailpulse.extract.jobs import (
    extract_catalog,
    extract_inventory,
    extract_locations,
    extract_orders,
    extract_payments,
    utc_window,
)
from retailpulse.square_client import SquareAPIError, SquareClient
from retailpulse.transform.silver import run_silver_transform

# A year of history. This is the ceiling on a single --days window, not a
# default: the guard exists so a typo can't kick off an unexpectedly large
# extraction, and 365 is what the dashboard's longest period needs to be
# meaningful (it also lets a year-over-year comparison exist at all).
MAX_EXTRACT_DAYS = 365


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retailpulse")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Verify Square authentication and list locations.")
    subparsers.add_parser("doctor", help="Run local environment and configuration diagnostics.")

    extract_all = subparsers.add_parser(
        "extract-all", help="Extract locations, catalog, orders, and payments."
    )
    extract_all.add_argument(
        "--days",
        type=int,
        default=7,
        help=f"Lookback window in days (1-{MAX_EXTRACT_DAYS}).",
    )

    subparsers.add_parser(
        "transform-silver", help="Normalize Bronze JSON into deduplicated Silver CSV tables."
    )
    return parser


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        print("Configuration error. Copy .env.example to .env and add your token.", file=sys.stderr)
        raise SystemExit(2) from exc


def validate_days(days: int) -> None:
    if days <= 0:
        print(f"--days must be a positive integer (got {days}).", file=sys.stderr)
        raise SystemExit(2)
    if days > MAX_EXTRACT_DAYS:
        print(
            f"--days={days} exceeds the safety maximum of {MAX_EXTRACT_DAYS}. "
            "Use a smaller window to avoid an unexpectedly large extraction.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def run_doctor() -> int:
    """Report diagnostic status without ever revealing secret values."""
    ok = True

    print(f"Python: {sys.version.split()[0]}")

    for package in ("httpx", "pydantic", "pydantic_settings"):
        found = importlib.util.find_spec(package) is not None
        print(f"Package '{package}': {'found' if found else 'MISSING'}")
        ok = ok and found

    try:
        settings = load_settings()
    except SystemExit:
        print("Configuration: FAILED to load (see message above)")
        return 1

    print(f"Square environment: {settings.square_environment}")
    if settings.is_production:
        print("WARNING: environment is set to PRODUCTION.")

    print(f"Square token configured: {settings.has_token}")
    # A missing token is not a doctor failure — local steps (transform-silver,
    # dbt, dashboard) don't need one. It's only required to contact Square.

    raw_dir = settings.raw_data_dir
    try:
        raw_dir.mkdir(parents=True, exist_ok=True)
        probe = raw_dir / ".doctor-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    print(f"Raw data directory '{raw_dir}' writable: {writable}")
    ok = ok and writable

    env_ignored = _git_ignores(".env")
    print(f".env covered by .gitignore: {env_ignored}")
    ok = ok and env_ignored

    return 0 if ok else 1


PRODUCTION_OPT_IN_ENV = "RETAILPULSE_ALLOW_PRODUCTION"


def require_production_opt_in() -> None:
    """Refuse to contact production unless the user has explicitly opted in.

    Having a production token in .env is not enough — the operator must also
    set RETAILPULSE_ALLOW_PRODUCTION=1 for that run. This makes hitting the
    real store a deliberate act, never an accident from a leftover env value.
    All operations remain read-only regardless; this is a blast-radius guard.
    """
    allowed = os.environ.get(PRODUCTION_OPT_IN_ENV, "").strip().lower() in {"1", "true", "yes"}
    if not allowed:
        print(
            "REFUSING to run against PRODUCTION.\n"
            "SQUARE_ENVIRONMENT=production, but this run is not opted in.\n"
            f"All RetailPulse operations are read-only, but to proceed deliberately set "
            f"{PRODUCTION_OPT_IN_ENV}=1 for this command, e.g.:\n"
            f"    {PRODUCTION_OPT_IN_ENV}=1 retailpulse extract-all --days 7",
            file=sys.stderr,
        )
        raise SystemExit(3)
    print(
        "NOTE: running against PRODUCTION (read-only). Raw output stays local and Git-ignored.",
        file=sys.stderr,
    )


def _git_ignores(path: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=os.getcwd(),
            capture_output=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "doctor":
        raise SystemExit(run_doctor())

    settings = load_settings()

    if args.command == "transform-silver":
        # RETAILPULSE_SILVER_DIR is what dbt's sources resolve, so the writer
        # has to honour it too or the two ends of Silver drift apart. It also
        # carries the s3:// form, which has no sibling-of-Bronze default.
        silver_root: str | Path = os.environ.get(
            "RETAILPULSE_SILVER_DIR", ""
        ) or settings.raw_data_dir.parent / "silver"
        counts = run_silver_transform(settings.raw_data_dir, silver_root)
        print("Silver transform complete: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        return

    if settings.is_production:
        require_production_opt_in()

    try:
        with SquareClient(settings) as client:
            if args.command == "check":
                payload = client.list_locations()
                locations = payload.get("locations", [])
                print(f"Connected to Square ({settings.square_environment}).")
                print(f"Active/returned locations: {len(locations)}")
                for location in locations:
                    print(f"- {location.get('name', 'Unnamed')} [{location.get('id')}] ")
                return

            validate_days(args.days)
            run_id = uuid.uuid4().hex
            begin_time, end_time = utc_window(args.days)
            environment = settings.square_environment

            locations = extract_locations(client, settings.raw_data_dir, run_id, environment)
            if not locations:
                raise RuntimeError("No active Square locations were returned.")

            catalog_pages = extract_catalog(client, settings.raw_data_dir, run_id, environment)
            order_pages = extract_orders(
                client, settings.raw_data_dir, locations, begin_time, end_time, run_id, environment
            )
            payment_pages = extract_payments(
                client, settings.raw_data_dir, begin_time, end_time, run_id, environment
            )
            inventory_pages = extract_inventory(
                client, settings.raw_data_dir, locations, run_id, environment
            )
            print(
                "Extraction complete: "
                f"run_id={run_id}, locations={len(locations)}, catalog_pages={catalog_pages}, "
                f"order_pages={order_pages}, payment_pages={payment_pages}, "
                f"inventory_pages={inventory_pages}"
            )
    except (SquareAPIError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
