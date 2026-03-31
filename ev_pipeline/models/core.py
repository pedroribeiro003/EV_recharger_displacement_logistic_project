"""
models/core.py
Models SQLAlchemy para o schema CORE.
Entidades limpas e normalizadas do domínio.
Requer a extensão PostGIS instalada no PostgreSQL.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.settings import get_settings
from models.base import Base, TimestampMixin

# PostGIS — importado condicionalmente para não quebrar em ambientes sem GeoAlchemy2
try:
    from geoalchemy2 import Geometry
    HAS_POSTGIS = True
except ImportError:
    HAS_POSTGIS = False
    Geometry = None  # type: ignore

settings = get_settings()
SCHEMA = settings.db_schema_core


class Municipality(Base, TimestampMixin):
    """Municípios brasileiros — fonte IBGE + OSM."""

    __tablename__ = "municipalities"
    __table_args__ = (
        UniqueConstraint("ibge_code", name="uq_core_mun_ibge_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ibge_code: Mapped[str] = mapped_column(String(7), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    state_code: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    region: Mapped[str | None] = mapped_column(
        String(32),
        comment="Sudeste | Sul | Nordeste | Norte | Centro-Oeste",
    )
    population: Mapped[int | None] = mapped_column(BigInteger)
    area_km2: Mapped[float | None] = mapped_column(Numeric(12, 3))
    gdp_per_capita: Mapped[float | None] = mapped_column(Numeric(12, 2))
    urban_pop_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))

    # Relacionamentos
    vehicle_snapshots: Mapped[list["VehicleFleetSnapshot"]] = relationship(
        back_populates="municipality"
    )
    charging_stations: Mapped[list["ChargingStation"]] = relationship(
        back_populates="municipality"
    )
    energy_readings: Mapped[list["EnergyReading"]] = relationship(
        back_populates="municipality"
    )

    def __repr__(self) -> str:
        return f"<Municipality {self.ibge_code} {self.name}/{self.state_code}>"


class VehicleFleetSnapshot(Base, TimestampMixin):
    """
    Frota de EVs por município e mês — fonte SENATRAN.
    Série temporal mensal.
    """

    __tablename__ = "vehicle_fleet_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "municipality_id", "reference_month",
            name="uq_core_fleet_mun_month",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    municipality_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.municipalities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    reference_month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total_ev: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_phev: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_hev: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_vehicles: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    ev_penetration_pct: Mapped[float | None] = mapped_column(
        Numeric(6, 4),
        comment="% EVs no total — calculado na ingestão",
    )
    yoy_ev_growth_pct: Mapped[float | None] = mapped_column(
        Numeric(6, 4),
        comment="Crescimento anual YoY — calculado no ETL",
    )

    municipality: Mapped["Municipality"] = relationship(back_populates="vehicle_snapshots")

    def __repr__(self) -> str:
        return (
            f"<VehicleFleetSnapshot mun_id={self.municipality_id} "
            f"month={self.reference_month} ev={self.total_ev}>"
        )


class ChargingStation(Base, TimestampMixin):
    """
    Estações de carregamento — fonte Open Charge Map + operadores.
    Campo `location` requer PostGIS.
    """

    __tablename__ = "charging_stations"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_core_station_source_ext"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    municipality_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.municipalities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    max_power_kw: Mapped[float | None] = mapped_column(Numeric(8, 2))
    num_connectors: Mapped[int | None] = mapped_column(SmallInteger)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_operational: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    operational_since: Mapped[date | None] = mapped_column(Date)
    grid_connection_kva: Mapped[float | None] = mapped_column(Numeric(10, 2))

    municipality: Mapped["Municipality | None"] = relationship(
        back_populates="charging_stations"
    )
    sessions: Mapped[list["ChargingSession"]] = relationship(
        back_populates="station"
    )

    def __repr__(self) -> str:
        return f"<ChargingStation {self.external_id} @ {self.name}>"


class ChargingSession(Base, TimestampMixin):
    """
    Sessões de carregamento — fonte ACN-Data / operadores.
    Particionada por started_at (configurar no DDL).
    """

    __tablename__ = "charging_sessions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.charging_stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    energy_kwh: Mapped[float | None] = mapped_column(Numeric(10, 3))
    peak_power_kw: Mapped[float | None] = mapped_column(Numeric(8, 2))
    avg_power_kw: Mapped[float | None] = mapped_column(Numeric(8, 2))
    vehicle_type: Mapped[str | None] = mapped_column(
        String(16),
        comment="BEV | PHEV | HEV",
    )
    connector_type: Mapped[str | None] = mapped_column(
        String(32),
        comment="CCS | CHAdeMO | Type2 | AC",
    )
    # Features temporais pré-computadas (evitam cálculo em query)
    hour_of_day: Mapped[int | None] = mapped_column(SmallInteger, index=True)
    day_of_week: Mapped[int | None] = mapped_column(SmallInteger, index=True)
    is_weekend: Mapped[bool | None] = mapped_column(Boolean)
    month_of_year: Mapped[int | None] = mapped_column(SmallInteger)

    station: Mapped["ChargingStation"] = relationship(back_populates="sessions")

    def __repr__(self) -> str:
        return (
            f"<ChargingSession station_id={self.station_id} "
            f"start={self.started_at} kwh={self.energy_kwh}>"
        )


class EnergyReading(Base, TimestampMixin):
    """
    Leituras da rede elétrica — fonte ONS / ANEEL.
    Particionada por read_at (configurar no DDL).
    """

    __tablename__ = "energy_readings"
    __table_args__ = (
        UniqueConstraint(
            "source", "region_code", "read_at",
            name="uq_core_energy_source_region_ts",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    municipality_id: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.municipalities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    load_mw: Mapped[float | None] = mapped_column(Numeric(10, 3))
    available_capacity_mw: Mapped[float | None] = mapped_column(Numeric(10, 3))
    voltage_kv: Mapped[float | None] = mapped_column(Numeric(8, 2))
    frequency_hz: Mapped[float | None] = mapped_column(Numeric(6, 3))
    load_pct: Mapped[float | None] = mapped_column(
        Numeric(5, 2),
        comment="% de utilização — calculado na ingestão",
    )

    municipality: Mapped["Municipality | None"] = relationship(
        back_populates="energy_readings"
    )

    def __repr__(self) -> str:
        return (
            f"<EnergyReading source={self.source} "
            f"region={self.region_code} at={self.read_at}>"
        )
