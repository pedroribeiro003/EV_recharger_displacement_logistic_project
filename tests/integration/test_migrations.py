"""Integration tests: verify migrations and schema integrity."""
import pytest
from sqlalchemy import inspect


EXPECTED_TABLES = {
    "stations",
    "connectors",
    "status_snapshots",
    "ibge_states",
    "ibge_municipalities",
    "anp_gas_stations",
    "aneel_tariffs",
    "senatran_fleet",
    "ipea_series",
    "ipea_values",
    "osm_pois",
}


def test_expected_tables_exist(db_engine):
    """All domain tables must be present after running migrations."""
    inspector = inspect(db_engine)
    existing = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - existing
    assert not missing, f"Missing tables after migration: {missing}"
