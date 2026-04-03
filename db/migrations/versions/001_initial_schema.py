"""Initial schema — all tables

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa
import geoalchemy2

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Extensions
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS earthdistance CASCADE")

    # ------------------------------------------------------------------
    # 2. ibge_states
    # ------------------------------------------------------------------
    op.create_table(
        "ibge_states",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("region", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_ibge_states_uf", "ibge_states", ["uf"])

    # ------------------------------------------------------------------
    # 3. ibge_municipalities
    # ------------------------------------------------------------------
    op.create_table(
        "ibge_municipalities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("state_id", sa.Integer, sa.ForeignKey("ibge_states.id"), nullable=False),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lng", sa.Float, nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326),
            nullable=True,
        ),
        sa.Column("population", sa.Integer, nullable=True),
        sa.Column("area_km2", sa.Numeric(12, 4), nullable=True),
        sa.Column("idhm", sa.Numeric(6, 4), nullable=True),
        sa.Column("gdp_per_capita", sa.Numeric(14, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ibge_municipalities_geom", "ibge_municipalities", ["geom"], postgresql_using="gist")
    op.create_index("ix_ibge_municipalities_state_id", "ibge_municipalities", ["state_id"])

    # ------------------------------------------------------------------
    # 4. ibge_municipality_boundaries
    # ------------------------------------------------------------------
    op.create_table(
        "ibge_municipality_boundaries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("municipality_id", sa.Integer, sa.ForeignKey("ibge_municipalities.id"), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="MULTIPOLYGON", srid=4326),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_ibge_boundaries_municipality", "ibge_municipality_boundaries", ["municipality_id"])
    op.create_index("ix_ibge_boundaries_geom", "ibge_municipality_boundaries", ["geom"], postgresql_using="gist")

    # ------------------------------------------------------------------
    # 5. stations
    # ------------------------------------------------------------------
    op.create_table(
        "stations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("station_code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("operator", sa.String(100), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("city", sa.String(150), nullable=True),
        sa.Column("state_uf", sa.String(2), nullable=True),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lng", sa.Float, nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326),
            nullable=True,
        ),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("municipality_id", sa.Integer, sa.ForeignKey("ibge_municipalities.id"), nullable=True),
        sa.Column("raw_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_stations_station_code", "stations", ["station_code"])
    op.create_index("ix_stations_station_code", "stations", ["station_code"])
    op.create_index("ix_stations_geom", "stations", ["geom"], postgresql_using="gist")
    op.create_index("ix_stations_municipality_id", "stations", ["municipality_id"])

    # ------------------------------------------------------------------
    # 6. connectors
    # ------------------------------------------------------------------
    op.create_table(
        "connectors",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("station_id", sa.Integer, sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("connector_code", sa.String(64), nullable=False),
        sa.Column("plug_type", sa.String(50), nullable=True),
        sa.Column("power_kw", sa.Numeric(8, 2), nullable=True),
        sa.Column("is_fast", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("current_status", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_connectors_station_id", "connectors", ["station_id"])

    # ------------------------------------------------------------------
    # 7. status_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "status_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("station_id", sa.Integer, sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("connector_code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("session_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("energy_kwh", sa.Numeric(10, 3), nullable=True),
        sa.Column("session_minutes", sa.Integer, nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_status_snapshots_station_id", "status_snapshots", ["station_id"])
    op.create_index("ix_status_snapshots_captured_at", "status_snapshots", ["captured_at"])

    # ------------------------------------------------------------------
    # 8. senatran_fleet
    # ------------------------------------------------------------------
    op.create_table(
        "senatran_fleet",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("municipality_id", sa.Integer, nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("month", sa.Integer, nullable=True),
        sa.Column("fuel_type", sa.String(50), nullable=False),
        sa.Column("vehicle_type", sa.String(80), nullable=False),
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ev_count", sa.Integer, nullable=True),
        sa.Column("hybrid_count", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_senatran_fleet_municipality_id", "senatran_fleet", ["municipality_id"])

    # ------------------------------------------------------------------
    # 9. aneel_distributors
    # ------------------------------------------------------------------
    op.create_table(
        "aneel_distributors",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("state_uf", sa.String(2), nullable=True),
        sa.Column("concession_area", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_aneel_distributors_code", "aneel_distributors", ["code"])

    # ------------------------------------------------------------------
    # 10. aneel_tariffs
    # ------------------------------------------------------------------
    op.create_table(
        "aneel_tariffs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("distributor_id", sa.Integer, sa.ForeignKey("aneel_distributors.id"), nullable=False),
        sa.Column("tariff_group", sa.String(50), nullable=True),
        sa.Column("tariff_subgroup", sa.String(50), nullable=True),
        sa.Column("supply_type", sa.String(50), nullable=True),
        sa.Column("te_kwh", sa.Numeric(10, 6), nullable=True),
        sa.Column("tusd_kwh", sa.Numeric(10, 6), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_aneel_tariffs_distributor_id", "aneel_tariffs", ["distributor_id"])

    # ------------------------------------------------------------------
    # 11. aneel_outages
    # ------------------------------------------------------------------
    op.create_table(
        "aneel_outages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("distributor_id", sa.Integer, sa.ForeignKey("aneel_distributors.id"), nullable=False),
        sa.Column("municipality_id", sa.Integer, nullable=True),
        sa.Column("outage_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outage_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer, nullable=True),
        sa.Column("affected_consumers", sa.Integer, nullable=True),
        sa.Column("cause", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_aneel_outages_distributor_id", "aneel_outages", ["distributor_id"])

    # ------------------------------------------------------------------
    # 12. anp_gas_stations
    # ------------------------------------------------------------------
    op.create_table(
        "anp_gas_stations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("cnpj", sa.String(18), nullable=False),
        sa.Column("trade_name", sa.String(200), nullable=True),
        sa.Column("municipality_id", sa.Integer, nullable=True),
        sa.Column("state_uf", sa.String(2), nullable=True),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lng", sa.Float, nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326),
            nullable=True,
        ),
        sa.Column("has_ev_charger", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_anp_gas_stations_cnpj", "anp_gas_stations", ["cnpj"])
    op.create_index("ix_anp_gas_stations_cnpj", "anp_gas_stations", ["cnpj"])
    op.create_index("ix_anp_gas_stations_municipality_id", "anp_gas_stations", ["municipality_id"])
    op.create_index("ix_anp_gas_stations_geom", "anp_gas_stations", ["geom"], postgresql_using="gist")

    # ------------------------------------------------------------------
    # 13. anp_fuel_prices
    # ------------------------------------------------------------------
    op.create_table(
        "anp_fuel_prices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("gas_station_id", sa.Integer, sa.ForeignKey("anp_gas_stations.id"), nullable=False),
        sa.Column("fuel_type", sa.String(80), nullable=False),
        sa.Column("price_brl", sa.Numeric(10, 3), nullable=True),
        sa.Column("survey_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_anp_fuel_prices_gas_station_id", "anp_fuel_prices", ["gas_station_id"])

    # ------------------------------------------------------------------
    # 14. ipea_series
    # ------------------------------------------------------------------
    op.create_table(
        "ipea_series",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(300), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("frequency", sa.String(20), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_ipea_series_code", "ipea_series", ["code"])
    op.create_index("ix_ipea_series_code", "ipea_series", ["code"])

    # ------------------------------------------------------------------
    # 15. ipea_values
    # ------------------------------------------------------------------
    op.create_table(
        "ipea_values",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("series_id", sa.Integer, sa.ForeignKey("ipea_series.id"), nullable=False),
        sa.Column("municipality_id", sa.Integer, nullable=True),
        sa.Column("reference_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("value", sa.Numeric(18, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ipea_values_series_id", "ipea_values", ["series_id"])
    op.create_index("ix_ipea_values_municipality_id", "ipea_values", ["municipality_id"])

    # ------------------------------------------------------------------
    # 16. osm_pois
    # ------------------------------------------------------------------
    op.create_table(
        "osm_pois",
        sa.Column("osm_id", sa.BigInteger, primary_key=True),
        sa.Column("osm_type", sa.String(10), nullable=False),
        sa.Column("name", sa.String(300), nullable=True),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("amenity", sa.String(80), nullable=True),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lng", sa.Float, nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326),
            nullable=True,
        ),
        sa.Column("municipality_id", sa.Integer, sa.ForeignKey("ibge_municipalities.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_osm_pois_geom", "osm_pois", ["geom"], postgresql_using="gist")
    op.create_index("ix_osm_pois_category", "osm_pois", ["category"])

    # ------------------------------------------------------------------
    # 17. station_poi_distances
    # ------------------------------------------------------------------
    op.create_table(
        "station_poi_distances",
        sa.Column("station_id", sa.Integer, sa.ForeignKey("stations.id"), primary_key=True, nullable=False),
        sa.Column("category", sa.String(80), primary_key=True, nullable=False),
        sa.Column("nearest_poi_id", sa.BigInteger, sa.ForeignKey("osm_pois.osm_id"), nullable=True),
        sa.Column("distance_m", sa.Numeric(12, 2), nullable=True),
    )
    op.create_index("ix_station_poi_distances_station_id", "station_poi_distances", ["station_id"])


def downgrade() -> None:
    op.drop_table("station_poi_distances")
    op.drop_table("osm_pois")
    op.drop_table("ipea_values")
    op.drop_table("ipea_series")
    op.drop_table("anp_fuel_prices")
    op.drop_table("anp_gas_stations")
    op.drop_table("aneel_outages")
    op.drop_table("aneel_tariffs")
    op.drop_table("aneel_distributors")
    op.drop_table("senatran_fleet")
    op.drop_table("status_snapshots")
    op.drop_table("connectors")
    op.drop_table("stations")
    op.drop_table("ibge_municipality_boundaries")
    op.drop_table("ibge_municipalities")
    op.drop_table("ibge_states")
