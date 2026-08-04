"""Tests for the optional operator-maintained dbt inputs.

The important property is the second test: a rebuild must never destroy real
vendor costs, which are business data that exists nowhere else in the repo
(the file is git-ignored on purpose).
"""

from retailpulse.reference_inputs import REFERENCE_INPUTS, ensure_reference_inputs


def test_creates_header_only_files_when_absent(tmp_path):
    input_dir = tmp_path / "input"
    warehouse = tmp_path / "gold" / "warehouse.duckdb"

    outcomes = ensure_reference_inputs(input_dir, warehouse)

    assert set(outcomes.values()) == {"created"}
    for filename, header in REFERENCE_INPUTS:
        # Header only, no data rows: this is what "I have none of these" looks
        # like to dbt, and it must still be readable as zero typed rows.
        assert (input_dir / filename).read_text() == header + "\n"


def test_existing_files_are_never_overwritten(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    costs = input_dir / "vendor_costs.csv"
    real_data = "variation_id,item_name,category_name,vendor_name,unit_cost_cents\nV1,Gin,LIQUOR,Acme,1299\n"
    costs.write_text(real_data)

    outcomes = ensure_reference_inputs(input_dir, tmp_path / "gold" / "warehouse.duckdb")

    assert outcomes["vendor_costs.csv"] == "kept"
    assert costs.read_text() == real_data
    assert outcomes["category_overrides.csv"] == "created"


def test_creates_the_warehouse_directory(tmp_path):
    """dbt will not create its own output directory; a clean checkout must build."""
    warehouse = tmp_path / "gold" / "warehouse.duckdb"

    ensure_reference_inputs(tmp_path / "input", warehouse)

    assert warehouse.parent.is_dir()
