"""Dagster orchestration for the RetailPulse pipeline.

Load the asset graph with:

    dagster dev -m retailpulse.orchestration.definitions

The orchestrator wraps the existing `retailpulse` CLI functions rather than
reimplementing them, so the pipeline remains runnable without Dagster.
"""
