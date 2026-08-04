"""Resources shared by the Dagster assets: filesystem layout, Square, dbt.

Two things matter here beyond wiring.

First, the production guard. `retailpulse extract-all` refuses to contact a
production Square account unless RETAILPULSE_ALLOW_PRODUCTION is set for that
run. An orchestrator is exactly the kind of new entrypoint that quietly routes
around a safety check like that, so the same environment variable is enforced
here, imported from the CLI rather than re-spelled, and raises a Dagster
`Failure` (which the run surfaces) instead of `SystemExit` (which it would not).

Second, paths. dbt's profiles.yml resolves RETAILPULSE_SILVER_DIR,
RETAILPULSE_INPUT_DIR and RETAILPULSE_WAREHOUSE_PATH from the environment. The
Makefile sets them; a `dagster dev` process launched from anywhere may not
have, so they are defaulted from the project root and exported before dbt runs.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from dagster import Failure
from dagster_dbt import DbtProject

from retailpulse.cli import PRODUCTION_OPT_IN_ENV
from retailpulse.config import Settings
from retailpulse.square_client import SquareClient

# src/retailpulse/orchestration/resources.py -> repository root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

DBT_PROJECT_DIR = PROJECT_ROOT / "dbt"


@dataclass(frozen=True)
class Paths:
    """Where each layer lives on disk, matching the Makefile's DBT_ENV block."""

    bronze: Path
    silver: Path
    input: Path
    warehouse: Path

    @classmethod
    def from_env(cls) -> "Paths":
        data = PROJECT_ROOT / "data"
        return cls(
            bronze=Path(os.environ.get("RAW_DATA_DIR", data / "bronze")),
            silver=Path(os.environ.get("RETAILPULSE_SILVER_DIR", data / "silver")),
            input=Path(os.environ.get("RETAILPULSE_INPUT_DIR", data / "input")),
            warehouse=Path(
                os.environ.get("RETAILPULSE_WAREHOUSE_PATH", data / "gold" / "warehouse.duckdb")
            ),
        )

    def export_for_dbt(self) -> None:
        """Publish the paths dbt's profiles.yml and sources read from env_var()."""
        os.environ.setdefault("RETAILPULSE_SILVER_DIR", str(self.silver))
        os.environ.setdefault("RETAILPULSE_INPUT_DIR", str(self.input))
        os.environ.setdefault("RETAILPULSE_WAREHOUSE_PATH", str(self.warehouse))


paths = Paths.from_env()
paths.export_for_dbt()


def production_is_allowed() -> bool:
    return os.environ.get(PRODUCTION_OPT_IN_ENV, "").strip().lower() in {"1", "true", "yes"}


@contextmanager
def square_client() -> Iterator[SquareClient]:
    """Yield an authenticated Square client, refusing un-opted-in production runs.

    All RetailPulse Square operations are read-only; this is a blast-radius
    guard, not an integrity one. It exists so that scheduling the pipeline can
    never turn into "the daemon started hammering the real store overnight
    because a token was left in .env".
    """

    settings = Settings()
    if not settings.has_token:
        raise Failure(
            "SQUARE_ACCESS_TOKEN is not set. The Square extraction assets need a token; "
            "the Silver and dbt assets do not. Copy .env.example to .env to run ingestion."
        )
    if settings.is_production and not production_is_allowed():
        raise Failure(
            "Refusing to run against PRODUCTION Square. "
            f"Set {PRODUCTION_OPT_IN_ENV}=1 in the Dagster process environment to opt in "
            "deliberately. All operations are read-only regardless."
        )
    with SquareClient(settings) as client:
        yield client


def _ensure_interpreter_bin_on_path() -> None:
    """Put this interpreter's bin directory on PATH.

    `DbtProject.prepare_if_dev()` shells out to dbt through a `DbtCliResource`
    it constructs itself, with the default executable name — so unlike our own
    resource, it cannot be told where dbt lives and simply needs PATH to be
    right. Launching `.venv/bin/dagster` without sourcing `activate` leaves it
    wrong, and the resulting error names `dbt_executable` rather than PATH,
    which sends you looking in the wrong place.
    """
    bin_dir = str(Path(sys.executable).parent)
    current = os.environ.get("PATH", "")
    if bin_dir not in current.split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join([bin_dir, current]) if current else bin_dir


def dbt_executable() -> str:
    """Locate dbt, preferring the one installed alongside this interpreter.

    `DbtCliResource` defaults to whatever `dbt` PATH resolves to, which is
    empty unless the venv happens to be activated. Since the whole point of an
    orchestrator is running unattended — under a daemon, a systemd unit, a
    container entrypoint — resolving it relative to `sys.executable` makes the
    project work without anyone having sourced an activate script.
    """
    candidate = Path(sys.executable).parent / "dbt"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("dbt")
    if found:
        return found
    raise Failure(
        "No dbt executable found next to the running interpreter or on PATH. "
        "Install the project's dev extra: pip install -e '.[dev,orchestration]'"
    )


# `prepare_if_dev()` regenerates the dbt manifest when running under
# `dagster dev`, so editing a model shows up in the asset graph without a
# manual `dbt parse`. In a deployment the manifest is built ahead of time.
dbt_project = DbtProject(
    project_dir=DBT_PROJECT_DIR,
    profiles_dir=DBT_PROJECT_DIR,
)
_ensure_interpreter_bin_on_path()
dbt_project.prepare_if_dev()
