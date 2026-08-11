"""Public demo entrypoint — Streamlit Community Cloud runs this file.

The dashboard itself (`dashboard/app.py`) expects a built warehouse to already
exist, because locally `make dbt-build` puts one there. Streamlit Cloud has no
build step: it clones the repo, installs requirements and runs one script. So
this wrapper does the build first, then hands over to the real app unchanged.

**This demo never touches the store's data, and cannot.** It runs the actual
pipeline — synthetic Bronze -> Silver transform -> the same 33 dbt models and
93 tests — against `scripts/generate_synthetic_bronze.py`, a deterministic
fixture describing a fictional "Synthetic Test Store". No Square credentials
exist in this environment, nothing reads `data/`, and every figure on screen is
computed from generated numbers. That is also why the demo is a fair sample of
the engineering and a useless sample of the business.

Everything is built into a temp directory, so a read-only or ephemeral
filesystem is fine, and `st.cache_resource` means the ~15s build happens once
per container rather than once per page view.
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent.resolve()

# 400 days rather than the 42 CI uses: the dashboard offers Last 7/30/90/365
# and All time, and the revenue forecast fits on 8 complete weeks. A short
# fixture leaves most of those windows empty, which reads as a broken app
# rather than a small one.
DEMO_DAYS = 400

# Deliberately duplicated from design.apply(), which is where page setup
# normally lives, and deliberately the first Streamlit call in the file.
# set_page_config must precede every other Streamlit command, and this wrapper
# renders a progress panel before dashboard/app.py ever reaches design.apply().
# Without this line that later call becomes the first one *after* output has
# been written, which is the case Streamlit rejects. Keep the arguments in step
# with design.apply().
st.set_page_config(
    page_title="RetailPulse",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource(show_spinner=False)
def build_demo_warehouse() -> str:
    """Run the real pipeline over a synthetic fixture. Returns the warehouse path."""
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "scripts"))

    workdir = Path(tempfile.mkdtemp(prefix="retailpulse-demo-"))
    bronze = workdir / "bronze"
    silver = workdir / "silver"
    inputs = workdir / "input"
    warehouse = workdir / "warehouse.duckdb"

    # dbt's sources and profile resolve these at parse time, so they must be
    # set before dbt is invoked — and before dashboard/app.py is imported,
    # since it reads the warehouse path at module level.
    os.environ["RETAILPULSE_SILVER_DIR"] = str(silver)
    os.environ["RETAILPULSE_INPUT_DIR"] = str(inputs)
    os.environ["RETAILPULSE_WAREHOUSE_PATH"] = str(warehouse)
    # Tells the dashboard to describe its numbers as synthetic. It changes
    # nothing about how any of them are computed.
    os.environ["RETAILPULSE_DEMO"] = "1"

    # Held in a placeholder so the whole progress panel can be removed once the
    # build finishes. `st.status(state="complete")` only collapses it, which
    # leaves a stale disclosure widget above the dashboard header for the rest
    # of the session.
    progress = st.empty()

    with progress.container():
        with st.status("Building the demo warehouse…", expanded=True) as status:
            st.write("Generating a synthetic Bronze fixture…")
            import generate_synthetic_bronze as synth

            sys.argv = ["generate_synthetic_bronze", str(bronze), "--days", str(DEMO_DAYS)]
            synth.main()

            st.write("Running the Bronze → Silver transform…")
            from retailpulse.transform.silver import run_silver_transform

            run_silver_transform(bronze, silver)

            st.write("Creating optional reference inputs…")
            from retailpulse.reference_inputs import ensure_reference_inputs

            ensure_reference_inputs(input_dir=inputs, warehouse_path=warehouse)

            st.write("Building the dbt models and running the tests…")
            # A subprocess, not dbtRunner in-process. dbt-duckdb keeps its
            # read-write connection to the file open after the invocation
            # returns, and DuckDB refuses a second connection to the same
            # database with a different configuration — so the dashboard's
            # read-only connect then fails with "Can't open a connection to
            # same database file with a different configuration". Letting the
            # process exit closes the handle and flushes the WAL, which is what
            # makes the file safely readable.
            completed = subprocess.run(
                [
                    sys.executable, "-m", "dbt.cli.main", "build",
                    "--project-dir", str(ROOT / "dbt"),
                    "--profiles-dir", str(ROOT / "dbt"),
                    "--target", "dev",
                ],
                capture_output=True,
                text=True,
                env=os.environ.copy(),
                check=False,
            )
            if completed.returncode != 0:
                # Leave the panel on screen in this case — it is the only place
                # the failure is visible on a hosted deploy.
                status.update(label="Demo build failed", state="error")
                st.code((completed.stdout or "")[-3000:] or completed.stderr[-3000:])
                raise RuntimeError("dbt build failed while preparing the demo warehouse")

    progress.empty()
    return str(warehouse)


warehouse_path = build_demo_warehouse()
os.environ["RETAILPULSE_WAREHOUSE_PATH"] = warehouse_path

# Hand over to the real dashboard, unmodified. `dashboard/` goes on the path
# because app.py imports its sibling `design` module by bare name, the way
# Streamlit would if it were the entrypoint.
sys.path.insert(0, str(ROOT / "dashboard"))
runpy.run_path(str(ROOT / "dashboard" / "app.py"), run_name="__main__")
